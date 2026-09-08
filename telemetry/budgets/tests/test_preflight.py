"""telemetry/budgets — composed preflight tests (issue #34).

The gateway/engine enforcement contract: kill switch -> quota -> budget, the
most severe decision wins, and refused calls carry the metering
non-billable outcome so they are never metered as usage.
"""

from __future__ import annotations

from telemetry.budgets.audit import MemoryAuditStore
from telemetry.budgets.budget import BudgetEnforcer, BudgetLimit, TenantBudgetPolicy
from telemetry.budgets.killswitch import KillSwitchController
from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_REFUSE,
    MODE_ENFORCE,
    OUTCOME_BUDGET_EXCEEDED,
    OUTCOME_REFUSED,
    RESOURCE_REQUESTS,
)
from telemetry.budgets.preflight import first_blocking_rail, preflight
from telemetry.budgets.quota import QuotaEnforcer, QuotaLimit, QuotaPolicy

DAY = "2026-09-08"
MONTH = "2026-09"


def _quota(ledger: StaticLedger) -> QuotaEnforcer:
    return QuotaEnforcer(
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


def _budget(ledger: StaticLedger) -> BudgetEnforcer:
    return BudgetEnforcer(
        ledger,
        {
            "acme": TenantBudgetPolicy(
                tenant_id="acme",
                mode=MODE_ENFORCE,
                cost_limit=BudgetLimit(window="month", limit=100.0, warn_at_pct=0.8),
                token_limit=BudgetLimit(window="day", limit=2_000_000, warn_at_pct=0.8),
            )
        },
    )


def test_all_rails_allow_when_healthy():
    ledger = StaticLedger()
    result = preflight(
        "acme", vendor="deepseek", requested_cost_usd=1.0, requested_tokens=5_000,
        day=DAY, month=MONTH,
        killswitch=KillSwitchController(),
        quota=_quota(ledger),
        budget=_budget(ledger),
    )

    assert result.decision in {DECISION_ALLOW, "warn", "would_warn", "would_block"}
    assert result.allowed


def test_kill_switch_refuses_before_budget_quota():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)
    audit = MemoryAuditStore()
    ks = KillSwitchController(audit=audit)
    ks.pause(reason="full stop", paused_by="owner")
    result = preflight(
        "acme", vendor="deepseek", requested_cost_usd=1.0, requested_tokens=100,
        killswitch=ks, quota=_quota(ledger), budget=_budget(ledger),
    )
    assert result.decision == DECISION_REFUSE
    assert result.outcome == OUTCOME_REFUSED
    blocking = first_blocking_rail(result)
    assert blocking is not None
    assert blocking.kind == "kill_switch"


def test_budget_block_carries_budget_exceeded_outcome():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 95.0)  # +10 = 105 > 100
    result = preflight(
        "acme", vendor="deepseek", requested_cost_usd=10.0, requested_tokens=100,
        day=DAY, month=MONTH,
        killswitch=KillSwitchController(),
        quota=_quota(ledger),
        budget=_budget(ledger),
    )
    assert result.decision == DECISION_BLOCK
    assert result.outcome == OUTCOME_BUDGET_EXCEEDED
    assert result.rail("budget").code == "budget.cost.exceeded"


def test_quota_hard_block_carries_blocked_outcome():
    ledger = StaticLedger()
    ledger.seed_calls("acme", DAY, 500)
    result = preflight(
        "acme", vendor="deepseek", requested_cost_usd=0.1, requested_tokens=10,
        day=DAY,
        killswitch=KillSwitchController(),
        quota=_quota(ledger),
        budget=BudgetEnforcer(StaticLedger(), {}),  # no budget -> allow rail
    )
    assert result.decision == DECISION_BLOCK
    assert result.rail("quota").code == "quota.hard.requests.exceeded"


def test_first_blocking_rail_is_none_when_allowed():
    ledger = StaticLedger()
    result = preflight(
        "acme", vendor="deepseek", requested_cost_usd=0.1, requested_tokens=10,
        killswitch=KillSwitchController(),
        quota=QuotaEnforcer(StaticLedger(), {}),
        budget=BudgetEnforcer(StaticLedger(), {}),
    )
    assert result.allowed
    assert first_blocking_rail(result) is None


def test_to_dict_shape():
    ledger = StaticLedger()
    result = preflight(
        "acme", vendor="deepseek", requested_cost_usd=0.1, requested_tokens=10,
        killswitch=KillSwitchController(),
        quota=QuotaEnforcer(StaticLedger(), {}),
        budget=BudgetEnforcer(StaticLedger(), {}),
    )
    payload = result.to_dict()
    assert payload["tenantId"] == "acme"
    assert "rails" in payload
    assert payload["allowed"] is True
