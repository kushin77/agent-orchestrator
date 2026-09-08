"""Control-plane typed models: requests, views and validation.

Pydantic-free by fleet doctrine (Python 3 stdlib only). Typed request bodies
are frozen dataclasses parsed from untrusted JSON with explicit validators
that raise :class:`ApiError` (400) on the first bad field; responses are typed
:class:`~.View` dataclasses that the handlers construct from the merged pillar
modules. Field names deliberately mirror the frozen upstream vocabulary
(``profileRef``, ``taskType``, ``tenantId``, ...) so a client speaks the same
wire language as the registry / gateway / telemetry contracts.

Validation invariants
---------------------

- ids are non-empty strings matching ``^[A-Za-z0-9][A-Za-z0-9._-]*$``;
- ``profile_ref`` must resolve to a frozen profile id (handlers enforce the
  registry catalog membership; here we only shape-check);
- ``task_type`` is a kebab ``^[a-z][a-z0-9-]*$`` (registry/prompts #13 shape);
- pagination/count limits are clamped, never silently negative.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .errors import invalid_body, validation_error

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TASKTYPE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{12}$")


# --- request-id -------------------------------------------------------------


def new_request_id(rng: Any) -> str:
    """Build a ``req_<hex>`` request id from an injectable randomness source."""
    return f"req_{rng()[:12]}"


def is_request_id(value: str) -> bool:
    return bool(_REQUEST_ID_RE.match(value or ""))


# --- validation helpers ------------------------------------------------------


def check_id(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    """Validate a resource/path id and return it as ``str``."""
    if not isinstance(value, str):
        raise validation_error(f"{field_name} must be a string", field=field_name)
    value = value.strip()
    if not value:
        if allow_empty:
            return value
        raise validation_error(f"{field_name} must not be empty", field=field_name)
    if not _ID_RE.match(value):
        raise validation_error(
            f"{field_name} has an invalid format", field=field_name, value=value
        )
    return value


def check_optional_id(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return check_id(value, field_name)


def check_task_type(value: Any) -> str:
    if not isinstance(value, str) or not _TASKTYPE_RE.match(value):
        raise validation_error(
            "taskType must be a kebab-case identifier", field="taskType", value=value
        )
    return value


def check_string(value: Any, field_name: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str):
        raise validation_error(f"{field_name} must be a string", field=field_name)
    value = value.strip()
    if not value:
        raise validation_error(f"{field_name} must not be empty", field=field_name)
    if len(value) > max_len:
        raise validation_error(
            f"{field_name} exceeds {max_len} characters", field=field_name
        )
    return value


def check_object(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise validation_error(f"{field_name} must be an object", field=field_name)
    return value


def check_nonnegative_int(value: Any, field_name: str, *, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise validation_error(
            f"{field_name} must be a non-negative integer", field=field_name
        )
    return value


def clamp_limit(value: Any, default: int = 100, maximum: int = 1000) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise validation_error("limit must be a positive integer", field="limit")
    return min(value, maximum)


# --- typed request bodies ---------------------------------------------------


@dataclass(frozen=True)
class RegisterAgentRequest:
    """POST /v1/agents — register an agent from a frozen profile seed."""

    agent_id: str
    profile_ref: str
    role: Optional[str] = None

    @classmethod
    def parse(cls, body: Optional[Dict[str, Any]]) -> "RegisterAgentRequest":
        if not isinstance(body, dict):
            raise invalid_body("request body must be a JSON object")
        return cls(
            agent_id=check_id(body.get("agentId"), "agentId"),
            profile_ref=check_id(body.get("profileRef"), "profileRef"),
            role=check_optional_id(body.get("role"), "role"),
        )


@dataclass(frozen=True)
class LifecycleRequest:
    """POST /v1/agents/{id}/activate|pause|retire — lifecycle transition.

    ``reason`` is optional but encouraged for pause/retire so the audit trail
    and the approval request carry intent.
    """

    reason: Optional[str] = None

    @classmethod
    def parse(cls, body: Optional[Dict[str, Any]]) -> "LifecycleRequest":
        if body is None:
            return cls()
        if not isinstance(body, dict):
            raise invalid_body("request body must be a JSON object")
        reason = body.get("reason")
        return cls(reason=None if reason is None else check_string(reason, "reason"))


@dataclass(frozen=True)
class DispatchTaskRequest:
    """POST /v1/agents/{id}/tasks — dispatch a task to an agent.

    Mirrors the gateway-proxy ``POST /v1/agents/{agentId}/tasks`` body
    (taskType = a published prompt-module taskType; input = render vars).
    """

    task_type: str
    input: Dict[str, Any] = field(default_factory=dict)
    idempotency_key: Optional[str] = None

    @classmethod
    def parse(cls, body: Optional[Dict[str, Any]]) -> "DispatchTaskRequest":
        if not isinstance(body, dict):
            raise invalid_body("request body must be a JSON object")
        return cls(
            task_type=check_task_type(body.get("taskType")),
            input=check_object(body.get("input", {}), "input"),
            idempotency_key=check_optional_id(
                body.get("idempotencyKey"), "idempotencyKey"
            ),
        )


@dataclass(frozen=True)
class TenantActionRequest:
    """POST /v1/tenants/{id}/pause|resume — tenant-level kill-switch actions."""

    reason: Optional[str] = None

    @classmethod
    def parse(cls, body: Optional[Dict[str, Any]]) -> "TenantActionRequest":
        if body is None:
            return cls()
        if not isinstance(body, dict):
            raise invalid_body("request body must be a JSON object")
        reason = body.get("reason")
        return cls(reason=None if reason is None else check_string(reason, "reason"))


# --- response views ---------------------------------------------------------

# Views are typed snapshots of the merged pillar entities. Handlers build them
# from the injected ports; ``to_dict`` renders the wire payload.


@dataclass(frozen=True)
class View:
    """Base view: JSON render via dataclasses.asdict."""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentView(View):
    agentId: str
    tenantId: str
    profileRef: str
    profileVersion: str
    status: str
    capabilities: List[str] = field(default_factory=list)
    modelTier: str = ""
    owner: str = ""
    lastSeen: Optional[str] = None
    registeredAt: str = ""


@dataclass(frozen=True)
class ProfileView(View):
    id: str
    version: str
    owner: str = ""
    systemPromptRef: str = ""
    toolAllowlist: List[str] = field(default_factory=list)
    capabilitySet: List[str] = field(default_factory=list)
    defaultModelTier: str = ""
    guardrailPolicyRef: str = ""


@dataclass(frozen=True)
class PersonaView(View):
    id: str
    version: str
    tenant: str = "platform"
    name: str = ""
    posture: str = ""
    defaultModelTier: str = ""
    systemPromptRef: str = ""
    ownedLanes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PromptModuleView(View):
    taskType: str
    version: str
    status: str = "published"
    description: str = ""
    modelTierHint: str = ""


@dataclass(frozen=True)
class PolicyView(View):
    id: str
    description: str = ""
    mode: str = "observe"
    controls: int = 0


@dataclass(frozen=True)
class TenantView(View):
    tenantId: str
    name: str
    tenantType: str = "platform"
    plan: str = "free"
    subscriptionStatus: str = "active"
    killSwitch: bool = False


@dataclass(frozen=True)
class UsageView(View):
    tenantId: str
    usage: Dict[str, Any] = field(default_factory=dict)
    budgetPositions: Dict[str, Any] = field(default_factory=dict)
    warnAtPct: float = 0.8


@dataclass(frozen=True)
class QuotaView(View):
    tenantId: str
    plan: str = "free"
    quotas: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskView(View):
    taskId: str
    tenantId: str
    agentId: str
    taskType: str
    status: str = "PENDING"


@dataclass(frozen=True)
class AuditRecordView(View):
    seq: int
    ts: str
    tenantId: str
    actor: str
    action: str
    resource: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class EventView(View):
    seq: int
    eventId: str
    type: str
    tenantId: str
    aggregateId: Optional[str] = None
    createdAt: str = ""
    state: str = "pending"
    attempts: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalView(View):
    approvalId: str
    action: str
    resource: str
    requester: str
    reason: str = ""
    status: str = "pending"
    approver: Optional[str] = None
    decidedAt: Optional[str] = None
