"""telemetry/budgets — spend ledger protocol + metering-feed adapter (issue #34).

The budget/quota enforcers must never keep a per-process counter — spend has
to survive restarts and be shared across instances pointing at the same
store.  The durable source of truth is the metering lane's append-only usage
store (issue #33); this module defines the narrow **spend ledger** the
enforcers read from, plus a ``MeteringReporterLedger`` adapter that consumes
the issue-#33 ``UsageReporter`` rollups verbatim.

Consumed vocabulary (never redefined):

- ``UsageReporter.tenant_daily`` -> ``{YYYY-MM-DD: UsageAggregate}`` and
  ``UsageReporter.tenant_monthly`` -> ``{YYYY-MM: UsageAggregate}``, where
  each ``UsageAggregate`` carries ``calls`` / ``total_tokens`` /
  ``cost_usd`` (issue #33).  Rollups count only *billable* records, so a
  refused call never inflates spend — a refused call is not usage.
- the per-tenant/per-provider rollup (``UsageReporter.rollup`` with
  ``(tenant, provider)`` dimensions) supplies per-vendor monthly spend for
  the per-vendor budget caps.
"""

from __future__ import annotations

from typing import Optional, Protocol

from telemetry.budgets.model import (
    day_bucket,
    month_bucket,
    this_month_utc,
    today_utc,
)


class SpendLedger(Protocol):
    """Durable current-usage source the enforcers read (never per-process).

    The enforcer checks are pure decisions over these figures; a caller
    wires real state by supplying any object implementing this protocol
    (the metering-feed adapter below is the canonical one).
    """

    def monthly_cost(self, tenant_id: str, month: Optional[str] = None) -> float:
        """Total billable estimated cost (USD) for ``tenant_id`` in ``month``."""

    def daily_cost(self, tenant_id: str, day: Optional[str] = None) -> float:
        """Total billable estimated cost (USD) for ``tenant_id`` on ``day``."""

    def daily_tokens(self, tenant_id: str, day: Optional[str] = None) -> int:
        """Billable tokens consumed by ``tenant_id`` on ``day`` (UTC)."""

    def daily_calls(self, tenant_id: str, day: Optional[str] = None) -> int:
        """Billable calls dispatched for ``tenant_id`` on ``day`` (UTC)."""

    def vendor_monthly_cost(
        self,
        tenant_id: str,
        vendor: str,
        month: Optional[str] = None,
    ) -> float:
        """Billable estimated cost (USD) for one vendor, one tenant, one month."""

    def has_data(self, tenant_id: str) -> bool:
        """Whether the ledger holds any billable record for ``tenant_id``.

        The no-data honesty probe (issue #341): a ledger that has never seen a
        record for a tenant cannot distinguish "no spend" from "not metered",
        so a spend surface must report NO_DATA instead of a measured zero.
        The counterpart of a ledger that simply reads 0 is a tenant whose
        records really do sum to zero (e.g. cache hits or local models only).
        """


class StaticLedger:
    """A dict-backed ledger for unit tests and offline seeding.

    ``costs`` maps ``(tenant_id, month)`` -> USD, ``daily_costs`` maps
    ``(tenant_id, day)`` -> USD, ``tokens`` maps ``(tenant_id, day)`` -> int,
    ``calls`` maps ``(tenant_id, day)`` -> int, ``vendor_costs`` maps
    ``(tenant_id, vendor, month)`` -> USD.  Missing keys read as zero (an
    empty ledger is honest: no spend yet).
    """

    def __init__(
        self,
        costs: Optional[dict[tuple[str, str], float]] = None,
        daily_costs: Optional[dict[tuple[str, str], float]] = None,
        tokens: Optional[dict[tuple[str, str], int]] = None,
        calls: Optional[dict[tuple[str, str], int]] = None,
        vendor_costs: Optional[dict[tuple[str, str, str], float]] = None,
    ) -> None:
        self._costs = dict(costs or {})
        self._daily_costs = dict(daily_costs or {})
        self._tokens = dict(tokens or {})
        self._calls = dict(calls or {})
        self._vendor_costs = dict(vendor_costs or {})

    def seed_cost(self, tenant_id: str, month: str, cost_usd: float) -> None:
        self._costs[(tenant_id, month)] = float(cost_usd)

    def seed_daily_cost(self, tenant_id: str, day: str, cost_usd: float) -> None:
        self._daily_costs[(tenant_id, day)] = float(cost_usd)

    def seed_tokens(self, tenant_id: str, day: str, tokens: int) -> None:
        self._tokens[(tenant_id, day)] = int(tokens)

    def seed_calls(self, tenant_id: str, day: str, calls: int) -> None:
        self._calls[(tenant_id, day)] = int(calls)

    def seed_vendor_cost(
        self, tenant_id: str, vendor: str, month: str, cost_usd: float
    ) -> None:
        self._vendor_costs[(tenant_id, vendor, month)] = float(cost_usd)

    def monthly_cost(self, tenant_id: str, month: Optional[str] = None) -> float:
        return float(self._costs.get((tenant_id, month or this_month_utc()), 0.0))

    def daily_cost(self, tenant_id: str, day: Optional[str] = None) -> float:
        return float(self._daily_costs.get((tenant_id, day or today_utc()), 0.0))

    def daily_tokens(self, tenant_id: str, day: Optional[str] = None) -> int:
        return int(self._tokens.get((tenant_id, day or today_utc()), 0))

    def daily_calls(self, tenant_id: str, day: Optional[str] = None) -> int:
        return int(self._calls.get((tenant_id, day or today_utc()), 0))

    def vendor_monthly_cost(
        self,
        tenant_id: str,
        vendor: str,
        month: Optional[str] = None,
    ) -> float:
        return float(
            self._vendor_costs.get(
                (tenant_id, vendor, month or this_month_utc()), 0.0
            )
        )

    def has_data(self, tenant_id: str) -> bool:
        """True when any seeded figure exists for the tenant."""
        keys = (
            list(self._costs)
            + list(self._daily_costs)
            + list(self._tokens)
            + list(self._calls)
        )
        if any(key[0] == tenant_id for key in keys):
            return True
        return any(key[0] == tenant_id for key in self._vendor_costs)


class MeteringReporterLedger:
    """Spend ledger adapter over the issue-#33 metering feed.

    Wraps a ``telemetry.metering.report.UsageReporter`` (the durable rollup
    reader) and reads the same figures the metering budget toggle reads —
    cross-instance durable, correct after restarts.  ``UsageReporter`` is
    imported only for typing; the adapter works with any object exposing
    ``tenant_daily`` / ``tenant_monthly`` / ``rollup``.
    """

    def __init__(self, reporter: object) -> None:
        self._reporter = reporter

    @property
    def reporter(self) -> object:
        return self._reporter

    def monthly_cost(self, tenant_id: str, month: Optional[str] = None) -> float:
        """Billable estimated cost for one tenant in a UTC month bucket."""
        month = month or this_month_utc()
        rows = self._reporter.tenant_monthly(tenant_id)  # type: ignore[attr-defined]
        total = 0.0
        for bucket, agg in rows.items():
            if bucket == month:
                total += float(agg.cost_usd)
        return total

    def daily_cost(self, tenant_id: str, day: Optional[str] = None) -> float:
        """Billable estimated cost for one tenant on a UTC day bucket."""
        day = day or today_utc()
        rows = self._reporter.tenant_daily(tenant_id)  # type: ignore[attr-defined]
        total = 0.0
        for bucket, agg in rows.items():
            if bucket == day:
                total += float(agg.cost_usd)
        return total

    def daily_tokens(self, tenant_id: str, day: Optional[str] = None) -> int:
        """Billable tokens for one tenant on a UTC day bucket."""
        day = day or today_utc()
        rows = self._reporter.tenant_daily(tenant_id)  # type: ignore[attr-defined]
        total = 0
        for bucket, agg in rows.items():
            if bucket == day:
                total += int(agg.total_tokens)
        return total

    def daily_calls(self, tenant_id: str, day: Optional[str] = None) -> int:
        """Billable calls for one tenant on a UTC day bucket."""
        day = day or today_utc()
        rows = self._reporter.tenant_daily(tenant_id)  # type: ignore[attr-defined]
        total = 0
        for bucket, agg in rows.items():
            if bucket == day:
                total += int(agg.calls)
        return total

    def vendor_monthly_cost(
        self,
        tenant_id: str,
        vendor: str,
        month: Optional[str] = None,
    ) -> float:
        """Billable cost for one tenant+vendor in a UTC month bucket.

        Reads the issue-#33 ``UsageReporter.rollup`` with ``(tenant,
        provider)`` dimensions, filtered to ``tenant_id``.
        """
        month = month or this_month_utc()
        rows = self._reporter.rollup(  # type: ignore[attr-defined]
            window="month",
            dimensions=("tenant", "provider"),
            tenant_id=tenant_id,
        )
        total = 0.0
        for key, agg in rows.items():
            # key == (bucket, tenant_id, provider)
            if len(key) >= 3 and key[0] == month and key[2] == vendor:
                total += float(agg.cost_usd)
        return total

    def has_data(self, tenant_id: str) -> bool:
        """True when the metering feed holds any billable record for the tenant.

        Reads the durable feed itself (not a rollup figure), so a tenant whose
        records are entirely unpriced still reads as *data-bearing* — the
        caller decides how to present an unknown cost, but it must never be
        presented as "no spend".
        """
        records = getattr(self._reporter, "records", None)
        if callable(records):
            return any(
                getattr(record, "tenant_id", None) == tenant_id
                and bool(getattr(record, "billable", False))
                for record in records()
            )
        rows = self._reporter.tenant_daily(tenant_id)  # type: ignore[attr-defined]
        return bool(rows)


# Re-exported bucket helpers so callers/tests share one import path.
__all__ = [
    "SpendLedger",
    "StaticLedger",
    "MeteringReporterLedger",
    "day_bucket",
    "month_bucket",
]
