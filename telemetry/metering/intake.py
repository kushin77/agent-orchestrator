"""telemetry/metering — usage intake + cost resolution (issue #33).

The intake normalizes every merged model-call record shape into this lane's
canonical ``UsageRecord`` and resolves its cost from the YAML rate cards:

- ``gateway/providers`` ``ModelCallEvent`` dicts (issue #15) — the provider
  stamp with real token usage;
- ``gateway/finops`` ``CallRecord`` dicts (issue #17) — the chooser's routed
  call with an attached ``estimated_cost_usd`` (no token counts);
- ``gateway/limits`` ``MeteringRecord`` dicts (issue #19) — whose
  ``cache_hit`` outcome is the explicit zero-cost record, and whose blocked
  outcomes are non-call events;
- the camelCase gateway-proxy call record that ``telemetry/observability``
  absorbs (issue #32) — ``tenantId``/``provider``/``model``/``inputTokens``/
  ``outputTokens``/``estimatedCostUsd``/``outcome``.

Cost resolution doctrine (each branch is negative-tested in ``tests/``):

1. **Cache hit** (the only explicit zero-cost path) -> ``0.0``,
   ``cost_source=cache_hit``, metered.
2. **Non-call outcome** (blocked / denied / budget_exceeded / rate_limited /
   queued / degraded / refused) -> a non-billable event; no usage, no cost.
3. **Local model** (card entry ``local: true``) -> metered ``0.0`` from the
   card — a *priced* $0, valid even without token counts.
4. **Live call with token usage and a card entry** -> cost from the rate
   card (``cost_source=rate_card``); 0 tokens on a known card is an honest
   ``0.0``.
5. **No card entry but a positive attached estimate** -> attributed as-is
   (``cost_source=attached``).
6. **Anything else** -> ``metered=False`` with an ``unmetered_reason`` and
   ``cost_usd=None`` (fail closed).  An unmetered call is never silently
   priced as zero.

Idempotent ingest: each source record derives a deterministic ``source_key``
(sha256 over the canonicalized source dict); re-feeding the same record is a
duplicate and is never double-counted (see ``store.py``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

from telemetry.metering.model import (
    CACHE_HIT_OUTCOME,
    COST_SOURCE_ATTACHED,
    COST_SOURCE_CACHE_HIT,
    COST_SOURCE_RATE_CARD,
    NON_BILLABLE_OUTCOMES,
    SOURCE_CALL_RECORD,
    SOURCE_GATEWAY_RECORD,
    SOURCE_METERING_RECORD,
    SOURCE_MODEL_CALL_EVENT,
    SOURCE_UNKNOWN,
    UsageRecord,
    parse_ts,
)
from telemetry.metering.ratecards import RateCardStore

#: source-key domain salt (change only if the dedup contract changes on purpose).
_SOURCE_KEY_SALT = "ao-metering/source/v1"

#: ModelCallEvent statuses that mean a provider actually served/completed a
#: call (transport/configuration failures never reached the model and are
#: non-billable).  Consumed from gateway/providers (issue #15).
_LIVE_PROVIDER_STATUSES = frozenset({"success", "output_invalid"})


def _source_key(record: Mapping[str, Any]) -> str:
    """Deterministic dedup key over a canonicalized source record.

    Re-feeding the same record (replay of the same feed line) yields the same
    key, so idempotent ingest can skip it.  Two genuinely different calls
    must differ in at least one field (every real call has its own
    timestamp/request id), so real calls never collide.
    """
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        (_SOURCE_KEY_SALT + "|" + canonical).encode("utf-8")
    ).hexdigest()


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _detect_source(record: Mapping[str, Any]) -> str:
    """Identify which merged record vocabulary a source dict belongs to."""
    if "task_class" in record and "budget_action" in record:
        return SOURCE_CALL_RECORD
    if "usage" in record and "status" in record and "logical_key" in record:
        return SOURCE_MODEL_CALL_EVENT
    if "outcome" in record and "model_tier" in record and "zero_cost" in record:
        return SOURCE_METERING_RECORD
    if "outcome" in record and "tenantId" in record:
        return SOURCE_GATEWAY_RECORD
    return SOURCE_UNKNOWN


class MeteringIntake:
    """Normalizes merged model-call records into ``UsageRecord``s.

    ``rate_store`` supplies the multi-provider rate cards; ``store`` (when
    given) makes :meth:`ingest` idempotent and durable.  :meth:`normalize`
    is a pure function of the source record (no I/O) so tests and the CLI can
    price records without persisting.
    """

    def __init__(
        self,
        rate_store: Optional[RateCardStore] = None,
        store: Any = None,
    ) -> None:
        self.rate_store = rate_store or RateCardStore.load_dir()
        self.store = store

    # -- source vocabulary extractors ------------------------------------
    def _extract_model_call_event(
        self, rec: Mapping[str, Any]
    ) -> dict[str, Any]:
        usage = rec.get("usage")
        has_tokens = isinstance(usage, Mapping)
        input_tokens = _as_int(usage.get("input_tokens")) if has_tokens else 0
        output_tokens = _as_int(usage.get("output_tokens")) if has_tokens else 0
        status = str(rec.get("status") or "")
        tokens_consumed = has_tokens and (input_tokens + output_tokens) > 0
        return {
            "tenant_id": str(rec.get("tenant_id") or ""),
            "agent_id": rec.get("agent_id"),
            "provider": rec.get("provider"),
            "model": rec.get("model"),
            "route": rec.get("logical_key"),
            "outcome": status,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "has_tokens": has_tokens,
            "ts": parse_ts(rec.get("ts")),
            "cache_hit": False,
            "attached_cost": None,
            # A provider failure that never consumed tokens (transport / circuit /
            # config) is not a billable call; a failure that DID consume tokens
            # (e.g. output_invalid) billed real usage and stays billable.
            "non_billable": not tokens_consumed and status not in _LIVE_PROVIDER_STATUSES,
        }

    def _extract_call_record(self, rec: Mapping[str, Any]) -> dict[str, Any]:
        action = str(rec.get("budget_action") or "allow")
        return {
            "tenant_id": str(rec.get("tenant_id") or ""),
            "agent_id": rec.get("agent_id"),
            "provider": rec.get("provider"),
            "model": rec.get("model"),
            "route": rec.get("task_class") or rec.get("tier"),
            "outcome": action,
            "input_tokens": 0,
            "output_tokens": 0,
            "has_tokens": False,
            "ts": parse_ts(rec.get("timestamp")),
            "cache_hit": False,
            "attached_cost": _as_float(rec.get("estimated_cost_usd")),
            # allow/warn/fallback = a routed provider call was made; stop = none.
            "non_billable": action not in ("allow", "warn", "fallback"),
        }

    def _extract_metering_record(self, rec: Mapping[str, Any]) -> dict[str, Any]:
        outcome = str(rec.get("outcome") or "")
        cached = bool(rec.get("cached", False))
        zero_cost = bool(rec.get("zero_cost", False))
        return {
            "tenant_id": str(rec.get("tenant") or ""),
            "agent_id": rec.get("agent"),
            "provider": None,
            "model": None,
            "route": rec.get("model_tier"),
            "outcome": outcome,
            "input_tokens": _as_int(rec.get("input_tokens")),
            "output_tokens": _as_int(rec.get("output_tokens")),
            "has_tokens": True,
            "ts": parse_ts(rec.get("at")),
            "cache_hit": cached or zero_cost or outcome == CACHE_HIT_OUTCOME,
            "attached_cost": None,
            "non_billable": outcome in NON_BILLABLE_OUTCOMES,
        }

    def _extract_gateway_record(self, rec: Mapping[str, Any]) -> dict[str, Any]:
        outcome = str(rec.get("outcome") or "")
        return {
            "tenant_id": str(rec.get("tenantId") or ""),
            "agent_id": rec.get("agentId"),
            "provider": rec.get("provider"),
            "model": rec.get("model"),
            "route": rec.get("tier") or rec.get("taskType"),
            "outcome": outcome,
            "input_tokens": _as_int(rec.get("inputTokens")),
            "output_tokens": _as_int(rec.get("outputTokens")),
            "has_tokens": "inputTokens" in rec or "outputTokens" in rec,
            "ts": parse_ts(rec.get("ts")),
            "cache_hit": outcome == CACHE_HIT_OUTCOME,
            "attached_cost": _as_float(rec.get("estimatedCostUsd")),
            "non_billable": outcome in NON_BILLABLE_OUTCOMES,
        }

    # -- classification + cost resolution ---------------------------------
    def _resolve_cost(
        self,
        provider: Optional[str],
        model: Optional[str],
        has_tokens: bool,
        input_tokens: int,
        output_tokens: int,
        attached_cost: Optional[float],
    ) -> tuple[Optional[float], Optional[str], bool, Optional[str]]:
        """Resolve (cost, cost_source, metered, unmetered_reason).

        Fail-closed doctrine: never fabricate a zero for an unmetered call.
        When the source carries no token counts at all (a gateway finops
        ``CallRecord``), a positive attached estimate is the only trustworthy
        price — the card cannot price unknown usage.
        """
        entry = None
        if provider and model:
            entry = self.rate_store.lookup(provider, model)
            if entry is not None and entry.local:
                # Priced $0 local model — unconditional regardless of usage.
                return 0.0, COST_SOURCE_RATE_CARD, True, None
            if entry is not None and has_tokens:
                estimate = self.rate_store.estimate(
                    provider, model, input_tokens, output_tokens
                )
                # estimate cannot be None here (the entry is known).
                return estimate.cost_usd, COST_SOURCE_RATE_CARD, True, None
        if attached_cost is not None and attached_cost > 0:
            return attached_cost, COST_SOURCE_ATTACHED, True, None
        if entry is not None:
            return (
                None,
                None,
                False,
                f"no token usage on record; cannot price {provider}/{model}",
            )
        where = f"{provider}/{model}" if (provider and model) else "unknown provider/model"
        return (
            None,
            None,
            False,
            f"no rate card for {where} and no attached cost estimate",
        )

    # -- public surface ---------------------------------------------------
    def normalize(self, source: Mapping[str, Any]) -> UsageRecord:
        """Normalize one merged source record into a canonical ``UsageRecord``.

        Raises ``ValueError`` when the source dict carries no recognizable
        vocabulary, or when it lacks a tenant stamp (a record with no tenant
        cannot be attributed).
        """
        source_type = _detect_source(source)
        if source_type == SOURCE_UNKNOWN:
            raise ValueError("unrecognized metering source record vocabulary")
        if source_type == SOURCE_MODEL_CALL_EVENT:
            fields = self._extract_model_call_event(source)
        elif source_type == SOURCE_CALL_RECORD:
            fields = self._extract_call_record(source)
        elif source_type == SOURCE_METERING_RECORD:
            fields = self._extract_metering_record(source)
        else:
            fields = self._extract_gateway_record(source)

        tenant_id = fields["tenant_id"]
        if not tenant_id:
            raise ValueError("metering record missing tenant stamp")

        outcome = fields["outcome"]
        cache_hit = bool(fields["cache_hit"])
        non_billable = bool(fields.get("non_billable", False)) or (
            outcome in NON_BILLABLE_OUTCOMES
        )

        # 1) Non-call outcomes -> non-billable event (no usage, no cost).
        if non_billable and not cache_hit:
            return UsageRecord(
                tenant_id=tenant_id,
                agent_id=fields["agent_id"],
                provider=fields["provider"],
                model=fields["model"],
                route=fields["route"],
                outcome=outcome,
                input_tokens=0,
                output_tokens=0,
                billable=False,
                metered=True,
                ts=fields["ts"],
                source_type=source_type,
                source_key=_source_key(dict(source)),
                cache_hit=False,
                unmetered_reason=None,
            )

        # 2) Explicit cache hit -> the only allowed metered zero-cost path.
        if cache_hit:
            return UsageRecord(
                tenant_id=tenant_id,
                agent_id=fields["agent_id"],
                provider=fields["provider"],
                model=fields["model"],
                route=fields["route"],
                outcome=CACHE_HIT_OUTCOME,
                input_tokens=0,
                output_tokens=0,
                billable=True,
                metered=True,
                cost_usd=0.0,
                cost_source=COST_SOURCE_CACHE_HIT,
                cache_hit=True,
                ts=fields["ts"],
                source_type=source_type,
                source_key=_source_key(dict(source)),
                unmetered_reason=None,
            )

        # 3) Live call -> price it (or fail closed when unmeterable).
        cost, cost_source, metered, reason = self._resolve_cost(
            fields["provider"],
            fields["model"],
            fields["has_tokens"],
            fields["input_tokens"],
            fields["output_tokens"],
            fields["attached_cost"],
        )
        billable = not non_billable
        return UsageRecord(
            tenant_id=tenant_id,
            agent_id=fields["agent_id"],
            provider=fields["provider"],
            model=fields["model"],
            route=fields["route"],
            outcome=outcome,
            input_tokens=fields["input_tokens"],
            output_tokens=fields["output_tokens"],
            billable=billable,
            metered=metered,
            cost_usd=cost,
            cost_source=cost_source,
            cache_hit=False,
            unmetered_reason=reason,
            ts=fields["ts"],
            source_type=source_type,
            source_key=_source_key(dict(source)),
        )

    def ingest(self, source: Mapping[str, Any]) -> "IngestOutcome":
        """Normalize and durably persist one source record (idempotent).

        Returns an ``IngestOutcome`` carrying the normalized record and
        whether it was a replay duplicate.  Without a configured ``store``
        this just normalizes (``duplicate=False``, nothing persisted).
        """
        record = self.normalize(source)
        if self.store is None:
            return IngestOutcome(record=record, duplicate=False)
        if self.store.seen(record.source_key):
            return IngestOutcome(record=record, duplicate=True)
        self.store.append(record)
        return IngestOutcome(record=record, duplicate=False)

    def ingest_many(self, sources: List[Mapping[str, Any]]) -> "IngestSummary":
        """Ingest a batch, tallying ingested/duplicates/unmetered/cache-hits."""
        summary = IngestSummary()
        for source in sources:
            outcome = self.ingest(source)
            summary.total += 1
            if outcome.duplicate:
                summary.duplicates += 1
                continue
            summary.ingested += 1
            if outcome.record.unmetered_reason is not None:
                summary.unmetered += 1
            if outcome.record.cache_hit:
                summary.cache_hits += 1
            if not outcome.record.billable:
                summary.non_billable += 1
        return summary


@dataclass(frozen=True)
class IngestOutcome:
    """Result of ingesting one source record."""

    record: UsageRecord
    duplicate: bool


@dataclass
class IngestSummary:
    """Tallies for one batch ingest."""

    total: int = 0
    ingested: int = 0
    duplicates: int = 0
    unmetered: int = 0
    cache_hits: int = 0
    non_billable: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "ingested": self.ingested,
            "duplicates": self.duplicates,
            "unmetered": self.unmetered,
            "cacheHits": self.cache_hits,
            "nonBillable": self.non_billable,
        }
