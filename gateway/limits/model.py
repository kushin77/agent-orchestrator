"""Shared value types for the gateway/limits cost/capacity control layer.

Pure data structures (no I/O, no network).  They define the request shape a
model-gateway consumer submits to the cost-control layer, the closed outcome
vocabulary, and the metering record that accounts every outcome.  The
telemetry pillar (phase 5, issues #31-#34) owns canonical usage/cost storage;
this module defines the local cost-control metering record that the gateway
proxy lane (issue #16) consumes until then.

Field vocabulary is consumed from the merged phase-1 contracts, never
redefined: ``model_tier`` uses the agent-profile tier vocabulary
(LOW|MED|HIGH|MAX, registry/profiles/catalog.yaml) and ``task_type`` is a
kebab-case taskType in the registry/prompts sense.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

# --- Closed outcome vocabulary (the metering record can only carry these) ---
CACHE_HIT = "cache_hit"          # served from semantic cache: zero provider cost
PROVIDER = "provider"            # served by a live provider call
BUDGET_EXCEEDED = "budget_exceeded"  # blocked by token budget (enforce mode)
RATE_LIMITED = "rate_limited"    # blocked by rate limiter
QUEUED = "queued"                # backpressure queued the request for later
DEGRADED = "degraded"            # backpressure degraded (no live provider call)
REFUSED = "refused"              # provider output refused by the output throttle

OUTCOMES = frozenset(
    {
        CACHE_HIT,
        PROVIDER,
        BUDGET_EXCEEDED,
        RATE_LIMITED,
        QUEUED,
        DEGRADED,
        REFUSED,
    }
)


def now_utc_iso() -> str:
    """UTC timestamp in the ISO-8601 shape used across the repo's records."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_request_id() -> str:
    """Opaque request identifier for one model call / metering record."""
    return uuid.uuid4().hex


@dataclass(frozen=True)
class ModelCallRequest:
    """One model call as submitted to the cost-control layer.

    ``tenant`` and ``agent`` are opaque ids (identity/onboarding).  ``model_tier``
    is the uppercase tier vocabulary (LOW|MED|HIGH|MAX).  ``task_type`` is an
    optional kebab-case taskType used for output-throttle caps and cache scoping.
    """

    tenant: str
    agent: str
    model_tier: str
    task_type: str | None = None
    prompt: str = ""
    request_id: str = field(default_factory=new_request_id)

    def __post_init__(self) -> None:
        if not self.tenant or not self.agent or not self.model_tier:
            raise ValueError("tenant, agent and model_tier are required")


@dataclass(frozen=True)
class MeteringRecord:
    """The accounting record for one model call.

    A cache hit is recorded as outcome=cache_hit with ``cached=True`` and
    ``zero_cost=True`` (no provider call happened).  Any blocked outcome
    (budget_exceeded / rate_limited / queued / degraded / refused) carries a
    non-null ``reason`` so a caller can never mistake it for a live success.
    """

    tenant: str
    agent: str
    model_tier: str
    task_type: str | None
    request_id: str
    outcome: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_key: str | None = None
    cached: bool = False
    zero_cost: bool = False
    reason: str | None = None
    at: str = field(default_factory=now_utc_iso)

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown metering outcome: {self.outcome!r}")
