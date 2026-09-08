"""telemetry/observability — trace/span data model (issue #32, phase 5).

The observability pillar's own value objects.  Field vocabulary is CONSUMED
from the merged sibling contracts and never redefined:

- the closed dispatch-outcome strings and ``SERVED_OUTCOMES`` set come from
  ``gateway/proxy/contract.py`` (issue #16) — the phase-5 telemetry pillar
  is the stated consumer of the gateway's JSONL model-call-audit record;
- ``tier`` uses the ``LOW|MED|HIGH|MAX`` registry tier vocabulary (issue #9);
- records serialize with the camelCase key shape of ``GatewayCallRecord``
  (``gateway/proxy/model.py``) so every trace line is one JSON object per
  line, the fleet model-call-audit shape.

These are pure data objects (no I/O, no network).  Writers/readers live in
``store.py``; the emit contract lives in ``intake.py``.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Optional

# --------------------------------------------------------------------------- #
# Outcome vocabulary (CONSUMED from gateway/proxy/contract.py, issue #16)
# --------------------------------------------------------------------------- #
OUTCOME_SUCCESS = "success"
OUTCOME_CACHE_HIT = "cache_hit"
OUTCOME_BLOCKED = "blocked"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_REFUSED = "refused"
OUTCOME_CANNOT_ASSESS = "cannot_assess"
OUTCOME_NO_HEALTHY_ROUTE = "no_healthy_route"
OUTCOME_FAILED = "failed"
OUTCOME_DENIED = "denied"

#: The full closed dispatch-outcome vocabulary (mirrors the gateway contract).
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

#: Outcomes that mean the platform failed to serve the request (an outage /
#: degradation signal — the ones an availability SLO must count as bad).
FAILURE_OUTCOMES = frozenset(
    {
        OUTCOME_FAILED,
        OUTCOME_NO_HEALTHY_ROUTE,
        OUTCOME_RATE_LIMITED,
        OUTCOME_REFUSED,
        OUTCOME_CANNOT_ASSESS,
    }
)

#: Outcomes that are a valid platform *decision* (guardrail/policy rejections)
#: rather than a serve attempt — excluded from the availability sample so a
#: pre-flight denial does not masquerade as an outage.
POLICY_OUTCOMES = frozenset({OUTCOME_BLOCKED, OUTCOME_DENIED})

#: Outcomes treated as a completed request attempt (the availability sample).
ATTEMPT_OUTCOMES = frozenset(SERVED_OUTCOMES | FAILURE_OUTCOMES)


def is_served(outcome: str) -> bool:
    """True when ``outcome`` means the request was served with typed output."""
    return outcome in SERVED_OUTCOMES


def is_failure(outcome: str) -> bool:
    """True when ``outcome`` means the platform failed to serve the request."""
    return outcome in FAILURE_OUTCOMES


def is_attempt(outcome: str) -> bool:
    """True when ``outcome`` is a completed serve attempt (good or bad)."""
    return outcome in ATTEMPT_OUTCOMES


def is_attempt_kind(kind: str) -> bool:
    """True when ``kind`` is a serve-attempt span kind (model call/step)."""
    return kind in ATTEMPT_KINDS


# --------------------------------------------------------------------------- #
# Emitting services (the pillars that produce spans)
# --------------------------------------------------------------------------- #
SERVICE_GATEWAY = "gateway"
SERVICE_GUARDRAILS = "guardrails"
SERVICE_ENGINE = "engine"
SERVICE_AGENT_LOOP = "agent_loop"
SERVICES = frozenset(
    {
        SERVICE_GATEWAY,
        SERVICE_GUARDRAILS,
        SERVICE_ENGINE,
        SERVICE_AGENT_LOOP,
    }
)

# --------------------------------------------------------------------------- #
# Span kinds
# --------------------------------------------------------------------------- #
KIND_TRACE = "trace"            # trace root (one per end-to-end request)
KIND_MODEL_CALL = "model_call"  # a provider round-trip (gateway emits these)
KIND_GUARD = "guard"            # a guardrail/policy decision
KIND_STEP = "step"              # an engine / agent-loop step
SPAN_KINDS = frozenset({KIND_TRACE, KIND_MODEL_CALL, KIND_GUARD, KIND_STEP})

#: Span kinds that represent a completed serve attempt (model calls and
#: engine/agent-loop steps).  Trace roots and guardrail decisions are not
#: serve attempts: a pre-flight guard pass/deny must not move the
#: availability ratio.
ATTEMPT_KINDS = frozenset({KIND_MODEL_CALL, KIND_STEP})


def new_id() -> str:
    """Opaque trace/span identifier (uuid hex, matches request_id shape)."""
    return uuid.uuid4().hex


def now_utc_iso() -> str:
    """UTC timestamp in the ISO-8601 shape used across the repo's records."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_epoch(ts: str) -> float:
    """Best-effort parse of a ``YYYY-MM-DDTHH:MM:SSZ``/ISO-8601 timestamp."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


def epoch_of(ts: str) -> float:
    """Epoch-seconds for an ISO-8601/``Z`` timestamp (0.0 if unparseable)."""
    return _iso_to_epoch(ts)


def opt_str(value: Any) -> Optional[str]:
    """Coerce a record value to ``str`` or ``None`` (never the string 'None')."""
    if value is None:
        return None
    text = str(value)
    return text if text else None


@dataclass(frozen=True)
class SpanRecord:
    """One telemetry span: a single observable unit across the request path.

    Every model/gateway/guardrail/agent-loop call is traced as a span that
    carries tenant/agent/provider/model/tokens/latency/outcome plus the
    correlation ids (``trace_id``) that link it end-to-end.  A trace root
    span (``kind == trace``) has ``parent_span_id is None``.
    """

    trace_id: str
    span_id: str
    tenant_id: str
    service: str
    kind: str
    name: str
    outcome: str
    ts: str
    parent_span_id: Optional[str] = None
    request_id: Optional[str] = None
    agent_id: Optional[str] = None
    task_type: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    tier: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    error: Optional[str] = None
    attrs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trace_id or not self.span_id or not self.tenant_id:
            raise ValueError("trace_id, span_id and tenant_id are required")
        if self.service not in SERVICES:
            raise ValueError(f"unknown span service: {self.service!r}")
        if self.kind not in SPAN_KINDS:
            raise ValueError(f"unknown span kind: {self.kind!r}")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown span outcome: {self.outcome!r}")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.latency_ms < 0.0:
            raise ValueError("latency_ms cannot be negative")

    @property
    def tokens(self) -> int:
        """Total tokens consumed by this span."""
        return self.input_tokens + self.output_tokens

    @property
    def is_served(self) -> bool:
        return is_served(self.outcome)

    @property
    def is_failure(self) -> bool:
        return is_failure(self.outcome)

    @property
    def is_attempt(self) -> bool:
        return is_attempt(self.outcome)

    def to_dict(self) -> dict[str, Any]:
        """camelCase JSONL record (the fleet model-call-audit shape)."""
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "requestId": self.request_id,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "taskType": self.task_type,
            "service": self.service,
            "kind": self.kind,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "outcome": self.outcome,
            "ts": self.ts,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "tokens": self.tokens,
            "latencyMs": self.latency_ms,
            "estimatedCostUsd": self.estimated_cost_usd,
            "error": self.error,
            "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SpanRecord":
        data = dict(raw)
        return cls(
            trace_id=str(data["traceId"]),
            span_id=str(data["spanId"]),
            parent_span_id=(
                str(data["parentSpanId"]) if data.get("parentSpanId") else None
            ),
            request_id=opt_str(data.get("requestId")),
            tenant_id=str(data["tenantId"]),
            agent_id=opt_str(data.get("agentId")),
            task_type=opt_str(data.get("taskType")),
            service=str(data["service"]),
            kind=str(data["kind"]),
            name=str(data["name"]),
            provider=opt_str(data.get("provider")),
            model=opt_str(data.get("model")),
            tier=opt_str(data.get("tier")),
            outcome=str(data["outcome"]),
            ts=str(data["ts"]),
            input_tokens=int(data.get("inputTokens", 0) or 0),
            output_tokens=int(data.get("outputTokens", 0) or 0),
            latency_ms=float(data.get("latencyMs", 0.0) or 0.0),
            estimated_cost_usd=float(data.get("estimatedCostUsd", 0.0) or 0.0),
            error=opt_str(data.get("error")),
            attrs=dict(data.get("attrs") or {}),
        )


def _epoch(ts: str) -> float:
    return _iso_to_epoch(ts)


@dataclass
class Trace:
    """An end-to-end request trace: a root span plus every child span.

    ``request_id`` is the cross-pillar correlation key (the gateway's
    ``request_id``/the engine's trace); ``trace_id`` is the telemetry-local
    correlation id that appears on every span line so a full request can be
    reconstructed across gateway -> guardrails -> engine steps.
    """

    trace_id: str
    tenant_id: str
    request_id: str
    started_at: str
    spans: list[SpanRecord] = field(default_factory=list)

    def add_span(self, span: SpanRecord) -> None:
        if span.trace_id != self.trace_id:
            raise ValueError("span trace_id does not match this trace")
        if span.tenant_id != self.tenant_id:
            raise ValueError("span tenant_id does not match this trace")
        self.spans.append(span)

    def __post_init__(self) -> None:
        # Keep spans deterministically ordered (parents before children, then
        # by start time) for stable waterfall rendering.
        self.spans.sort(
            key=lambda s: (
                0 if s.parent_span_id is None else 1,
                _epoch(s.ts),
                s.span_id,
            )
        )

    @property
    def root(self) -> Optional[SpanRecord]:
        for span in self.spans:
            if span.parent_span_id is None:
                return span
        return None

    @property
    def ended_at(self) -> Optional[str]:
        if not self.spans:
            return self.started_at
        return max(self.spans, key=lambda s: _epoch(s.ts)).ts

    @property
    def duration_ms(self) -> float:
        start = self.root.ts if self.root else self.started_at
        return max(0.0, _epoch(self.ended_at or start) - _epoch(start)) * 1000.0

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.spans)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(s.estimated_cost_usd for s in self.spans), 6)

    @property
    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for span in self.spans:
            counts[span.outcome] = counts.get(span.outcome, 0) + 1
        return counts

    def by_id(self) -> dict[str, SpanRecord]:
        return {s.span_id: s for s in self.spans}

    def children_of(self, span_id: Optional[str]) -> list[SpanRecord]:
        return [s for s in self.spans if s.parent_span_id == span_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "tenantId": self.tenant_id,
            "requestId": self.request_id,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "durationMs": round(self.duration_ms, 3),
            "totalTokens": self.total_tokens,
            "totalCostUsd": self.total_cost_usd,
            "outcomes": self.outcome_counts,
            "spans": [s.to_dict() for s in self.spans],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Trace":
        return cls(
            trace_id=str(raw["traceId"]),
            tenant_id=str(raw["tenantId"]),
            request_id=str(raw["requestId"]),
            started_at=str(raw["startedAt"]),
            spans=[SpanRecord.from_dict(s) for s in raw.get("spans", [])],
        )


def quantile(values: list[float], q: float) -> Optional[float]:
    """Deterministic nearest-rank percentile (0..1) over a value list.

    Returns ``None`` for an empty list so callers can distinguish "no data"
    from a measured value (no-false-green: an empty latency sample must not
    read as a healthy 0ms).
    """
    if not values:
        return None
    ordered = sorted(values)
    idx = int(math.ceil(q * len(ordered))) - 1
    idx = max(0, min(idx, len(ordered) - 1))
    return ordered[idx]
