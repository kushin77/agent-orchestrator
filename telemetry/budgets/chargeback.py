"""telemetry/budgets — per-tenant chargeback report generator (issue #34).

Produces the per-tenant chargeback lines for tenant billing from the
durable metering feed (issue #33) — calls, cache hits, tokens and estimated
cost per tenant per month — as deterministic CSV/JSON.  Adapted from
monitoring-stack's ``chargeback-report-generator.py`` (monthly per-tenant
chargeback from billing/usage data) and the observability usage feed
(issue #32), but fully offline over the metering rollups.

The generator is a *formatter* over ``UsageReporter`` rollups: it never
fabricates a cost figure and never makes a policy decision.  Cost is
whatever the metering cost engine attributed (a refused/unmetered call is
surfaced separately, never billed as zero).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ChargebackRow:
    """One per-tenant chargeback line (one tenant per month bucket)."""

    tenant_id: str
    month: str
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
            "tenantId": self.tenant_id,
            "month": self.month,
            "calls": self.calls,
            "cacheHits": self.cache_hits,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "costUsd": round(self.cost_usd, 8),
            "unmeteredCalls": self.unmetered_calls,
        }

    def to_csv_row(self) -> List[str]:
        return [
            self.tenant_id,
            self.month,
            str(self.calls),
            str(self.cache_hits),
            str(self.input_tokens),
            str(self.output_tokens),
            str(self.total_tokens),
            f"{self.cost_usd:.8f}",
            str(self.unmetered_calls),
        ]


CSV_HEADER = [
    "tenantId",
    "month",
    "calls",
    "cacheHits",
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "costUsd",
    "unmeteredCalls",
]


class ChargebackReportGenerator:
    """Per-tenant chargeback report over the issue-#33 metering rollups.

    ``reporter`` is a ``telemetry.metering.report.UsageReporter`` (or any
    object exposing ``by_tenant()`` and ``tenant_monthly(tenant)`` whose
    aggregates carry ``calls`` / ``cache_hits`` / ``input_tokens`` /
    ``output_tokens`` / ``cost_usd`` / ``unmetered_calls``).
    """

    def __init__(self, reporter: Any) -> None:
        self.reporter = reporter

    def tenants(self) -> List[str]:
        """Tenants with recorded billable usage (sorted)."""
        return sorted(self.reporter.by_tenant().keys())  # type: ignore[attr-defined]

    def report(
        self,
        *,
        month: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[ChargebackRow]:
        """One row per tenant-month (optionally filtered to one month/tenant).

        Months are the metering month buckets present in the store.  When a
        tenant has no usage in the store it produces no row — a chargeback
        line is only ever an honest sum over metered records.
        """
        rows: List[ChargebackRow] = []
        for tenant in self.tenants():
            if tenant_id is not None and tenant != tenant_id:
                continue
            monthly = self.reporter.tenant_monthly(tenant)  # type: ignore[attr-defined]
            for bucket in sorted(monthly):
                if month is not None and bucket != month:
                    continue
                agg = monthly[bucket]
                rows.append(
                    ChargebackRow(
                        tenant_id=tenant,
                        month=bucket,
                        calls=int(agg.calls),
                        cache_hits=int(agg.cache_hits),
                        input_tokens=int(agg.input_tokens),
                        output_tokens=int(agg.output_tokens),
                        cost_usd=float(agg.cost_usd),
                        unmetered_calls=int(agg.unmetered_calls),
                    )
                )
        return rows

    def totals(self) -> Dict[str, float]:
        """Whole-report totals (billing feed summary line)."""
        rows = self.report()
        total_cost = sum(r.cost_usd for r in rows)
        total_calls = sum(r.calls for r in rows)
        total_tokens = sum(r.total_tokens for r in rows)
        unmetered = sum(r.unmetered_calls for r in rows)
        return {
            "tenants": len({r.tenant_id for r in rows}),
            "calls": total_calls,
            "totalTokens": total_tokens,
            "costUsd": round(total_cost, 8),
            "unmeteredCalls": unmetered,
        }

    # ------------------------------------------------------------------ #
    def write_csv(self, path: Path, **kwargs: Any) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.report(**kwargs)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_HEADER)
            for row in rows:
                writer.writerow(row.to_csv_row())
        return path

    def write_json(self, path: Path, **kwargs: Any) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generatedAt": _now_utc(),
            "rows": [r.to_dict() for r in self.report(**kwargs)],
            "totals": self.totals(),
        }
        path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        return path


def _now_utc() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
