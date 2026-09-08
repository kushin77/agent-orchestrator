"""Agent registry domain model (issue #10): tenant-scoped Agent rows + lifecycle.

The Agent row is the unit the Agent Identity + Registry service manages. It is
derived from a frozen AgentProfile (issue #9): an Agent instance is created
from a ``profile_ref`` (an id in registry/profiles/seeds, e.g. ``coder``) and
inherits the profile's closed ``capabilitySet`` and ``toolAllowlist`` plus its
``owner`` and ``defaultModelTier``. The serialized row keys follow the issue-#10
acceptance criteria (``profileRef``, ``status``, ``owner``, ``lastSeen``,
``capabilities``) and the #9 camelCase JSON style.

Lifecycle (closed status vocabulary with enforced transitions):

::

    registered --activate--> active --pause--> paused
        |                        |               |
        |--------+---------------/               |
        |        |                              |
        +--------+------------ retire ---------> retired (terminal)

The ``registered`` -> ``active`` gap is deliberate: a freshly registered agent
is not yet dispatchable until an operator activates it. ``retired`` is
terminal and can never be re-activated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .errors import InvalidAgentIdError, InvalidTenantIdError, InvalidTransitionError

# Canonical identifiers: lowercase, dash-separated slugs.
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Closed lifecycle status vocabulary.
STATUS_REGISTERED = "registered"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_RETIRED = "retired"
STATUSES = (STATUS_REGISTERED, STATUS_ACTIVE, STATUS_PAUSED, STATUS_RETIRED)
TERMINAL_STATUSES = (STATUS_RETIRED,)

# Lifecycle transitions: action -> the set of current states it may leave.
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "activate": (STATUS_REGISTERED, STATUS_PAUSED),
    "pause": (STATUS_ACTIVE,),
    "retire": (STATUS_REGISTERED, STATUS_ACTIVE, STATUS_PAUSED),
}


def now_utc() -> str:
    """RFC 3339 UTC timestamp, e.g. ``2026-09-08T12:00:00Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_agent_id(agent_id: str) -> None:
    """Reject agent ids that do not match the canonical pattern (fail closed)."""
    if not isinstance(agent_id, str) or not AGENT_ID_RE.match(agent_id):
        raise InvalidAgentIdError(
            f"invalid agent id {agent_id!r}; expected ^[a-z][a-z0-9-]*$"
        )


def validate_tenant_id(tenant_id: str) -> None:
    """Reject tenant ids that do not match the canonical pattern (fail closed)."""
    if not isinstance(tenant_id, str) or not TENANT_ID_RE.match(tenant_id):
        raise InvalidTenantIdError(
            f"invalid tenant id {tenant_id!r}; expected ^[a-z0-9][a-z0-9-]*$"
        )


def require_transition(current: str, action: str) -> None:
    """Refuse a lifecycle action that is not legal from the current status."""
    if current in TERMINAL_STATUSES:
        raise InvalidTransitionError(
            f"agent is {current!r} (terminal); {action!r} is not allowed"
        )
    allowed = TRANSITIONS.get(action)
    if allowed is None:
        raise InvalidTransitionError(f"unknown lifecycle action {action!r}")
    if current not in allowed:
        raise InvalidTransitionError(
            f"cannot {action!r} an agent in status {current!r} "
            f"(allowed sources: {', '.join(allowed)})"
        )


@dataclass(frozen=True)
class Agent:
    """A tenant-scoped registered agent (the registry row).

    Attributes mirror the resolved AgentProfile: ``capabilities`` is the
    profile's closed ``capabilitySet`` and ``tools`` its closed
    ``toolAllowlist`` (both validated against registry/profiles/catalog.yaml at
    registration). ``tenant_id`` is the owning tenant (the agent org); no Agent
    crosses a tenant boundary.
    """

    agent_id: str
    tenant_id: str
    profile_ref: str
    profile_version: str
    owner: str
    capabilities: Tuple[str, ...]
    tools: Tuple[str, ...]
    model_tier: str
    role: Optional[str]
    status: str
    last_seen: Optional[str]
    registered_at: str

    def to_record(self) -> Dict[str, object]:
        """Serialize to the wire/contract view (camelCase row keys)."""
        return {
            "agentId": self.agent_id,
            "tenantId": self.tenant_id,
            "profileRef": self.profile_ref,
            "profileVersion": self.profile_version,
            "owner": self.owner,
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "modelTier": self.model_tier,
            "role": self.role,
            "status": self.status,
            "lastSeen": self.last_seen,
            "registeredAt": self.registered_at,
        }

    def has_capability(self, capability: str) -> bool:
        """Whether this agent declares the given capability."""
        return capability in self.capabilities

    def as_status(self, status: str, *, last_seen: Optional[str] = None) -> "Agent":
        """Return a copy of this agent with a new status (and optional heartbeat)."""
        return replace(self, status=status, last_seen=last_seen)

    @classmethod
    def from_profile(
        cls,
        *,
        tenant_id: str,
        agent_id: str,
        profile_ref: str,
        profile_version: str,
        owner: str,
        capability_set: Tuple[str, ...],
        tool_allowlist: Tuple[str, ...],
        model_tier: str,
        role: Optional[str] = None,
        registered_at: Optional[str] = None,
    ) -> "Agent":
        """Construct a freshly registered Agent from a resolved profile."""
        validate_tenant_id(tenant_id)
        validate_agent_id(agent_id)
        if not capability_set:
            raise ValueError("a registered agent must declare at least one capability")
        if not tool_allowlist:
            raise ValueError("a registered agent must carry at least one tool")
        return cls(
            agent_id=agent_id,
            tenant_id=tenant_id,
            profile_ref=profile_ref,
            profile_version=profile_version,
            owner=owner,
            capabilities=tuple(sorted(capability_set)),
            tools=tuple(sorted(tool_allowlist)),
            model_tier=model_tier,
            role=role,
            status=STATUS_REGISTERED,
            last_seen=None,
            registered_at=registered_at or now_utc(),
        )


def agent_records(agents: List[Agent]) -> List[Dict[str, object]]:
    """Serialize a list of Agents to their record view."""
    return [agent.to_record() for agent in agents]
