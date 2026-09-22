"""Per-tenant ERP usage and cost, rolled up from the platform metering feed.

Acceptance criterion 2 of issue #654: a roll-up produces **per-tenant ERP usage
+ cost**. The roll-up is a *reader* over the platform's usage store
(``telemetry.metering.report.UsageReporter``), reading only the records this
lane wrote (``provider == "erp"``). It owns no totals of its own and stores
nothing: the metering feed remains the single place a usage figure lives.

Three honesty rules are built in, and each one is a refusal rather than a
convention:

* **no fabricated zero.** A tenant with no ERP records has *no row* — not a row
  of zeros. ``for_tenant`` returns ``None``, and :meth:`ErpRollup.bill` refuses
  (``unmetered-usage``) rather than billing a zero it cannot evidence. This is
  the platform's own NO_DATA rule (``SpendLedger.has_data``, issue #341).
* **no billing of an unpriced operation.** A record with ``metered: False``
  means nothing priced that operation, so :meth:`ErpRollup.bill` refuses for
  that tenant-month instead of totalling the rest and quietly understating the
  bill. Its cost stays out of the figure entirely.
* **no cost published over a broken chain.** :meth:`ErpRollup.certify` verifies
  the tenant's audit chain (``telemetry.ledger``) before a cost is published;
  a chain that does not verify refuses (``ledger-unverified``). Usage that
  cannot be attributed to an intact audit trail is not a billable figure.

---knowledge---
module_id: integrations.erp.finops.rollup
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [TenantErpUsage, ErpRollup]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from telemetry.metering.report import UsageReporter
from telemetry.metering.model import month_bucket

from .model import Refused
from .usage import ERP_PROVIDER

__all__ = ["ErpRollup", "TenantErpUsage"]


@dataclass(frozen=True)
class TenantErpUsage:
    """One tenant's ERP usage in one month bucket.

    ``unmetered_operations`` is carried beside the cost rather than folded into
    it: a bill showing only a total cannot distinguish "this tenant was cheap"
    from "this tenant's operations were never priced", and those are different
    facts about the platform.
    """

    tenant_id: str
    month: str
    operations: int
    metered_operations: int
    unmetered_operations: int
    cost_usd: float
    by_kind: Tuple[Tuple[str, int], ...]
    by_operation: Tuple[Tuple[str, int], ...]

    @property
    def fully_metered(self) -> bool:
        """True when every operation in this bucket carried a price."""
        return self.unmetered_operations == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "month": self.month,
            "operations": self.operations,
            "meteredOperations": self.metered_operations,
            "unmeteredOperations": self.unmetered_operations,
            "costUsd": round(self.cost_usd, 8),
            "byKind": dict(self.by_kind),
            "byOperation": dict(self.by_operation),
        }


@dataclass
class ErpRollup:
    """The per-tenant ERP usage/cost roll-up over the platform metering feed."""

    reporter: UsageReporter

    # --- reads ------------------------------------------------------------ #

    def erp_records(self) -> List[Any]:
        """Every billable ERP record in the feed, in append order."""
        return [
            record
            for record in self.reporter.records()
            if getattr(record, "provider", None) == ERP_PROVIDER
            and bool(getattr(record, "billable", False))
        ]

    def months(self) -> Tuple[str, ...]:
        """The month buckets present in the ERP records (sorted)."""
        return tuple(sorted({month_bucket(record.ts) for record in self.erp_records()}))

    def usage(self, month: Optional[str] = None) -> Tuple[TenantErpUsage, ...]:
        """One row per tenant-month, sorted by (month, tenant).

        ``month`` narrows to a single bucket. A tenant with no records in the
        window produces no row at all.
        """
        grouped: Dict[Tuple[str, str], List[Any]] = {}
        for record in self.erp_records():
            bucket = month_bucket(record.ts)
            if month is not None and bucket != month:
                continue
            grouped.setdefault((bucket, record.tenant_id), []).append(record)

        rows: List[TenantErpUsage] = []
        for (bucket, tenant) in sorted(grouped):
            records = grouped[(bucket, tenant)]
            kinds: Dict[str, int] = {}
            operations: Dict[str, int] = {}
            cost = 0.0
            metered = 0
            for record in records:
                kinds[str(record.model)] = kinds.get(str(record.model), 0) + 1
                key = str(record.route)
                operations[key] = operations.get(key, 0) + 1
                if record.metered:
                    metered += 1
                    cost += float(record.cost_usd or 0.0)
            rows.append(
                TenantErpUsage(
                    tenant_id=tenant,
                    month=bucket,
                    operations=len(records),
                    metered_operations=metered,
                    unmetered_operations=len(records) - metered,
                    cost_usd=round(cost, 8),
                    by_kind=tuple(sorted(kinds.items())),
                    by_operation=tuple(sorted(operations.items())),
                )
            )
        return tuple(rows)

    def for_tenant(self, tenant: str, month: Optional[str] = None) -> Optional[TenantErpUsage]:
        """One tenant's row, or ``None`` when the feed holds nothing for it.

        ``None`` is the NO_DATA answer, and it is deliberately not a zero row:
        a surface that prints ``0.00`` for an unmetered tenant claims a
        measurement it does not have.
        """
        for row in self.usage(month=month):
            if row.tenant_id == tenant:
                return row
        return None

    def totals(self) -> Dict[str, Any]:
        """Whole-feed ERP totals (the FinOps summary line)."""
        rows = self.usage()
        return {
            "tenants": len({row.tenant_id for row in rows}),
            "operations": sum(row.operations for row in rows),
            "unmeteredOperations": sum(row.unmetered_operations for row in rows),
            "costUsd": round(sum(row.cost_usd for row in rows), 8),
        }

    # --- the two refusals ------------------------------------------------ #

    def bill(self, tenant: str, month: Optional[str] = None) -> TenantErpUsage:
        """The tenant's billable row, refusing anything not fully priced.

        Refuses ``unmetered-usage`` when the feed holds nothing for the tenant
        (nothing to bill and no evidence of a zero) or when any of its ERP
        operations was never priced (a total over the priced subset would be a
        smaller number presented as the whole bill).
        """
        row = self.for_tenant(tenant, month=month)
        if row is None:
            raise Refused(
                "unmetered-usage",
                f"{tenant} has no metered ERP usage"
                + (f" in {month}" if month else ""),
                where=tenant,
            )
        if row.unmetered_operations:
            raise Refused(
                "unmetered-usage",
                f"{tenant} has {row.unmetered_operations} of {row.operations} ERP "
                f"operation(s) with no published price, so no cost can be billed",
                where=tenant,
            )
        return row

    def certify(self, audit: Any, tenant: str) -> Tuple[int, str]:
        """Verify the tenant's audit chain before its cost is published.

        Returns the verified ``(seq, hash)`` tail. A chain that is not OK — or
        that cannot be assessed — refuses ``ledger-unverified``: a cost whose
        underlying operations cannot be shown on an intact append-only trail is
        not a figure this lane will hand to billing.
        """
        verdict = audit.verify(tenant)
        if verdict.status != "OK":
            raise Refused(
                "ledger-unverified",
                f"{tenant}: the audit chain is {verdict.status}"
                + (f" ({verdict.detail})" if verdict.detail else ""),
                where=tenant,
            )
        return audit.tail(tenant)
