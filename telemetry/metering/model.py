"""telemetry/metering — usage metering + cost-engine data model (issue #33).

The canonical record this lane owns is a ``UsageRecord``: one per model call
(metered or not), carrying the tenant/agent/provider/model/route stamp, real
token counts, the resolved cost estimate and *why* that cost is trustworthy
(cost source), plus an explicit ``metered`` flag so an unmetered call is
never mistaken for a zero-cost one.

Vocabulary is CONSUMED from the merged sibling contracts, never redefined:

- ``provider`` / ``model`` / ``tenant_id`` / ``agent_id`` / ``logical_key`` /
  ``usage`` come from the gateway ``providers`` stamp (issue #15,
  ``ModelCallEvent``);
- ``tier`` / ``task_class`` / ``budget_action`` / ``estimated_cost_usd`` come
  from the gateway ``finops`` chooser (issue #17, ``CallRecord``);
- ``outcome`` / ``model_tier`` / ``request_id`` / ``cached`` / ``zero_cost``
  come from the gateway ``limits`` cost-control layer (issue #19,
  ``MeteringRecord`` — its ``cache_hit`` outcome is the explicit zero-cost
  record this lane honors);
- the camelCase ``requestId`` / ``tenantId`` / ``inputTokens`` /
  ``outputTokens`` / ``estimatedCostUsd`` / ``outcome`` feed is the gateway
  proxy call record that ``telemetry/observability`` absorbs (issue #32).

Cost is resolved in ``intake.py`` from the YAML rate cards under
``rate_cards/``. This module is pure data + tiny time helpers — no I/O, no
network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Optional

SCHEMA_VERSION = 1
RECORD_KIND = "usage"

# --- Source vocabularies this intake accepts (each merged record shape) ----
SOURCE_MODEL_CALL_EVENT = "model_call_event"   # gateway/providers, issue #15
SOURCE_CALL_RECORD = "call_record"             # gateway/finops, issue #17
SOURCE_METERING_RECORD = "metering_record"     # gateway/limits, issue #19
SOURCE_GATEWAY_RECORD = "gateway_record"       # gateway proxy / observability, issue #32
SOURCE_UNKNOWN = "unknown"

# --- Cost-source vocabulary (why a cost figure is trustworthy) -------------
COST_SOURCE_RATE_CARD = "rate_card"    # priced from this lane's YAML rate cards
COST_SOURCE_ATTACHED = "attached"      # positive estimate the source record carried
COST_SOURCE_CACHE_HIT = "cache_hit"    # the explicit zero-cost cache-hit path
COST_SOURCE_NONE = None                # unmetered — never treated as zero

# --- Non-call outcomes (a guard/policy decision, not a provider call) ------
# Consumed across sources: gateway proxy (issue #16) blocked/denied, gateway
# limits (issue #19) budget/rate/backpressure blocks, and gateway finops
# (issue #17) budget_action ``stop`` (the chooser emits nothing for STOP, but
# a stray STOP record means no call was made).  No provider call happened, so
# there is no usage and no cost — these are recorded as non-billable events
# and excluded from usage/cost rollups.
NON_BILLABLE_OUTCOMES = frozenset(
    {
        "blocked",
        "denied",
        "budget_exceeded",
        "rate_limited",
        "queued",
        "degraded",
        "refused",
        "stop",
    }
)

# --- Cache-hit markers (the only explicit zero-cost path) -------------------
CACHE_HIT_OUTCOME = "cache_hit"


def now_utc_iso() -> str:
    """UTC timestamp in the repo-wide RFC 3339 ``Z`` shape."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> str:
    """Normalize a record timestamp to the RFC 3339 ``Z`` string shape.

    Accepts an epoch float/int (gateway ``ts``), an ISO string with a ``Z``
    or numeric offset, or ``None`` (falls back to now).  Unparseable input
    raises ``ValueError`` — a malformed timestamp must never silently
    mis-bucket a record.
    """
    if value is None or value == "":
        return now_utc_iso()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    text = str(value).strip()
    if not text:
        return now_utc_iso()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unparseable timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_bucket(ts: str) -> str:
    """UTC calendar-day bucket (``YYYY-MM-DD``) for a record timestamp."""
    return ts[:10]


def month_bucket(ts: str) -> str:
    """UTC calendar-month bucket (``YYYY-MM``) for a record timestamp."""
    return ts[:7]


@dataclass(frozen=True)
class UsageRecord:
    """The canonical metered record for one model call (issue #33).

    ``metered`` is the honesty flag: ``True`` means a cost was resolved from
    a rate card, an attached positive estimate, or the explicit cache-hit
    zero-cost path; ``False`` means the provider/model is unmetered and the
    cost is deliberately ``None`` (fail closed — never silently zero).
    """

    tenant_id: str
    agent_id: Optional[str]
    provider: Optional[str]
    model: Optional[str]
    route: Optional[str]
    outcome: str
    input_tokens: int
    output_tokens: int
    billable: bool
    metered: bool
    ts: str
    source_type: str
    source_key: str
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None
    cache_hit: bool = False
    unmetered_reason: Optional[str] = None
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed by the call."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the camelCase JSONL shape (one record per line)."""
        payload: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": RECORD_KIND,
            "recordId": self.record_id,
            "sourceType": self.source_type,
            "sourceKey": self.source_key,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "provider": self.provider,
            "model": self.model,
            "route": self.route,
            "outcome": self.outcome,
            "billable": self.billable,
            "metered": self.metered,
            "cacheHit": self.cache_hit,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "costUsd": None if self.cost_usd is None else round(self.cost_usd, 8),
            "costSource": self.cost_source,
            "unmeteredReason": self.unmetered_reason,
            "ts": self.ts,
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UsageRecord":
        """Rebuild a record from its serialized JSONL dict."""
        if payload.get("kind") != RECORD_KIND:
            raise ValueError(
                f"not a metering record (kind={payload.get('kind')!r})"
            )
        return cls(
            tenant_id=str(payload.get("tenantId") or ""),
            agent_id=payload.get("agentId"),
            provider=payload.get("provider"),
            model=payload.get("model"),
            route=payload.get("route"),
            outcome=str(payload.get("outcome") or ""),
            input_tokens=int(payload.get("inputTokens", 0) or 0),
            output_tokens=int(payload.get("outputTokens", 0) or 0),
            billable=bool(payload.get("billable", False)),
            metered=bool(payload.get("metered", False)),
            ts=str(payload.get("ts") or now_utc_iso()),
            source_type=str(payload.get("sourceType") or SOURCE_UNKNOWN),
            source_key=str(payload.get("sourceKey") or ""),
            cost_usd=payload.get("costUsd"),
            cost_source=payload.get("costSource"),
            cache_hit=bool(payload.get("cacheHit", False)),
            unmetered_reason=payload.get("unmeteredReason"),
            record_id=str(payload.get("recordId") or uuid.uuid4().hex),
        )
