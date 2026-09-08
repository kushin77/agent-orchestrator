"""Consumer SDK typed models (issue #41).

Typed value objects exchanged with the platform.  Field vocabulary is
**consumed** from the merged contracts and never redefined:

- the gateway dispatch shape (``taskType``/``input``/``outcome``/``content``,
  issue #16 ``gateway/proxy``) — ``content`` is the schema-validated typed
  object on a served outcome and ``None`` otherwise;
- the session-token claim keys (issue #10 registry/service + issue #35 sso):
  ``iss``/``sub``/``aud``/``iat``/``exp``/``jti`` plus the scoped
  ``tenantId``/``agentId``/``role``/``allowedTools``;
- the control-plane envelope field names (``ok``/``status``/``requestId``/
  ``data``/``error``, issue #38 ``identity/cpapi``) and the public-edge route
  surface (issue #37 ``identity/edges``);
- the MCP tool-catalog + JSON-RPC vocabulary (issue #20 ``gateway/mcp``).

Wire serialization uses the platform's camelCase JSON keys; the Python
attributes are snake_case.  All models are immutable dataclasses with
``from_dict``/``to_dict`` helpers and fail closed on malformed input.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

# --------------------------------------------------------------------------- #
# Closed vocabularies (mirrored from the merged contracts)
# --------------------------------------------------------------------------- #
#: Gateway dispatch outcomes (issue #16 contract.py — closed set).
OUTCOME_SUCCESS = "success"
OUTCOME_CACHE_HIT = "cache_hit"
OUTCOME_BLOCKED = "blocked"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_REFUSED = "refused"
OUTCOME_CANNOT_ASSESS = "cannot_assess"
OUTCOME_NO_HEALTHY_ROUTE = "no_healthy_route"
OUTCOME_FAILED = "failed"
OUTCOME_DENIED = "denied"

OUTCOMES = frozenset(
    {
        OUTCOME_SUCCESS,
        OUTCOME_CACHE_HIT,
        OUTCOME_BLOCKED,
        OUTCOME_RATE_LIMITED,
        OUTCOME_REFUSED,
        OUTCOME_CANNOT_ASSESS,
        OUTCOME_NO_HEALTHY_ROUTE,
        OUTCOME_FAILED,
        OUTCOME_DENIED,
    }
)

#: Outcomes that mean the request was actually served with typed content.
SERVED_OUTCOMES = frozenset({OUTCOME_SUCCESS, OUTCOME_CACHE_HIT})

#: Outcome -> HTTP status semantic of the gateway REST surface (issue #16).
OUTCOME_STATUS = {
    OUTCOME_SUCCESS: 200,
    OUTCOME_CACHE_HIT: 200,
    OUTCOME_BLOCKED: 429,
    OUTCOME_RATE_LIMITED: 429,
    OUTCOME_REFUSED: 422,
    OUTCOME_CANNOT_ASSESS: 422,
    OUTCOME_NO_HEALTHY_ROUTE: 503,
    OUTCOME_FAILED: 502,
    OUTCOME_DENIED: 403,
}

#: Dispatch stream stages (issue #16).
STAGES = frozenset(
    {
        "received",
        "agent_resolved",
        "task_resolved",
        "route_selected",
        "guard",
        "attempt",
        "completed",
    }
)

#: Session-token claim keys the SDK reads (issue #10/#35).
CLAIM_ISSUER = "iss"
CLAIM_SUBJECT = "sub"
CLAIM_AUDIENCE = "aud"
CLAIM_ISSUED_AT = "iat"
CLAIM_EXPIRY = "exp"
CLAIM_JTI = "jti"
CLAIM_TENANT_ID = "tenantId"
CLAIM_AGENT_ID = "agentId"
CLAIM_SUBJECT_TYPE = "subjectType"
CLAIM_ROLE = "role"
CLAIM_PURPOSE = "purpose"
CLAIM_ALLOWED_TOOLS = "allowedTools"


def outcome_is_served(outcome: str) -> bool:
    """Whether an outcome represents a served (typed) response."""
    return outcome in SERVED_OUTCOMES


# --------------------------------------------------------------------------- #
# Gateway task models (issue #16)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TaskRequest:
    """A task submitted to an agent (``POST /v1/agents/{agentId}/tasks``).

    ``agent_id`` is the REST path parameter; ``task_type`` is a published
    prompt-module task type; ``input`` is the per-call render-variable map.
    """

    tenant_id: str
    task_type: str
    input: Mapping[str, Any] = field(default_factory=dict)
    complexity: Optional[float] = None
    tokens: Optional[int] = None
    stream: bool = False
    request_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.task_type:
            raise ValueError("tenant_id and task_type are required")

    def to_wire(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "tenantId": self.tenant_id,
            "taskType": self.task_type,
            "input": dict(self.input),
        }
        if self.complexity is not None:
            payload["complexity"] = self.complexity
        if self.tokens is not None:
            payload["tokens"] = self.tokens
        if self.stream:
            payload["stream"] = True
        if self.request_id:
            payload["requestId"] = self.request_id
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class DispatchEvent:
    """One incremental dispatch stage emitted on a streaming dispatch."""

    request_id: str
    stage: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"unknown dispatch stage: {self.stage!r}")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DispatchEvent":
        return cls(
            request_id=str(raw.get("requestId") or ""),
            stage=str(raw.get("stage") or ""),
            data=dict(raw.get("data") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requestId": self.request_id,
            "stage": self.stage,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class GatewayCallRecord:
    """One full gateway dispatch record (audit/metering; issue #16)."""

    request_id: str
    ts: str
    tenant_id: str
    agent_id: str
    task_type: str
    outcome: str
    capability: Optional[str] = None
    task_class: Optional[str] = None
    tier: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    budget_action: str = ""
    attempts: int = 0
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> Optional["GatewayCallRecord"]:
        if not raw:
            return None
        return cls(
            request_id=str(raw.get("requestId") or ""),
            ts=str(raw.get("ts") or ""),
            tenant_id=str(raw.get("tenantId") or ""),
            agent_id=str(raw.get("agentId") or ""),
            task_type=str(raw.get("taskType") or ""),
            outcome=str(raw.get("outcome") or ""),
            capability=raw.get("capability"),
            task_class=raw.get("taskClass"),
            tier=raw.get("tier"),
            provider=raw.get("provider"),
            model=raw.get("model"),
            input_tokens=int(raw.get("inputTokens") or 0),
            output_tokens=int(raw.get("outputTokens") or 0),
            latency_ms=float(raw.get("latencyMs") or 0.0),
            estimated_cost_usd=float(raw.get("estimatedCostUsd") or 0.0),
            budget_action=str(raw.get("budgetAction") or ""),
            attempts=int(raw.get("attempts") or 0),
            error=raw.get("error"),
        )


@dataclass(frozen=True)
class TaskResult:
    """The terminal result of one task dispatch (typed, fail closed).

    ``content`` is the schema-validated typed object for a served outcome and
    ``None`` on every explicit non-served outcome (the proxy never fabricates
    content, and neither does the SDK).  ``record`` is the gateway call record
    that was emitted to audit + metering.
    """

    request_id: str
    tenant_id: str
    agent_id: str
    task_type: str
    outcome: str
    content: Any = None
    provider: Optional[str] = None
    model: Optional[str] = None
    tier: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    error: Optional[str] = None
    record: Optional[GatewayCallRecord] = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown task-result outcome: {self.outcome!r}")
        if not outcome_is_served(self.outcome):
            if self.content is not None:
                raise ValueError(
                    f"outcome {self.outcome!r} must not carry typed content"
                )

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any], *, record: Optional[GatewayCallRecord] = None
    ) -> "TaskResult":
        return cls(
            request_id=str(raw.get("requestId") or ""),
            tenant_id=str(raw.get("tenantId") or ""),
            agent_id=str(raw.get("agentId") or ""),
            task_type=str(raw.get("taskType") or ""),
            outcome=str(raw.get("outcome") or ""),
            content=raw.get("content"),
            provider=raw.get("provider"),
            model=raw.get("model"),
            tier=raw.get("tier"),
            input_tokens=int(raw.get("inputTokens") or 0),
            output_tokens=int(raw.get("outputTokens") or 0),
            latency_ms=float(raw.get("latencyMs") or 0.0),
            error=raw.get("error"),
            record=record,
        )

    def served(self) -> bool:
        return outcome_is_served(self.outcome)

    def ensure_served(self) -> "TaskResult":
        """Return self when served, else raise ``TaskNotServedError``."""
        if not self.served():
            from .errors import TaskNotServedError

            raise TaskNotServedError(self.outcome, self)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requestId": self.request_id,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "taskType": self.task_type,
            "outcome": self.outcome,
            "content": self.content,
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "latencyMs": self.latency_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class TaskEnvelope:
    """The gateway REST envelope ``{status, result, record}`` (issue #16)."""

    status: int
    result: TaskResult
    record: Optional[GatewayCallRecord] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskEnvelope":
        return cls(
            status=int(raw.get("status") or 500),
            result=TaskResult.from_dict(
                dict(raw.get("result") or {}),
                record=GatewayCallRecord.from_dict(raw.get("record")),
            ),
            record=GatewayCallRecord.from_dict(raw.get("record")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "result": self.result.to_dict(),
            "record": self.record.to_dict() if self.record else None,
        }


# --------------------------------------------------------------------------- #
# Control-plane models (usage / audit / policy; issues #37/#38)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class UsageBudget:
    """Per-tenant budget posture riding on a usage report (issue #34 vocab)."""

    limit_usd: Optional[float] = None
    spent_usd: float = 0.0
    action: str = "observe"  # observe | enforce (issue #33 rollout vocab)
    warn_at_pct: Optional[float] = None
    status: str = "ok"  # ok | warn | blocked

    @classmethod
    def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "UsageBudget":
        raw = raw or {}
        return cls(
            limit_usd=raw.get("limitUsd"),
            spent_usd=float(raw.get("spentUsd") or 0.0),
            action=str(raw.get("action") or "observe"),
            warn_at_pct=raw.get("warnAtPct"),
            status=str(raw.get("status") or "ok"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limitUsd": self.limit_usd,
            "spentUsd": self.spent_usd,
            "action": self.action,
            "warnAtPct": self.warn_at_pct,
            "status": self.status,
        }


@dataclass(frozen=True)
class UsageReport:
    """Tenant usage summary (``GET /v1/tenants/{tenantId}/usage`` / ``me``)."""

    tenant_id: str
    period: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    budget: Optional[UsageBudget] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "UsageReport":
        return cls(
            tenant_id=str(raw.get("tenantId") or ""),
            period=str(raw.get("period") or ""),
            calls=int(raw.get("calls") or 0),
            input_tokens=int(raw.get("inputTokens") or 0),
            output_tokens=int(raw.get("outputTokens") or 0),
            estimated_cost_usd=float(raw.get("estimatedCostUsd") or 0.0),
            budget=UsageBudget.from_dict(raw.get("budget")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "period": self.period,
            "calls": self.calls,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "estimatedCostUsd": self.estimated_cost_usd,
            "budget": self.budget.to_dict() if self.budget else None,
        }


@dataclass(frozen=True)
class AuditRecord:
    """One append-only audit record (issue #38 / ledger vocabulary).

    ``actor`` uses the platform ``kind:id`` shape and ``resource`` the
    ``resource`` the action targeted; ``detail`` carries the structured extra
    context of the record.
    """

    seq: int
    ts: str
    actor: str
    action: str
    resource: str
    tenant_id: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AuditRecord":
        return cls(
            seq=int(raw.get("seq") or 0),
            ts=str(raw.get("ts") or ""),
            actor=str(raw.get("actor") or ""),
            action=str(raw.get("action") or ""),
            resource=str(raw.get("resource") or ""),
            tenant_id=str(raw.get("tenantId") or ""),
            detail=dict(raw.get("detail") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "tenantId": self.tenant_id,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class PolicyBinding:
    """One declared policy binding (``GET /v1/policies`` / ``{policyId}``)."""

    policy_id: str
    name: str = ""
    bundle: Optional[str] = None
    version: Optional[str] = None
    tenant_id: str = ""
    enabled: bool = True
    controls: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PolicyBinding":
        return cls(
            policy_id=str(raw.get("policyId") or ""),
            name=str(raw.get("name") or raw.get("policyId") or ""),
            bundle=raw.get("bundle"),
            version=raw.get("version"),
            tenant_id=str(raw.get("tenantId") or ""),
            enabled=bool(raw.get("enabled", True)),
            controls=list(raw.get("controls") or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policyId": self.policy_id,
            "name": self.name,
            "bundle": self.bundle,
            "version": self.version,
            "tenantId": self.tenant_id,
            "enabled": self.enabled,
            "controls": list(self.controls),
        }


# --------------------------------------------------------------------------- #
# MCP models (issue #20)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolDefinition:
    """One declared MCP tool (id, description, JSON input schema)."""

    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolDefinition":
        return cls(
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            input_schema=dict(raw.get("inputSchema") or raw.get("input_schema") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


@dataclass(frozen=True)
class ToolResult:
    """The result of one ``tools/call`` (JSON-RPC text content items)."""

    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False

    @property
    def text(self) -> str:
        parts = [c.get("text", "") for c in self.content if c.get("type") == "text"]
        return "\n".join(parts)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolResult":
        return cls(
            content=list(raw.get("content") or []),
            is_error=bool(raw.get("isError", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"content": list(self.content), "isError": self.is_error}


# --------------------------------------------------------------------------- #
# Small JOSE-shape helpers (decode only — verification belongs to the platform)
# --------------------------------------------------------------------------- #
def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("malformed base64url segment") from exc


def decode_claims(compact_token: str) -> Dict[str, Any]:
    """Best-effort decode of the JWT-shaped session token payload claims.

    The SDK only *reads* claims (tenant scoping, expiry) to build requests and
    provide a per-tenant guard.  Signature verification is the platform's job
    (identity/sso + edges verify; the SDK never trusts a token it could not
    parse).  Returns the payload mapping on success and raises ``ValueError``
    on a malformed token.
    """
    parts = compact_token.split(".")
    if len(parts) != 3:
        raise ValueError("session token must be header.payload.signature")
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception as exc:
        raise ValueError("session token payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("session token payload must be a JSON object")
    return payload
