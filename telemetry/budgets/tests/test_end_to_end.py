"""telemetry/budgets — end-to-end enforcement over the real metering feed (#34).

The full-loop negative the acceptance criteria demand, exercised against the
actual issue-#33 metering feed:

- ingest real model-call records -> durable rollups (issue #33);
- the budget enforcer reads current spend off that feed and **rejects a call
  whose projected cost would exceed the tenant budget** (never silent);
- the kill switch refuses a normally-allowed call the instant it is ON;
- quota/audit/export/chargeback all compose on the same store.
"""

from __future__ import annotations

from telemetry.budgets.audit import MemoryAuditStore, record_decision
from telemetry.budgets.budget import BudgetEnforcer, BudgetLimit, TenantBudgetPolicy
from telemetry.budgets.chargeback import ChargebackReportGenerator
from telemetry.budgets.exporter import BudgetStateExporter
from telemetry.budgets.killswitch import KillSwitchController
from telemetry.budgets.ledger import MeteringReporterLedger
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_REFUSE,
    DECISION_WARN,
    MODE_ENFORCE,
    OUTCOME_BUDGET_EXCEEDED,
    RESOURCE_REQUESTS,
)
from telemetry.budgets.quota import QuotaEnforcer, QuotaLimit, QuotaPolicy
from conftest import MONTH_SEP, T_SEP_08, call_record


DAY = "2026-09-08"


def _seq_ts(index: int) -> str:
    """Distinct in-day timestamp so repeated records get distinct dedup keys."""
    return f"2026-09-08T09:{index:02d}:00Z"

MONTH = MONTH_SEP


def test_over_budget_call_rejected_over_real_metering_feed(metering_reporter):
    ingest, reporter_factory = metering_reporter
    # acme already spent $0.07 this month on the metering feed.
    ingest(call_record(tenant="acme", estimate=0.02, ts=_seq_ts(1)))
    ingest(call_record(tenant="acme", estimate=0.02, ts=_seq_ts(2)))
    ingest(call_record(tenant="acme", estimate=0.03, ts=_seq_ts(3)))

    ledger = MeteringReporterLedger(reporter_factory())
    assert abs(ledger.monthly_cost("acme", month=MONTH) - 0.07) < 1e-9

    enforcer = BudgetEnforcer(
        ledger,
        {
            "acme": TenantBudgetPolicy(
                tenant_id="acme",
                mode=MODE_ENFORCE,
                cost_limit=BudgetLimit(window="month", limit=0.08, warn_at_pct=0.8),
            )
        },
    )
    # A call that would push spend to $0.085 > $0.08 cap is BLOCKED.
    d = enforcer.check("acme", vendor="deepseek",
                       requested_cost_usd=0.015, month=MONTH)
    assert d.decision == DECISION_BLOCK
    assert not d.allowed
    assert d.code == "budget.cost.exceeded"
    assert d.outcome == OUTCOME_BUDGET_EXCEEDED  # metering non-billable outcome

    # A small call that stays under the hard cap is warned but allowed
    # (current $0.07 is already above the warnAt $0.064 threshold).
    d2 = enforcer.check("acme", vendor="deepseek",
                        requested_cost_usd=0.001, month=MONTH)
    assert d2.decision == DECISION_WARN
    assert d2.allowed


def test_kill_switch_refuses_after_ingested_usage(metering_reporter):
    ingest, reporter_factory = metering_reporter
    ingest(call_record(tenant="acme", estimate=0.01, ts=T_SEP_08))
    ledger = MeteringReporterLedger(reporter_factory())
    audit = MemoryAuditStore()
    ks = KillSwitchController(audit=audit)
    # normally allowed
    assert ks.check_call("acme", service="model").decision == DECISION_ALLOW
    # engage -> refused
    ks.pause(reason="platform incident", paused_by="owner")
    d = ks.check_call("acme", service="model")
    assert d.decision == DECISION_REFUSE
    assert any(e.code == "kill_switch.global_pause" for e in audit.read())


def test_quota_over_metered_calls(metering_reporter):
    ingest, reporter_factory = metering_reporter
    for index in range(6):
        ingest(call_record(tenant="acme", estimate=0.001, ts=_seq_ts(index)))
    ledger = MeteringReporterLedger(reporter_factory())
    assert ledger.daily_calls("acme", day="2026-09-08") == 6
    enforcer = QuotaEnforcer(
        ledger,
        {
            "acme": QuotaPolicy(
                tenant_id="acme",
                limits={
                    RESOURCE_REQUESTS: QuotaLimit(
                        resource=RESOURCE_REQUESTS, window="day",
                        soft_limit=3, hard_limit=5,
                    )
                },
            )
        },
    )
    d = enforcer.check("acme", resource=RESOURCE_REQUESTS,
                       requested=1, day="2026-09-08")
    assert d.decision == DECISION_BLOCK  # 6 + 1 > hard 5


def test_export_and_chargeback_over_same_store(metering_reporter):
    ingest, reporter_factory = metering_reporter
    ingest(call_record(tenant="acme", estimate=0.01, ts=T_SEP_08))
    ingest(call_record(tenant="globex", estimate=0.02, ts=T_SEP_08))
    usage_reporter = reporter_factory()
    ledger = MeteringReporterLedger(usage_reporter)

    # chargeback over the metering feed
    cb = ChargebackReportGenerator(usage_reporter)
    rows = cb.report(month=MONTH)
    assert {r.tenant_id for r in rows} == {"acme", "globex"}
    assert cb.totals()["calls"] == 2

    # export over the same ledger
    audit = MemoryAuditStore()
    exporter = BudgetStateExporter(ledger, audit=audit)
    snap = exporter.snapshot()
    assert snap["killSwitch"]["globalPause"] is False
    assert snap["audit"]["totalEvents"] == 0

    # a budget block lands in the audit and the export surfaces it
    enforcer = BudgetEnforcer(
        ledger,
        {
            "acme": TenantBudgetPolicy(
                tenant_id="acme",
                mode=MODE_ENFORCE,
                cost_limit=BudgetLimit(window="month", limit=0.005, warn_at_pct=0.8),
            )
        },
    )
    decision = enforcer.check("acme", requested_cost_usd=0.001, month=MONTH)
    assert decision.decision == DECISION_BLOCK
    record_decision(audit, decision)
    exporter = BudgetStateExporter(ledger, budget=enforcer, audit=audit)
    summary = exporter.audit_summary()
    assert summary["byDecision"].get("block") == 1
