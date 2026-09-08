"""Gateway proxy value objects (issue #16).

The data model of the central route -> dispatch -> log funnel.  Field
vocabulary is CONSUMED from the merged sibling contracts and never redefined:

- ``task_type`` is a kebab-case taskType in the registry/prompts sense
  (issue #13) and ``capability`` ids come from the registry/profiles catalog
  (issue #9);
- the agent view mirrors the AgentProfile ten-field contract (issue #9);
- ``model_tier`` values use the ``LOW|MED|HIGH|MAX`` tier vocabulary
  (registry/profiles/catalog.yaml);
- the FinOps ladder tier (``L0/L1/L2``) is mapped onto the registry tier by
  the router (gateway/finops, issue #17).

These objects are the seam the injected resolvers and the core dispatch engine
exchange; they are pure data (no I/O, no network).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from proxy.contract import (
    OUTCOME_CACHE_HIT,
    OUTCOME_SUCCESS,
    OUTCOMES,
)


def new_request_id() -> str:
    """Opaque per-dispatch request identifier (uuid hex)."""
    return uuid.uuid4().hex


def now_utc_iso() -> str:
    """UTC timestamp in the ISO-8601 shape used across the repo's records."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Stream stages (incremental-result support in the dispatch interface)
# --------------------------------------------------------------------------- #
STAGE_RECEIVED = "received"
STAGE_AGENT_RESOLVED = "agent_resolved"
STAGE_TASK_RESOLVED = "task_resolved"
STAGE_ROUTE_SELECTED = "route_selected"
STAGE_GUARD = "guard"
STAGE_ATTEMPT = "attempt"
STAGE_COMPLETED = "completed"

STAGES = frozenset(
    {
        STAGE_RECEIVED,
        STAGE_AGENT_RESOLVED,
        STAGE_TASK_RESOLVED,
        STAGE_ROUTE_SELECTED,
        STAGE_GUARD,
        STAGE_ATTEMPT,
        STAGE_COMPLETED,
    }
)


# --------------------------------------------------------------------------- #
# Core value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Message:
    """One rendered chat turn (role + content) fed to a model backend."""

    role: str
    content: str


@dataclass(frozen=True)
class TaskRequest:
    """A task submitted to an agent (the ``POST .../tasks`` body semantics).

    ``agent_id`` is the REST path parameter (``/v1/agents/{agentId}/tasks``),
    so it is passed to ``dispatch(agent_id, request)`` rather than stored here.
    ``input`` is the per-call variable map used to render the resolved prompt
    module's frozen bodies (``render_prompt`` variables).
    """

    tenant_id: str
    task_type: str
    input: Mapping[str, Any] = field(default_factory=dict)
    complexity: float | None = None
    tokens: int | None = None
    stream: bool = False
    request_id: str = field(default_factory=new_request_id)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.task_type:
            raise ValueError("tenant_id and task_type are required")


@dataclass(frozen=True)
class AgentView:
    """The dispatchable agent as resolved by the injected agent resolver.

    Mirrors the AgentProfile contract (issue #9): closed capability/tool/
    constraint sets and the profile's default model tier.  The proxy consumes
    these boundaries; it does not own them.
    """

    agent_id: str
    tenant_id: str
    profile_id: str
    persona_id: str | None = None
    owner: str | None = None
    capability_set: frozenset[str] = frozenset()
    tool_allowlist: frozenset[str] = frozenset()
    constraint_set: frozenset[str] = frozenset()
    default_model_tier: str = "MED"
    guardrail_policy_ref: str | None = None

    def has_capability(self, capability: str) -> bool:
        return capability in self.capability_set


@dataclass(frozen=True)
class TaskView:
    """A resolved, rendered prompt module (issue #13 contract, consumed).

    ``output_schema`` is the module's JSON Schema (loaded from the
    ``outputSchema`` reference) the typed response must satisfy.  ``messages``
    are the frozen module bodies rendered with the request's variables.
    """

    task_type: str
    version: str
    prompt_id: str
    model_tier_hint: str | None = None
    messages: tuple[Message, ...] = ()
    output_schema: Mapping[str, Any] | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @property
    def user_prompt(self) -> str:
        """The concatenated non-system text (cache/key + token estimate input)."""
        return "\n".join(
            m.content for m in self.messages if m.role != "system"
        ).strip()


@dataclass(frozen=True)
class ChatInvocation:
    """The provider-agnostic request handed to the injected model backend.

    Carries the tenant/agent stamp so a real backend can resolve credentials
    and stamp its own audit/metering events without the proxy core reaching
    into provider internals.
    """

    task_type: str
    tenant_id: str
    agent_id: str
    messages: tuple[Message, ...]
    schema: Mapping[str, Any] | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = ""


@dataclass(frozen=True)
class TierChoice:
    """The chooser seam's decision (duck-typed over gateway/finops).

    The injected chooser (e.g. the FinOps ``ModelChooser``, issue #17) returns
    the cheapest capable *ladder tier* (``L0/L1/L2``) for a task class; the
    router maps it onto the registry tier vocabulary and builds the candidate
    chain.  ``provider``/``model_label`` are optional hints that, when given,
    make the named provider the chain's primary.
    """

    task_class: str
    tier: str  # FinOps ladder tier L0/L1/L2
    estimated_cost_usd: float = 0.0
    budget_action: str = "allow"
    provider: str | None = None
    model_label: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteCandidate:
    """One ordered provider hop of the routed fallback chain."""

    provider: str
    registry_tier: str  # LOW|MED|HIGH|MAX
    role: str  # primary | fallback | local

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "tier": self.registry_tier,
            "role": self.role,
        }


@dataclass(frozen=True)
class RouteDecision:
    """The router's decision: task-type -> capability -> model tier + chain."""

    task_type: str
    capability: str
    task_class: str
    ladder_tier: str  # L0/L1/L2
    registry_tier: str  # LOW/MED/HIGH/MAX
    candidates: tuple[RouteCandidate, ...]  # healthy, ordered primary->...->local
    all_candidates: tuple[RouteCandidate, ...]  # unfiltered (pre-health)
    estimated_cost_usd: float = 0.0
    budget_action: str = "allow"
    reasons: tuple[str, ...] = ()

    def candidate_providers(self) -> tuple[str, ...]:
        return tuple(c.provider for c in self.candidates)


@dataclass(frozen=True)
class DispatchEvent:
    """One incremental pipeline stage, emitted on ``dispatch_stream``."""

    request_id: str
    stage: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"unknown dispatch stage: {self.stage!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "stage": self.stage,
            "data": dict(self.data),
        }


# --------------------------------------------------------------------------- #
# Full gateway call record (audit + metering; criterion 5)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GatewayCallRecord:
    """One full gateway dispatch, recorded to the audit and metering sinks.

    Emitted on EVERY dispatch, whatever the outcome: provider/model/tenant/
    agent/tokens/latency/outcome plus the routing stamp.  The JSONL shape is
    the fleet model-call-audit record (one JSON object per line) that the
    phase-5 telemetry pillar consumes.
    """

    request_id: str
    ts: str
    tenant_id: str
    agent_id: str
    task_type: str
    outcome: str
    capability: str | None = None
    task_class: str | None = None
    tier: str | None = None  # registry tier LOW/MED/HIGH
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    budget_action: str = ""
    attempts: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown call-record outcome: {self.outcome!r}")

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "ts": self.ts,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "taskType": self.task_type,
            "capability": self.capability,
            "taskClass": self.task_class,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "tokens": self.tokens,
            "latencyMs": self.latency_ms,
            "estimatedCostUsd": self.estimated_cost_usd,
            "budgetAction": self.budget_action,
            "attempts": self.attempts,
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# Task result
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TaskResult:
    """The terminal result of one ``dispatch(agent_id, task_request)``.

    ``content`` is the schema-validated typed object on a served outcome and
    ``None`` on every explicit non-success.  ``record`` is the gateway call
    record that was emitted to the audit + metering sinks.  ``events`` is the
    full incremental trace (stage events) so a streaming caller and the thin
    handler can relay it as it happens.
    """

    request_id: str
    tenant_id: str
    agent_id: str
    task_type: str
    outcome: str
    content: Any = None
    provider: str | None = None
    model: str | None = None
    tier: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None
    record: GatewayCallRecord | None = None
    events: tuple[DispatchEvent, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown task-result outcome: {self.outcome!r}")
        # Never-fail-open: a non-served outcome never fabricates typed content.
        if self.outcome not in (OUTCOME_SUCCESS, OUTCOME_CACHE_HIT):
            if self.content is not None:
                raise ValueError(
                    f"outcome {self.outcome!r} must not carry typed content"
                )

    def served(self) -> bool:
        return self.outcome in (OUTCOME_SUCCESS, OUTCOME_CACHE_HIT)

    def to_dict(self) -> dict[str, Any]:
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
