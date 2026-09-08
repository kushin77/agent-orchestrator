"""Agent Identity + Registry service facade (issue #10).

The multi-tenant replacement for the fleet's ad-hoc registries: registers
tenant-scoped agents from frozen AgentProfiles (issue #9), drives their
lifecycle (register / activate / pause / retire), routes task types to agents
by capability (fail closed) and issues per-agent session credentials with
tenant-scoped claims. Every mutating operation appends to the append-only
audit log in ``registry/events``.

Everything is tenant-scoped: an agent, route or capability lookup in one tenant
never observes another tenant's rows and never falls back across a tenant
boundary.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional, Tuple

from .catalog import ClosedCatalog, load_catalog
from .errors import AgentAlreadyRegisteredError, RegistryServiceError
from .identity import AgentSession, IdentityService
from .model import (
    STATUS_ACTIVE,
    STATUS_PAUSED,
    STATUS_REGISTERED,
    STATUS_RETIRED,
    Agent,
    agent_records,
    now_utc,
    require_transition,
)
from .routing import RouteResolution, TaskRoute, TaskRouter
from .store import RegistryStore


class AgentRegistry:
    """Facade over store + lifecycle + routing + identity issuance.

    All lifecycle methods append an audit event to ``self.events`` (an
    append-only hash-chained log from ``registry/events``).
    """

    def __init__(
        self,
        *,
        event_log: Optional[Any] = None,
        signing_key: Optional[bytes] = None,
    ) -> None:
        # Lazy import keeps this facade importable even before the events
        # subtree is on sys.path; the log is created on first registry use.
        if event_log is None:
            from events import EventLog

            event_log = EventLog()
        self._catalog = load_catalog()
        self.store = RegistryStore()
        self.events = event_log
        self._signing_key = signing_key
        self._router: Optional[TaskRouter] = None
        self._identity: Optional[IdentityService] = None

    @property
    def catalog(self) -> ClosedCatalog:
        """The closed platform vocabulary this service validates against."""
        return self._catalog

    def router(self) -> TaskRouter:
        """The task-route / capability resolver for this registry."""
        if self._router is None:
            self._router = TaskRouter(self.store, events=self.events)
        return self._router

    def identity(self) -> IdentityService:
        """The scoped-claims identity issuer for this registry."""
        if self._identity is None:
            self._identity = IdentityService(
                self.store, events=self.events, signing_key=self._signing_key
            )
        return self._identity

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def register(
        self,
        tenant_id: str,
        agent_id: str,
        profile_ref: str,
        *,
        role: Optional[str] = None,
        actor: Optional[str] = None,
        ts: Optional[str] = None,
    ) -> Agent:
        """Register a new agent of ``profile_ref`` (a #9 profile id) in a tenant.

        The agent inherits its capabilities, tools, owner and tier from the
        immutable profile seed; an unknown profile, capability or tool is
        refused (fail closed). The agent starts in ``registered`` (not yet
        dispatchable).
        """
        from .catalog import resolve_profile

        if self.store.has_agent(tenant_id, agent_id):
            raise AgentAlreadyRegisteredError(
                f"agent {agent_id!r} is already registered in tenant {tenant_id!r}"
            )
        profile = resolve_profile(profile_ref)
        ts = ts or now_utc()
        agent = Agent.from_profile(
            tenant_id=tenant_id,
            agent_id=agent_id,
            profile_ref=profile.id,
            profile_version=profile.version,
            owner=profile.owner,
            capability_set=profile.capability_set,
            tool_allowlist=profile.tool_allowlist,
            model_tier=profile.model_tier,
            role=role,
            registered_at=ts,
        )
        self.store.put_agent(agent)
        self.events.append(
            "register",
            ts=ts,
            status=STATUS_REGISTERED,
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor=actor,
            detail={"profileRef": profile.id, "profileVersion": profile.version},
        )
        return agent

    def _transition(
        self, tenant_id: str, agent_id: str, action: str, target: str, actor: Optional[str]
    ) -> Agent:
        agent = self.store.require_agent(tenant_id, agent_id)
        require_transition(agent.status, action)
        updated = dataclasses.replace(agent, status=target)
        self.store.put_agent(updated)
        ts = now_utc()
        self.events.append(
            action,
            ts=ts,
            status=target,
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor=actor,
        )
        return updated

    def activate(self, tenant_id: str, agent_id: str, *, actor: Optional[str] = None) -> Agent:
        """Activate a registered/paused agent so it becomes dispatchable."""
        return self._transition(tenant_id, agent_id, "activate", STATUS_ACTIVE, actor)

    def pause(self, tenant_id: str, agent_id: str, *, actor: Optional[str] = None) -> Agent:
        """Pause an active agent (no new work; existing sessions keep claims)."""
        return self._transition(tenant_id, agent_id, "pause", STATUS_PAUSED, actor)

    def retire(self, tenant_id: str, agent_id: str, *, actor: Optional[str] = None) -> Agent:
        """Retire an agent (terminal). A retired agent is never re-activated."""
        return self._transition(tenant_id, agent_id, "retire", STATUS_RETIRED, actor)

    def touch(
        self, tenant_id: str, agent_id: str, *, ts: Optional[str] = None
    ) -> Agent:
        """Record a heartbeat (updates ``lastSeen``); no lifecycle event."""
        agent = self.store.require_agent(tenant_id, agent_id)
        updated = dataclasses.replace(agent, last_seen=ts or now_utc())
        self.store.put_agent(updated)
        return updated

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def get(self, tenant_id: str, agent_id: str) -> Agent:
        """Return an agent of this tenant (raises if absent in this tenant)."""
        return self.store.require_agent(tenant_id, agent_id)

    def list(self, tenant_id: str, *, status: Optional[str] = None) -> List[Agent]:
        """All agents of one tenant, optionally filtered by status."""
        agents = self.store.list_agents(tenant_id)
        if status is not None:
            agents = [agent for agent in agents if agent.status == status]
        return agents

    def records(self, tenant_id: str) -> List[Dict[str, object]]:
        """Wire view of a tenant's agents."""
        return agent_records(self.store.list_agents(tenant_id))

    # ------------------------------------------------------------------ #
    # routing (delegated to the tenant-scoped TaskRouter)
    # ------------------------------------------------------------------ #
    def set_task_route(
        self,
        tenant_id: str,
        task_type: str,
        agent_ids: List[str],
        *,
        required_capabilities: Optional[List[str]] = None,
    ) -> TaskRoute:
        """Register/overwrite a task route for a tenant (see TaskRouter)."""
        return self.router().set_task_route(
            tenant_id,
            task_type,
            agent_ids,
            required_capabilities=required_capabilities,
        )

    def resolve_task(self, tenant_id: str, task_type: str) -> RouteResolution:
        """Resolve a task type to its routable agents (fails closed)."""
        return self.router().resolve_task(tenant_id, task_type)

    def resolve_required(self, tenant_id: str, task_type: str) -> RouteResolution:
        """Resolve a task type to agents that hold its required capabilities."""
        return self.router().resolve_required(tenant_id, task_type)

    def resolve_by_capability(self, tenant_id: str, capability: str) -> List[Agent]:
        """Active agents of a tenant holding ``capability`` (fail closed)."""
        return self.router().resolve_by_capability(tenant_id, capability)

    def list_routes(self, tenant_id: str) -> List[TaskRoute]:
        return self.router().list_routes(tenant_id)

    # ------------------------------------------------------------------ #
    # identity issuance (delegated to the scoped-claims IdentityService)
    # ------------------------------------------------------------------ #
    def issue_session(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        role: Optional[str] = None,
        ttl_seconds: int = 3600,
        signing_key: Optional[bytes] = None,
        now: Optional[int] = None,
    ) -> AgentSession:
        """Mint a tenant-scoped session credential for an active agent."""
        return self.identity().issue_session(
            tenant_id,
            agent_id,
            role=role,
            ttl_seconds=ttl_seconds,
            signing_key=signing_key,
            now=now,
        )

    def verify_session(
        self, token: str, signing_key: Optional[bytes] = None, *, now: Optional[int] = None
    ) -> AgentSession:
        """Decode + verify a session credential (signature and expiry)."""
        return self.identity().verify_token(token, signing_key, now=now)

    def require_scope(self, session: AgentSession, tenant_id: str) -> AgentSession:
        """Refuse to use a session outside the tenant its claims name."""
        return self.identity().require_scope(session, tenant_id)

    def authorize_tool_use(
        self, session: AgentSession, tenant_id: str, tool_id: str
    ) -> bool:
        """Scope then tool gate for a single tool call (fail closed)."""
        return self.identity().authorize_tool_use(session, tenant_id, tool_id)


__all__ = [
    "Agent",
    "AgentRegistry",
    "AgentSession",
    "AgentAlreadyRegisteredError",
    "RegistryServiceError",
    "RouteResolution",
    "STATUS_ACTIVE",
    "STATUS_PAUSED",
    "STATUS_REGISTERED",
    "STATUS_RETIRED",
    "TaskRoute",
]
