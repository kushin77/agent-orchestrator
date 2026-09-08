"""telemetry/metering — usage rollups + cost attribution report (issue #33).

Builds the durable aggregation on top of the store: daily and monthly usage
rollups per tenant/agent/model/provider, and the cost-attribution report the
SaaS bills on (per-tenant cost, per-agent cost, provider mix).  Rollups count
only *billable* records (a blocked/denied guard decision is not usage), and a
record that could not be priced is surfaced as ``unmetered`` — its tokens are
counted but its cost is **never** assumed to be zero.  A cost total is
therefore always an honest sum over metered records only.

This is also the tenant + internal-billing "usage API": pure functions over
the store, consumed by the CLI and, later, by the control-plane portal and
the phase-5 budgets lane (issue #34).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from telemetry.metering.model import UsageRecord, day_bucket, month_bucket
from telemetry.metering.store import UsageStore

GROUP_TENANT = "tenant"
GROUP_AGENT = "agent"
GROUP_PROVIDER = "provider"
GROUP_MODEL = "model"
WINDOW_DAY = "day"
WINDOW_MONTH = "month"
WINDOWS = frozenset({WINDOW_DAY, WINDOW_MONTH})

_DIMENSION_GETTERS = {
    GROUP_TENANT: lambda r: r.tenant_id,
    GROUP_AGENT: lambda r: r.agent_id or "",
    GROUP_PROVIDER: lambda r: r.provider or "",
    GROUP_MODEL: lambda r: r.model or "",
}


@dataclass(frozen=True)
class UsageAggregate:
    """Totals for one aggregation key (billable calls only)."""

    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    unmetered_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "cacheHits": self.cache_hits,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "costUsd": round(self.cost_usd, 8),
            "unmeteredCalls": self.unmetered_calls,
        }


def _empty() -> UsageAggregate:
    return UsageAggregate()


def _bucket(record: UsageRecord, window: str) -> str:
    if window == WINDOW_DAY:
        return day_bucket(record.ts)
    if window == WINDOW_MONTH:
        return month_bucket(record.ts)
    raise ValueError(f"unknown rollup window: {window!r}")


class UsageReporter:
    """Aggregates stored usage records into rollups and cost reports."""

    def __init__(self, store: UsageStore) -> None:
        self.store = store

    def records(self) -> List[UsageRecord]:
        """The underlying billable-relevant records, in append order."""
        return self.store.read()

    # ------------------------------------------------------------------ #
    # Generic rollup engine
    # ------------------------------------------------------------------ #
    def rollup(
        self,
        window: str = WINDOW_DAY,
        dimensions: Tuple[str, ...] = (GROUP_TENANT,),
        start: Optional[str] = None,
        end: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[Tuple[str, ...], UsageAggregate]:
        """Aggregate billable records into per-bucket rows.

        ``dimensions`` are the grouping columns carried alongside the window
        bucket (any of tenant/agent/provider/model).  ``start``/``end`` bound
        the window bucket strings inclusively; ``tenant_id`` narrows to one
        tenant.  Returns ``{bucket, dim1, dim2, ...: UsageAggregate}``.
        """
        for dim in dimensions:
            if dim not in _DIMENSION_GETTERS:
                raise ValueError(f"unknown rollup dimension: {dim!r}")
        if window not in WINDOWS:
            raise ValueError(f"unknown rollup window: {window!r}")

        rows: Dict[Tuple[str, ...], UsageAggregate] = {}
        for record in self.records():
            if not record.billable:
                continue
            if tenant_id is not None and record.tenant_id != tenant_id:
                continue
            key = _bucket(record, window)
            if start is not None and key < start:
                continue
            if end is not None and key > end:
                continue
            parts: List[str] = [key]
            for dim in dimensions:
                parts.append(str(_DIMENSION_GETTERS[dim](record)))
            row_key = tuple(parts)
            agg = rows.get(row_key, _empty())
            rows[row_key] = UsageAggregate(
                calls=agg.calls + 1,
                cache_hits=agg.cache_hits + (1 if record.cache_hit else 0),
                input_tokens=agg.input_tokens + record.input_tokens,
                output_tokens=agg.output_tokens + record.output_tokens,
                cost_usd=agg.cost_usd + (record.cost_usd or 0.0),
                unmetered_calls=agg.unmetered_calls
                + (0 if record.metered else 1),
            )
        return rows

    # ------------------------------------------------------------------ #
    # Convenience API (tenant usage + internal billing)
    # ------------------------------------------------------------------ #
    def tenant_daily(
        self,
        tenant_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Dict[str, UsageAggregate]:
        """Per-day usage/cost for one tenant (the tenant usage API)."""
        rows = self.rollup(
            window=WINDOW_DAY,
            dimensions=(),
            start=start,
            end=end,
            tenant_id=tenant_id,
        )
        return {key[0]: agg for key, agg in rows.items()}

    def tenant_monthly(
        self,
        tenant_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Dict[str, UsageAggregate]:
        """Per-month usage/cost for one tenant."""
        rows = self.rollup(
            window=WINDOW_MONTH,
            dimensions=(),
            start=start,
            end=end,
            tenant_id=tenant_id,
        )
        return {key[0]: agg for key, agg in rows.items()}

    def by_tenant(self) -> Dict[str, UsageAggregate]:
        """Per-tenant cost attribution across all recorded usage."""
        return self._by_dimensions((GROUP_TENANT,))

    def by_agent(
        self, tenant_id: Optional[str] = None
    ) -> Dict[str, UsageAggregate]:
        """Per-agent cost attribution within a tenant (or across all).

        Keys are ``<tenant>::<agent>`` so agents never collide across
        tenants.  All recorded usage is aggregated (any time window).
        """
        return self._by_dimensions(
            (GROUP_TENANT, GROUP_AGENT), tenant_id=tenant_id
        )

    def by_model(self) -> Dict[str, UsageAggregate]:
        """Per-provider/model usage across all recorded usage (mix source)."""
        return self._by_dimensions((GROUP_PROVIDER, GROUP_MODEL))

    def _by_dimensions(
        self,
        dimensions: Tuple[str, ...],
        tenant_id: Optional[str] = None,
    ) -> Dict[str, UsageAggregate]:
        """Aggregate billable records by ``dimensions`` over all time."""
        rows: Dict[str, UsageAggregate] = {}
        for record in self.records():
            if not record.billable:
                continue
            if tenant_id is not None and record.tenant_id != tenant_id:
                continue
            parts = [str(_DIMENSION_GETTERS[d](record)) for d in dimensions]
            key = "::".join(parts)
            agg = rows.get(key, _empty())
            rows[key] = UsageAggregate(
                calls=agg.calls + 1,
                cache_hits=agg.cache_hits + (1 if record.cache_hit else 0),
                input_tokens=agg.input_tokens + record.input_tokens,
                output_tokens=agg.output_tokens + record.output_tokens,
                cost_usd=agg.cost_usd + (record.cost_usd or 0.0),
                unmetered_calls=agg.unmetered_calls
                + (0 if record.metered else 1),
            )
        return dict(sorted(rows.items()))

    def totals(self, tenant_id: Optional[str] = None) -> UsageAggregate:
        """Whole-store totals for one tenant (or across all tenants)."""
        rows = self.rollup(
            window=WINDOW_MONTH, dimensions=(), tenant_id=tenant_id
        )
        total = _empty()
        for agg in rows.values():
            total = UsageAggregate(
                calls=total.calls + agg.calls,
                cache_hits=total.cache_hits + agg.cache_hits,
                input_tokens=total.input_tokens + agg.input_tokens,
                output_tokens=total.output_tokens + agg.output_tokens,
                cost_usd=total.cost_usd + agg.cost_usd,
                unmetered_calls=total.unmetered_calls + agg.unmetered_calls,
            )
        return total

    # ------------------------------------------------------------------ #
    # Provider mix
    # ------------------------------------------------------------------ #
    def provider_mix(
        self, tenant_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Cost/call share per provider (percentages of the metered total).

        Only *metered, billable, provider-attributed* records are included:
        an unmetered call has no attributed cost and is never shown here as
        zero, and provider-less cache hits (surfaced via ``cache_hits`` in
        the totals) are not attributed to a provider.  Shares are computed
        over the included rows, so they always sum to ~100%.
        """
        rows: Dict[str, UsageAggregate] = {}
        for record in self.records():
            if not record.billable or not record.metered:
                continue
            if not record.provider:
                continue
            if tenant_id is not None and record.tenant_id != tenant_id:
                continue
            agg = rows.get(record.provider, _empty())
            rows[record.provider] = UsageAggregate(
                calls=agg.calls + 1,
                cache_hits=agg.cache_hits + (1 if record.cache_hit else 0),
                input_tokens=agg.input_tokens + record.input_tokens,
                output_tokens=agg.output_tokens + record.output_tokens,
                cost_usd=agg.cost_usd + (record.cost_usd or 0.0),
                unmetered_calls=agg.unmetered_calls,
            )
        total_cost = sum(a.cost_usd for a in rows.values())
        total_calls = sum(a.calls for a in rows.values())
        mix: Dict[str, Dict[str, Any]] = {}
        for provider, agg in sorted(rows.items()):
            mix[provider] = {
                "calls": agg.calls,
                "callSharePct": round(
                    (agg.calls / total_calls * 100.0) if total_calls else 0.0, 2
                ),
                "costUsd": round(agg.cost_usd, 8),
                "costSharePct": round(
                    (agg.cost_usd / total_cost * 100.0) if total_cost else 0.0, 2
                ),
            }
        return mix

    # ------------------------------------------------------------------ #
    # Billing / internal feed
    # ------------------------------------------------------------------ #
    def billing_summary(self) -> Dict[str, Any]:
        """The internal billing feed: totals + unmetered visibility."""
        rows = self.rollup(window=WINDOW_MONTH, dimensions=())
        non_billable = [r for r in self.records() if not r.billable]
        total = self.totals()
        return {
            "schemaVersion": 1,
            "window": "month",
            "billableCalls": total.calls,
            "cacheHits": total.cache_hits,
            "inputTokens": total.input_tokens,
            "outputTokens": total.output_tokens,
            "totalTokens": total.total_tokens,
            "costUsd": round(total.cost_usd, 8),
            "unmeteredCalls": total.unmetered_calls,
            "nonBillableEvents": len(non_billable),
            "monthBuckets": {
                key[0]: agg.to_dict() for key, agg in sorted(rows.items())
            },
        }
