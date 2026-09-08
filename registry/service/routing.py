"""Task-route table and capability resolution for the registry (issue #10).

Answers "what can this task type be routed to" within one tenant. The router
keeps tenant-scoped task-route tables (task type -> ordered candidate agents)
and resolves them to the active candidates that can actually serve the route's
required capabilities.

Resolution **fails closed**:

- an unknown task type is denied (``UnknownTaskTypeError``) - it is never
  routed anywhere and never falls back to other task types or other tenants
  (deviation from the hermes fallback-to-any-healthy-agent pattern, which the
  issue explicitly forbids);
- a route may only reference agents registered in the same tenant and
  capabilities from the closed catalog;
- capability lookup is tenant-scoped: an agent that holds the capability in
  another tenant is invisible here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .catalog import require_capability
from .errors import (
    InvalidTaskTypeError,
    NoRoutableAgentError,
    RouteAgentNotInTenantError,
    RoutingError,
    UnknownTaskTypeError,
)
from .model import STATUS_ACTIVE, Agent, now_utc
from .store import RegistryStore

TASK_TYPE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class TaskRoute:
    """A tenant-scoped routing policy for one task type."""

    task_type: str
    tenant_id: str
    agent_ids: Tuple[str, ...]
    required_capabilities: Tuple[str, ...] = ()
    strategy: str = "ordered"
    created_at: str = field(default_factory=now_utc)

    def to_record(self) -> Dict[str, object]:
        return {
            "taskType": self.task_type,
            "tenantId": self.tenant_id,
            "agentIds": list(self.agent_ids),
            "requiredCapabilities": list(self.required_capabilities),
            "strategy": self.strategy,
        }


@dataclass(frozen=True)
class RouteResolution:
    """Result of resolving a task type to routable agents in one tenant."""

    tenant_id: str
    task_type: str
    candidates: Tuple[Agent, ...]
    decision_reason: str

    @property
    def resolved(self) -> bool:
        """True when at least one active candidate can serve the route."""
        return bool(self.candidates)

    @property
    def agent_ids(self) -> Tuple[str, ...]:
        return tuple(agent.agent_id for agent in self.candidates)


def _validate_task_type(task_type: str) -> None:
    if not isinstance(task_type, str) or not TASK_TYPE_RE.match(task_type):
        raise InvalidTaskTypeError(
            f"invalid task type {task_type!r}; expected ^[a-z][a-z0-9-]*$"
        )


class TaskRouter:
    """Tenant-scoped task-route table + fail-closed resolution."""

    def __init__(
        self,
        store: RegistryStore,
        *,
        events: Optional[Any] = None,
        catalog: Optional[Any] = None,
    ) -> None:
        from .catalog import load_catalog

        self._store = store
        self._events = events
        self._catalog = catalog if catalog is not None else load_catalog()
        # tenant_id -> {task_type -> TaskRoute}
        self._routes: Dict[str, Dict[str, TaskRoute]] = {}

    # ------------------------------------------------------------------ #
    # route administration
    # ------------------------------------------------------------------ #
    def set_task_route(
        self,
        tenant_id: str,
        task_type: str,
        agent_ids: List[str],
        *,
        required_capabilities: Optional[List[str]] = None,
        created_at: Optional[str] = None,
    ) -> TaskRoute:
        """Register/overwrite a task route for one tenant (fail closed).

        Every candidate must already be registered in this tenant and must hold
        every required capability; the capability ids must come from the closed
        catalog. A route never names an agent that lives in another tenant.
        """
        _validate_task_type(task_type)
        required = tuple(required_capabilities or ())
        if not agent_ids:
            raise RoutingError(f"route for task type {task_type!r} needs >=1 candidate")
        if len(set(agent_ids)) != len(agent_ids):
            raise RoutingError(f"route for task type {task_type!r} has duplicate agents")
        for capability in required:
            require_capability(capability, self._catalog)
        for agent_id in agent_ids:
            agent = self._store.get_agent(tenant_id, agent_id)
            if agent is None:
                raise RouteAgentNotInTenantError(
                    f"route for task type {task_type!r} names agent {agent_id!r} "
                    f"which is not registered in tenant {tenant_id!r}"
                )
            for capability in required:
                if not agent.has_capability(capability):
                    raise RoutingError(
                        f"agent {agent_id!r} in tenant {tenant_id!r} lacks required "
                        f"capability {capability!r}"
                    )
        route = TaskRoute(
            task_type=task_type,
            tenant_id=tenant_id,
            agent_ids=tuple(agent_ids),
            required_capabilities=required,
            created_at=created_at or now_utc(),
        )
        self._routes.setdefault(tenant_id, {})[task_type] = route
        if self._events is not None:
            self._events.append(
                "route",
                status="set",
                tenant_id=tenant_id,
                actor=None,
                detail={
                    "taskType": task_type,
                    "agentIds": list(agent_ids),
                    "requiredCapabilities": list(required),
                },
            )
        return route

    def list_routes(self, tenant_id: str) -> List[TaskRoute]:
        """All task routes of one tenant, in deterministic task-type order."""
        return sorted(
            self._routes.get(tenant_id, {}).values(), key=lambda route: route.task_type
        )

    def get_task_route(self, tenant_id: str, task_type: str) -> Optional[TaskRoute]:
        return self._routes.get(tenant_id, {}).get(task_type)

    # ------------------------------------------------------------------ #
    # resolution (fail closed)
    # ------------------------------------------------------------------ #
    def resolve_task(self, tenant_id: str, task_type: str) -> RouteResolution:
        """Resolve a task type to its routable agents in this tenant.

        Denies unknown task types (``UnknownTaskTypeError``); never falls back
        to another task or another tenant. Candidates are the route's agents
        that are registered in this tenant and currently ``active`` (paused and
        retired agents are skipped, not substituted).
        """
        route = self._routes.get(tenant_id, {}).get(task_type)
        if route is None:
            raise UnknownTaskTypeError(tenant_id, task_type)
        candidates: List[Agent] = []
        for agent_id in route.agent_ids:
            agent = self._store.get_agent(tenant_id, agent_id)
            if agent is None or agent.status != STATUS_ACTIVE:
                continue
            candidates.append(agent)
        return RouteResolution(
            tenant_id=tenant_id,
            task_type=task_type,
            candidates=tuple(candidates),
            decision_reason=(
                f"route for {task_type} in {tenant_id}: "
                f"{len(candidates)} active candidate(s) of {len(route.agent_ids)}"
            ),
        )

    def resolve_required(
        self, tenant_id: str, task_type: str
    ) -> RouteResolution:
        """Resolve a task type requiring its route's capabilities to be held.

        Stricter than ``resolve_task``: an active candidate that does not hold
        every capability the route requires is skipped. Raises
        ``NoRoutableAgentError`` when no active candidate qualifies, so a caller
        can fail closed rather than dispatch to an unqualified agent.
        """
        route = self._routes.get(tenant_id, {}).get(task_type)
        if route is None:
            raise UnknownTaskTypeError(tenant_id, task_type)
        candidates: List[Agent] = []
        for agent_id in route.agent_ids:
            agent = self._store.get_agent(tenant_id, agent_id)
            if agent is None or agent.status != STATUS_ACTIVE:
                continue
            if all(agent.has_capability(c) for c in route.required_capabilities):
                candidates.append(agent)
        if not candidates:
            raise NoRoutableAgentError(
                f"no active candidate in tenant {tenant_id!r} can serve task type "
                f"{task_type!r} with capabilities "
                f"{list(route.required_capabilities)!r}"
            )
        return RouteResolution(
            tenant_id=tenant_id,
            task_type=task_type,
            candidates=tuple(candidates),
            decision_reason=(
                f"route for {task_type} in {tenant_id}: "
                f"{len(candidates)} qualified active candidate(s)"
            ),
        )

    def resolve_by_capability(self, tenant_id: str, capability: str) -> List[Agent]:
        """Return the active agents of this tenant that hold ``capability``.

        Capability ids are validated against the closed catalog
        (``UnknownCapabilityError``); lookup never crosses a tenant boundary.
        Deterministic (sorted) order for stable routing.
        """
        require_capability(capability, self._catalog)
        return [
            agent
            for agent in self._store.list_agents(tenant_id)
            if agent.status == STATUS_ACTIVE and agent.has_capability(capability)
        ]

    def capacity_report(self, tenant_id: str) -> Dict[str, object]:
        """Per-status agent counts for a tenant (useful for capacity checks)."""
        counts = {"registered": 0, "active": 0, "paused": 0, "retired": 0}
        for agent in self._store.list_agents(tenant_id):
            counts[agent.status] = counts.get(agent.status, 0) + 1
        return {
            "tenantId": tenant_id,
            "byStatus": counts,
            "total": sum(counts.values()),
            "taskTypes": len(self._routes.get(tenant_id, {})),
        }
