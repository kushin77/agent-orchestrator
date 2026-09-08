"""telemetry/budgets — state exporter tests (issue #34).

The machine-readable export feeds dashboards/alerting: kill-switch state,
per-tenant budget/quota positions and the SLO feed (CONSUMED from the
issue-#32 observability vocabulary via real ``SloResult`` objects).
"""

from __future__ import annotations

import json
from pathlib import Path

from telemetry.budgets.audit import MemoryAuditStore, record_decision
from telemetry.budgets.budget import BudgetEnforcer, BudgetLimit, TenantBudgetPolicy
from telemetry.budgets.exporter import BudgetStateExporter, slo_row
from telemetry.budgets.killswitch import KillSwitchController
from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import (
    MODE_ENFORCE,
    RESOURCE_REQUESTS,
    this_month_utc,
    today_utc,
)
from telemetry.budgets.quota import QuotaEnforcer, QuotaLimit, QuotaPolicy

DAY = today_utc()
MONTH = this_month_utc()


def _enforcers(ledger: StaticLedger):
    budget = BudgetEnforcer(
        ledger,
        {
            "acme": TenantBudgetPolicy(
                tenant_id="acme",
                mode=MODE_ENFORCE,
                cost_limit=BudgetLimit(window="month", limit=120.0, warn_at_pct=0.8),
            )
        },
    )
    quota = QuotaEnforcer(
        ledger,
        {
            "acme": QuotaPolicy(
                tenant_id="acme",
                limits={
                    RESOURCE_REQUESTS: QuotaLimit(
                        resource=RESOURCE_REQUESTS, window="day",
                        soft_limit=100, hard_limit=500,
                    )
                },
            )
        },
    )
    return budget, quota


def test_snapshot_kill_switch_state():
    audit = MemoryAuditStore()
    ks = KillSwitchController(audit=audit)
    ledger = StaticLedger()
    budget, quota = _enforcers(ledger)
    exporter = BudgetStateExporter(ledger, killswitch=ks, budget=budget,
                                   quota=quota, audit=audit)
    snap = exporter.snapshot()
    assert snap["killSwitch"]["globalPause"] is False


def test_snapshot_budget_position_exceeded():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)  # over the 120 cap
    budget, quota = _enforcers(ledger)
    exporter = BudgetStateExporter(ledger, budget=budget, quota=quota)
    state = exporter.tenant_budget_state("acme")
    assert state is not None
    cost = state["limits"]["costUsd"]
    assert cost["position"] == "exceeded"
    assert cost["limitUsd"] == 120.0


def test_snapshot_quota_status():
    ledger = StaticLedger()
    ledger.seed_calls("acme", DAY, 600)  # over hard 500
    budget, quota = _enforcers(ledger)
    exporter = BudgetStateExporter(ledger, budget=budget, quota=quota)
    state = exporter.tenant_quota_state("acme")
    assert state is not None
    assert state["resources"]["requests"]["status"] == "exceeded"
    assert state["resources"]["requests"]["hardLimit"] == 500.0


def test_snapshot_audit_summary():
    audit = MemoryAuditStore()
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)
    budget, quota = _enforcers(ledger)
    decision = budget.check("acme", requested_cost_usd=0.1, month=MONTH)
    record_decision(audit, decision)
    exporter = BudgetStateExporter(ledger, budget=budget, quota=quota, audit=audit)
    summary = exporter.audit_summary()
    assert summary["totalEvents"] == 1
    assert summary["byDecision"].get("block") == 1


def test_write_json_round_trip(tmp_path: Path):
    ledger = StaticLedger()
    budget, quota = _enforcers(ledger)
    exporter = BudgetStateExporter(ledger, budget=budget, quota=quota)
    out = exporter.write_json(tmp_path / "state.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "killSwitch" in payload
    assert "tenants" in payload
    assert "acme" in payload["tenants"]


# --------------------------------------------------------------------------- #
# SLO feed (consumed observability vocabulary)
# --------------------------------------------------------------------------- #
def _slo_result(verdict: str, kind: str = "availability", budget_used: float = 0.0):
    from telemetry.observability.slos import SloDefinition, SloResult

    if kind == "availability":
        definition = SloDefinition(
            name=f"tenant-availability", tenant_id="acme", kind=kind,
            window_seconds=3600, target_ratio=0.99,
        )
        return SloResult(
            definition=definition,
            window_start_iso="2026-09-08T09:00:00Z",
            window_end_iso="2026-09-08T10:00:00Z",
            verdict=verdict,
            has_window_data=True,
            attempts=100, good_count=99, bad_count=1,
            measured_ratio=0.99,
        )
    definition = SloDefinition(
        name="tenant-cost-budget", tenant_id="acme", kind="cost",
        window_seconds=86400, budget_usd=25.0,
    )
    return SloResult(
        definition=definition,
        window_start_iso="2026-09-07T00:00:00Z",
        window_end_iso="2026-09-08T00:00:00Z",
        verdict=verdict,
        has_window_data=True,
        attempts=10,
        spent_usd=budget_used,
        budget_consumed_ratio=budget_used / 25.0,
    )


def test_slo_row_from_real_slo_result():
    from telemetry.observability.slos import VERDICT_OK

    ok = _slo_result(VERDICT_OK)
    row = slo_row(ok)
    assert row["tenantId"] == "acme"
    assert row["verdict"] == "OK"
    assert row["isOk"] is True
    assert row["kind"] == "availability"


def test_slo_row_breached_not_ok():
    from telemetry.observability.slos import VERDICT_BREACHED

    breached = _slo_result(VERDICT_BREACHED, kind="cost", budget_used=30.0)
    row = slo_row(breached)
    assert row["verdict"] == "BREACHED"
    assert row["isOk"] is False
    assert row["budgetConsumedRatio"] == 30.0 / 25.0


def test_slo_row_from_serialized_dict():
    payload = {
        "tenantId": "acme", "slo": "tenant-cost-budget", "kind": "cost",
        "windowStart": "2026-09-07T00:00:00Z", "windowEnd": "2026-09-08T00:00:00Z",
        "verdict": "BREACHED", "missedWindow": False,
        "attempts": 10, "spentUsd": 30.0, "budgetConsumedRatio": 1.2,
        "target": 25.0,
    }
    row = slo_row(payload)
    assert row["verdict"] == "BREACHED"
    assert row["isOk"] is False
    assert row["target"] == 25.0


def test_snapshot_includes_slo_feed():
    from telemetry.observability.slos import VERDICT_OK, VERDICT_NO_DATA

    exporter = BudgetStateExporter(StaticLedger())
    snap = exporter.snapshot(slo_results=[_slo_result(VERDICT_OK)])
    assert len(snap["slos"]) == 1
    assert snap["slos"][0]["verdict"] == "OK"
