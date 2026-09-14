"""portal.server.finops — the FinOps single-pane report adapter (issue #341).

WHY this exists: the metering lane (``telemetry/metering``) knows what was
spent and on what tiers, and the budgets lane (``telemetry/budgets``) knows the
limits, the quotas, the chargeback lines and the alert thresholds — but both
are offline libraries. A tenant admin cannot open a library. This module is the
*server half* of the FinOps single-pane: it exposes those lanes' own reports
over the console's HTTP surface, per tenant and per agent.

Cannibalize, do not duplicate. Every figure is read through the lane that owns
it:

* the current tenant/budget/quota policy — ``livestore.TelemetrySnapshot``
  (the console's one live-telemetry seam, issue #348);
* usage, cost attribution and per-agent/per-model rollups —
  ``telemetry.metering.report.UsageReporter`` over the durable metering feed;
* the cost breakdown and its pricing tiers —
  ``telemetry.metering.cost_details`` (standard / longContext / local);
* budget enforcement, positions and thresholds —
  ``telemetry.budgets.budget`` / ``.exporter`` / ``.ledger``;
* the spend alerts and their warning/alert thresholds —
  ``telemetry.budgets.alerts``;
* the per-tenant chargeback lines — ``telemetry.budgets.chargeback``.

Nothing here re-implements a reader or a threshold; the adapter owns transport
shape and the honesty rules of the pane:

* **no data is not zero.** A tenant the feed has never metered reports
  ``costUsd: null`` and a ``NO_DATA`` verdict, never a fabricated ``0.0`` that
  would read as "no spend, all good". A tenant whose records are metered and
  genuinely sum to zero (cache hits, local models) reports ``0.0`` with
  ``costKnown: true`` — that zero *is* measured.
* **an incomplete cost is never a total.** When any call could not be priced,
  the breakdown reports ``costComplete: false`` and names the unpriced models;
  ``costUsd`` covers only the priced calls and is labelled as such.
* **per-agent spend is attributed, not invented.** The telemetry lane declares
  limits per tenant (and per vendor), so an agent row carries its attributed
  share of the tenant budget — never a fabricated per-agent limit.

The surface ships **feature-flag-gated OFF** (GR-5): the flag is declared in
``infra/feature-flags/registry.yaml`` under ``surfaces`` and read here through
the same reader the fleet projection uses (``portal.server.fleet``); while it
is off the app refuses every ``/api/finops/*`` route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from portal.server.fleet import read_surface_default, surface_enabled
from portal.server.livestore import DEFAULT_USAGE_STORE, TelemetrySnapshot
from telemetry.budgets.alerts import SpendAlertEvaluator
from telemetry.budgets.budget import (
    BudgetEnforcer,
    TenantBudgetPolicy,
    load_budget_policies,
)
from telemetry.budgets.chargeback import ChargebackReportGenerator
from telemetry.budgets.exporter import BudgetStateExporter
from telemetry.budgets.ledger import MeteringReporterLedger
from telemetry.budgets.model import now_utc_iso, this_month_utc, today_utc
from telemetry.metering.cost_details import (
    CostModel,
    breakdown_for,
    usage_details_for,
)
from telemetry.metering.model import UsageRecord
from telemetry.metering.ratecards import RateCardStore
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import JsonlUsageStore

#: The registry surface key that gates this endpoint family.
FINOPS_SURFACE = "finops_reports"
#: The flag declaration read at boot (repo-root relative).
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"
#: Where the metering lane's rate cards live (repo-root relative).
RATE_CARDS_RELATIVE = Path("telemetry") / "metering" / "rate_cards"
#: Where the budgets lane's per-tenant policies live (repo-root relative).
POLICIES_RELATIVE = Path("telemetry") / "budgets" / "config" / "policies.yaml"


class FinOpsReports:
    """Projects the metering + budgets lanes' reports over HTTP (issue #341).

    ``enabled`` is resolved from the feature-flag registry unless supplied
    explicitly (tests pass it; the server lets the registry decide). Every
    reader is built lazily on first use, so a flag-OFF surface costs the
    process nothing but the registry read.

    ``usage_store_path`` points the pane at a specific durable metering feed
    (the repo's own runtime store by default). ``day``/``month`` pin the
    alerting buckets (``YYYY-MM-DD`` / ``YYYY-MM``) so an offline check is
    reproducible; a live server leaves them unset and the current UTC
    day/month apply.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        registry_path: Optional[Path | str] = None,
        usage_store_path: Optional[Path | str] = None,
        policies_path: Optional[Path | str] = None,
        day: Optional[str] = None,
        month: Optional[str] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.registry_path = (
            Path(registry_path) if registry_path is not None else None
        )
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                registry_path=self.registry_path,
                surface=FINOPS_SURFACE,
            )
        self.enabled = bool(enabled)
        self.usage_store_path = (
            Path(usage_store_path)
            if usage_store_path is not None
            else self.repo_root / DEFAULT_USAGE_STORE
        )
        self.policies_path = (
            Path(policies_path)
            if policies_path is not None
            else self.repo_root / POLICIES_RELATIVE
        )
        self.day = day
        self.month = month
        self._clock = clock
        self._telemetry: Optional[TelemetrySnapshot] = None
        self._reporter: Optional[UsageReporter] = None
        self._cost_model: Optional[CostModel] = None
        self._policies: Optional[Dict[str, TenantBudgetPolicy]] = None
        self._ledger: Optional[MeteringReporterLedger] = None
        self._alerts: Optional[SpendAlertEvaluator] = None
        self._enforcer: Optional[BudgetEnforcer] = None
        self._chargeback: Optional[ChargebackReportGenerator] = None

    # -- lazily built readers ----------------------------------------------
    @property
    def telemetry(self) -> TelemetrySnapshot:
        """The live telemetry policy view (budget/quota/plan/mode)."""
        if self._telemetry is None:
            self._telemetry = TelemetrySnapshot(
                self.repo_root, usage_store_path=self.usage_store_path
            )
        return self._telemetry

    @property
    def reporter(self) -> UsageReporter:
        """The metering lane's durable rollup reader over the live feed."""
        if self._reporter is None:
            self._reporter = UsageReporter(JsonlUsageStore(self.usage_store_path))
        return self._reporter

    @property
    def cost_model(self) -> CostModel:
        """The rate-card cost model that prices calls by tier."""
        if self._cost_model is None:
            cards = RateCardStore.load_dir(self.repo_root / RATE_CARDS_RELATIVE)
            self._cost_model = CostModel(cards)
        return self._cost_model

    @property
    def policies(self) -> Dict[str, TenantBudgetPolicy]:
        """The budgets lane's declared per-tenant policies."""
        if self._policies is None:
            self._policies = load_budget_policies(self.policies_path)
        return self._policies

    @property
    def ledger(self) -> MeteringReporterLedger:
        """Durable spend read off the metering feed (never a process counter)."""
        if self._ledger is None:
            self._ledger = MeteringReporterLedger(self.reporter)
        return self._ledger

    @property
    def alerts(self) -> SpendAlertEvaluator:
        """The spend-alert evaluator (warning/alert thresholds, NO_DATA path)."""
        if self._alerts is None:
            self._alerts = SpendAlertEvaluator(self.ledger, self.policies)
        return self._alerts

    @property
    def enforcer(self) -> BudgetEnforcer:
        """The budget enforcer (soft/hard caps, warn/block ladder)."""
        if self._enforcer is None:
            self._enforcer = BudgetEnforcer(self.ledger, self.policies)
        return self._enforcer

    @property
    def chargeback(self) -> ChargebackReportGenerator:
        """The per-tenant chargeback report generator."""
        if self._chargeback is None:
            self._chargeback = ChargebackReportGenerator(self.reporter)
        return self._chargeback

    @property
    def exporter(self) -> BudgetStateExporter:
        """The budgets lane's machine-readable state exporter."""
        return BudgetStateExporter(self.ledger, budget=self.enforcer)

    # -- universe -----------------------------------------------------------
    def tenant_ids(self) -> List[str]:
        """Tenants the live stores know: declared policies + metered tenants.

        Deliberately *live*: a tenant appears here because the budgets lane
        declares a policy for it or the feed has metered it — never because a
        list in this module says so.
        """
        ids = set(self.policies)
        ids.update(record.tenant_id for record in self.records())
        return sorted(tenant for tenant in ids if tenant)

    def records(self) -> List[UsageRecord]:
        """Every record in the durable metering feed (one read)."""
        return self.reporter.records()

    def _tenant_records(self, tenant_id: str) -> List[UsageRecord]:
        """The tenant's billable records (the ones that are usage at all)."""
        return [
            record
            for record in self.records()
            if record.tenant_id == tenant_id and record.billable
        ]

    def known_tenant(self, tenant_id: str) -> bool:
        """Whether the live stores know this tenant (404 gate for the route)."""
        return tenant_id in self.tenant_ids()

    def _now_iso(self) -> str:
        if self._clock is not None:
            return self._clock()
        return now_utc_iso()

    def _buckets(self) -> Dict[str, str]:
        return {
            "month": self.month or this_month_utc(),
            "day": self.day or today_utc(),
        }

    # -- reads --------------------------------------------------------------
    def overview(self, tenant_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Per-tenant FinOps summary: cost, budget, utilization, verdict.

        ``tenant_ids`` narrows the pane to the tenants the caller may read
        (the route passes the principal's scoped, permitted set).
        """
        universe = set(self.tenant_ids())
        wanted = sorted(universe) if tenant_ids is None else [
            tenant for tenant in tenant_ids if tenant in universe
        ]
        records = self.records()
        rows: List[Dict[str, Any]] = []
        fired: List[Dict[str, Any]] = []
        for tenant_id in wanted:
            rows.append(self._summary(tenant_id, records))
            state = self.alerts.evaluate(
                tenant_id, day=self.day, month=self.month
            )
            fired.extend(alert.to_dict() for alert in state.fired)
        return {
            "generatedAt": self._now_iso(),
            "window": self._buckets(),
            "currency": "USD",
            "tenants": rows,
            "firedAlerts": fired,
        }

    def report(self, tenant_id: str) -> Dict[str, Any]:
        """The single-pane report for one tenant (the acceptance surface)."""
        records = self._tenant_records(tenant_id)
        cost = self._cost_state(records)
        breakdown = breakdown_for(records, self.cost_model)
        budget = self.policies.get(tenant_id)
        quota = self._quota_state(tenant_id, records)
        alerts = self.alerts.evaluate(tenant_id, day=self.day, month=self.month)
        return {
            "generatedAt": self._now_iso(),
            "currency": "USD",
            "window": self._buckets(),
            "tenantId": tenant_id,
            "plan": self.telemetry.plan(tenant_id),
            "mode": self.telemetry.mode(tenant_id) or (budget.mode if budget else ""),
            "dataQuality": self._data_quality(cost, breakdown),
            "usage": {
                "calls": cost["calls"],
                "cacheHits": cost["cacheHits"],
                "unmeteredCalls": cost["unmeteredCalls"],
                "tokens": cost["tokens"],
                "usageDetails": usage_details_for(records),
            },
            "cost": {
                "attributedCostUsd": cost["costUsd"],
                "costKnown": cost["costKnown"],
                "breakdown": breakdown.to_dict(),
                "rateCards": self._rate_card_state(records),
            },
            "budget": self._budget_state(tenant_id),
            "quotas": quota,
            "agents": self._agent_rows(tenant_id, records),
            "models": self._model_rows(tenant_id, records),
            "chargeback": [row.to_dict() for row in self.chargeback.report(
                tenant_id=tenant_id
            )],
            "alerts": alerts.to_dict(),
        }

    def alerts_report(
        self, tenant_ids: Optional[Sequence[str]] = None
    ) -> Dict[str, Any]:
        """The machine-readable spend-alert feed.

        ``tenant_ids`` narrows the feed to the tenants the caller may read (the
        route passes the principal's scoped, permitted set); ``None`` covers
        every tenant the budgets lane declares a policy for.
        """
        tenants = (
            sorted(self.policies) if tenant_ids is None else list(tenant_ids)
        )
        states = [
            self.alerts.evaluate(tenant, day=self.day, month=self.month)
            for tenant in tenants
        ]
        return {
            "generatedAt": self._now_iso(),
            "window": self._buckets(),
            "fired": [
                alert.to_dict()
                for state in states
                for alert in state.fired
            ],
            "tenants": [state.to_dict() for state in states],
        }

    # -- building blocks ----------------------------------------------------
    @staticmethod
    def _cost_state(records: Sequence[UsageRecord]) -> Dict[str, Any]:
        """Measured usage/cost of a record set, with the honesty flags.

        ``costUsd`` is ``None`` — never ``0.0`` — when no record was priced:
        an unmetered tenant, and a tenant whose calls are all unpriced, have an
        *unknown* cost. ``costKnown`` is True only when every billable record
        was metered, so a partial figure can never pass as the total.
        """
        metered = [record for record in records if record.metered]
        unmetered = len(records) - len(metered)
        return {
            "calls": len(records),
            "cacheHits": sum(1 for record in records if record.cache_hit),
            "unmeteredCalls": unmetered,
            "tokens": sum(record.total_tokens for record in records),
            "costUsd": (
                round(sum(float(record.cost_usd or 0.0) for record in metered), 8)
                if metered
                else None
            ),
            "costKnown": bool(metered) and unmetered == 0,
        }

    @staticmethod
    def _data_quality(
        cost: Dict[str, Any], breakdown: Any
    ) -> Dict[str, Any]:
        """Why the pane's figures are (or are not) complete."""
        reasons: List[str] = []
        if cost["calls"] == 0:
            reasons.append(
                "no billable usage record for this tenant in the durable "
                "metering feed: spend is unknown, not zero"
            )
        elif cost["unmeteredCalls"]:
            reasons.append(
                f"{cost['unmeteredCalls']} of {cost['calls']} billable calls "
                "were unpriced: cost figures cover the priced calls only"
            )
        return {
            "hasData": cost["calls"] > 0,
            "costKnown": cost["costKnown"],
            "costComplete": breakdown.cost_complete,
            "unpricedModels": list(breakdown.unpriced_models),
            "reasons": reasons,
        }

    def _summary(
        self, tenant_id: str, records: Sequence[UsageRecord]
    ) -> Dict[str, Any]:
        """One overview row: measured cost vs the tenant's declared cap."""
        tenant_records = [
            record
            for record in records
            if record.tenant_id == tenant_id and record.billable
        ]
        cost = self._cost_state(tenant_records)
        policy = self.policies.get(tenant_id)
        limit = policy.cost_limit if policy else None
        state = self.alerts.evaluate(tenant_id, day=self.day, month=self.month)
        return {
            "tenantId": tenant_id,
            "plan": self.telemetry.plan(tenant_id),
            "mode": state.mode,
            "hasData": cost["calls"] > 0,
            "costUsd": cost["costUsd"],
            "costKnown": cost["costKnown"],
            "budgetUsd": float(limit.limit) if limit else None,
            "cap": limit.cap if limit else None,
            "utilizationPct": (
                round(100.0 * float(cost["costUsd"]) / float(limit.limit), 2)
                if limit and cost["costUsd"] is not None
                else None
            ),
            "calls": cost["calls"],
            "tokens": cost["tokens"],
            "verdict": state.verdict,
            "firedAlerts": len(state.fired),
        }

    def _budget_state(self, tenant_id: str) -> Dict[str, Any]:
        """The tenant's declared limits, thresholds and cap semantics.

        Reads the budgets lane's own policy objects and its exporter formula —
        this adapter declares no threshold of its own.
        """
        policy = self.policies.get(tenant_id)
        exported = self.exporter.tenant_budget_state(tenant_id)
        if policy is None:
            return {
                "configured": False,
                "reason": "no budget policy declared for this tenant",
                "exporter": exported,
            }
        return {
            "configured": True,
            "mode": policy.mode,
            "costLimit": (
                policy.cost_limit.to_dict() if policy.cost_limit else None
            ),
            "tokenLimit": (
                policy.token_limit.to_dict() if policy.token_limit else None
            ),
            "vendorCaps": [cap.to_dict() for cap in policy.vendor_caps],
            "exporter": exported,
        }

    def _quota_state(
        self, tenant_id: str, records: Sequence[UsageRecord]
    ) -> Dict[str, Any]:
        """Declared quota (plan defaults + overrides) beside live measured use.

        ``requests``/``tokens`` usage is measured by the metering feed;
        ``concurrency``/``storage`` have no probe in this lane, so they are
        reported as unmeasured rather than as an invented zero.
        """
        declared = self.telemetry.effective_quota(tenant_id)
        calls = len(records)
        tokens = sum(record.total_tokens for record in records)
        measured = {"requests": calls, "tokens": tokens}
        rows: Dict[str, Any] = {}
        for resource, spec in declared.items():
            rows[resource] = {
                "softLimit": spec.get("softLimit"),
                "hardLimit": spec.get("hardLimit"),
                "window": spec.get("window"),
                "used": measured.get(resource),
                "measured": resource in measured,
            }
        return {"plan": self.telemetry.plan(tenant_id), "resources": rows}

    def _rate_card_state(self, records: Sequence[UsageRecord]) -> Dict[str, Any]:
        """The rate cards behind the prices: providers + a change fingerprint."""
        cards = self.cost_model.cards
        return {
            "providers": cards.providers(),
            "fingerprint": cards.fingerprint(),
            "pricedModels": sorted(
                {
                    f"{record.provider}/{record.model}"
                    for record in records
                    if record.provider and record.model
                }
            ),
        }

    def _agent_rows(
        self, tenant_id: str, records: Sequence[UsageRecord]
    ) -> List[Dict[str, Any]]:
        """Per-agent cost/budget/usage rows (attributed, never invented)."""
        return self._group_rows(
            records,
            key=lambda record: record.agent_id or "",
            tenant_id=tenant_id,
            id_key="agentId",
        )

    def _model_rows(
        self, tenant_id: str, records: Sequence[UsageRecord]
    ) -> List[Dict[str, Any]]:
        """Per-provider/model usage rows, with their pricing tiers."""
        return self._group_rows(
            records,
            key=lambda record: f"{record.provider or ''}/{record.model or ''}",
            tenant_id=tenant_id,
            id_key="model",
        )

    def _group_rows(
        self,
        records: Sequence[UsageRecord],
        *,
        key: Callable[[UsageRecord], str],
        tenant_id: str,
        id_key: str,
    ) -> List[Dict[str, Any]]:
        """Group billable records, price each group, attribute its cost share.

        Sorted by attributed cost (descending) so the pane's first row is the
        biggest spender; a group with an unknown cost sorts last but is never
        shown as ``0.0``.
        """
        groups: Dict[str, List[UsageRecord]] = {}
        for record in records:
            groups.setdefault(key(record), []).append(record)
        policy = self.policies.get(tenant_id)
        limit = float(policy.cost_limit.limit) if policy and policy.cost_limit else None
        tenant_cost = self._cost_state(records)["costUsd"]
        rows: List[Dict[str, Any]] = []
        for group_id, group in groups.items():
            cost = self._cost_state(group)
            breakdown = breakdown_for(group, self.cost_model)
            rows.append(
                {
                    id_key: group_id,
                    "usageDetails": usage_details_for(group),
                    "calls": cost["calls"],
                    "tokens": cost["tokens"],
                    "cacheHits": cost["cacheHits"],
                    "unmeteredCalls": cost["unmeteredCalls"],
                    "costUsd": cost["costUsd"],
                    "costKnown": cost["costKnown"],
                    "costDetails": breakdown.to_dict(),
                    "shareOfTenantCostPct": (
                        round(100.0 * float(cost["costUsd"]) / float(tenant_cost), 2)
                        if cost["costUsd"] is not None and tenant_cost
                        else None
                    ),
                    "attributedBudgetPct": (
                        round(100.0 * float(cost["costUsd"]) / limit, 2)
                        if cost["costUsd"] is not None and limit
                        else None
                    ),
                }
            )
        rows.sort(
            key=lambda row: (
                -(row["costUsd"] if row["costUsd"] is not None else -1.0),
                str(row[id_key]),
            )
        )
        return rows
