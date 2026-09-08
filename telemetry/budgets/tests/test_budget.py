"""telemetry/budgets — budget enforcer tests (issue #34).

Covers the warn -> block ladder, observe vs enforce rollout, the per-vendor
cap, and the core negative: **an over-budget call is refused in enforce
mode — never silently allowed**.
"""

from __future__ import annotations

import pytest

from telemetry.budgets.budget import (
    BudgetEnforcer,
    BudgetLimit,
    TenantBudgetPolicy,
    VendorBudgetCap,
    load_budget_policies,
)
from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WARN,
    DECISION_WOULD_BLOCK,
    DECISION_WOULD_WARN,
    MODE_ENFORCE,
    MODE_OBSERVE,
    OUTCOME_BUDGET_EXCEEDED,
)

DAY = "2026-09-08"
MONTH = "2026-09"


def _policy(
    tenant: str,
    mode: str = MODE_ENFORCE,
    cost_limit_usd: float = 0.0,
    token_limit: int = 0,
    caps=(),
    cost_warn: float = 0.8,
    token_warn: float = 0.8,
) -> TenantBudgetPolicy:
    return TenantBudgetPolicy(
        tenant_id=tenant,
        mode=mode,
        cost_limit=(
            BudgetLimit(window="month", limit=cost_limit_usd, warn_at_pct=cost_warn)
            if cost_limit_usd
            else None
        ),
        token_limit=(
            BudgetLimit(window="day", limit=token_limit, warn_at_pct=token_warn)
            if token_limit
            else None
        ),
        vendor_caps=tuple(caps),
    )


def _enforcer(ledger: StaticLedger, *policies: TenantBudgetPolicy) -> BudgetEnforcer:
    return BudgetEnforcer(ledger, {p.tenant_id: p for p in policies})


# --------------------------------------------------------------------------- #
# allow / warn ladder
# --------------------------------------------------------------------------- #
def test_budget_within_limit_allows_enforce():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)          # monthly cap 100, warn at 80
    enforcer = _enforcer(ledger, _policy("acme", cost_limit_usd=100.0))
    d = enforcer.check("acme", requested_cost_usd=1.0, month=MONTH)
    assert d.decision == DECISION_ALLOW
    assert d.allowed
    assert d.code == "budget.cost.allow"


def test_budget_at_warn_threshold_warns_but_allows():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 75.0)          # 75 + 5 = 80 == warnAt
    enforcer = _enforcer(ledger, _policy("acme", cost_limit_usd=100.0, cost_warn=0.8))
    d = enforcer.check("acme", requested_cost_usd=5.0, month=MONTH)
    assert d.decision == DECISION_WARN
    assert d.allowed  # warned, not refused
    assert d.code == "budget.cost.warn"


def test_budget_token_warn_threshold():
    ledger = StaticLedger()
    ledger.seed_tokens("acme", DAY, 1_500_000)     # + 100k = 1.6M == warnAt
    # limit 2_000_000 warn 0.8 -> warnAt 1_600_000; projected 1.6M >= warnAt
    # and < limit -> warn (not block)
    enforcer = _enforcer(ledger, _policy("acme", token_limit=2_000_000))
    d = enforcer.check("acme", requested_tokens=100_000, day=DAY)
    assert d.decision == DECISION_WARN
    assert d.allowed


# --------------------------------------------------------------------------- #
# Negative: over-budget call is BLOCKED in enforce mode (never silently allowed)
# --------------------------------------------------------------------------- #
def test_over_budget_cost_call_is_blocked_not_silently_allowed():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 90.0)
    enforcer = _enforcer(ledger, _policy("acme", cost_limit_usd=100.0))
    d = enforcer.check("acme", requested_cost_usd=20.0, month=MONTH)  # 110 > 100
    assert d.decision == DECISION_BLOCK
    assert not d.allowed
    assert d.code == "budget.cost.exceeded"
    assert d.outcome == OUTCOME_BUDGET_EXCEEDED  # maps to metering non-billable


def test_over_budget_token_call_is_blocked():
    ledger = StaticLedger()
    ledger.seed_tokens("acme", DAY, 1_900_000)
    enforcer = _enforcer(ledger, _policy("acme", token_limit=2_000_000))
    d = enforcer.check("acme", requested_tokens=200_000, day=DAY)  # 2.1M > 2M
    assert d.decision == DECISION_BLOCK
    assert not d.allowed
    assert d.code == "budget.tokens.exceeded"
    assert d.outcome == OUTCOME_BUDGET_EXCEEDED


def test_over_budget_blocks_even_with_zero_tokens_when_cost_over():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 100.0)         # already at the cap
    enforcer = _enforcer(ledger, _policy("acme", cost_limit_usd=100.0))
    d = enforcer.check("acme", requested_cost_usd=0.001, month=MONTH)
    assert d.decision == DECISION_BLOCK


# --------------------------------------------------------------------------- #
# Observe mode: reports would_block but NEVER refuses
# --------------------------------------------------------------------------- #
def test_observe_over_budget_is_would_block_but_allowed():
    ledger = StaticLedger()
    ledger.seed_cost("omega", MONTH, 90.0)
    enforcer = _enforcer(
        ledger, _policy("omega", mode=MODE_OBSERVE, cost_limit_usd=100.0)
    )
    d = enforcer.check("omega", requested_cost_usd=20.0, month=MONTH)
    assert d.decision == DECISION_WOULD_BLOCK
    assert d.allowed  # observe never refuses
    assert d.outcome is None


def test_observe_at_warn_is_would_warn():
    ledger = StaticLedger()
    ledger.seed_cost("omega", MONTH, 75.0)
    enforcer = _enforcer(
        ledger, _policy("omega", mode=MODE_OBSERVE, cost_limit_usd=100.0, cost_warn=0.8)
    )
    d = enforcer.check("omega", requested_cost_usd=5.0, month=MONTH)
    assert d.decision == DECISION_WOULD_WARN
    assert d.allowed


def test_observe_within_budget_allows():
    ledger = StaticLedger()
    enforcer = _enforcer(
        ledger, _policy("omega", mode=MODE_OBSERVE, cost_limit_usd=100.0)
    )
    d = enforcer.check("omega", requested_cost_usd=1.0, month=MONTH)
    assert d.decision == DECISION_ALLOW


# --------------------------------------------------------------------------- #
# Per-vendor cap (per-vendor/model budget ladder)
# --------------------------------------------------------------------------- #
def test_vendor_cap_block_when_vendor_over():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)                      # tenant fine
    ledger.seed_vendor_cost("acme", "anthropic", MONTH, 45.0)  # cap 50
    enforcer = _enforcer(
        ledger,
        _policy(
            "acme",
            cost_limit_usd=500.0,
            caps=[VendorBudgetCap(vendor="anthropic", limit_usd=50.0, warn_at_pct=0.8)],
        ),
    )
    d = enforcer.check("acme", vendor="anthropic", model="claude-3-5-sonnet",
                       requested_cost_usd=10.0, month=MONTH)  # 55 > 50
    assert d.decision == DECISION_BLOCK
    assert d.code == "budget.vendor.anthropic.exceeded"
    assert d.vendor == "anthropic"


def test_vendor_within_cap_allows_when_tenant_fine():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)
    ledger.seed_vendor_cost("acme", "anthropic", MONTH, 30.0)  # +1 = 31 < warnAt 40
    enforcer = _enforcer(
        ledger,
        _policy(
            "acme",
            cost_limit_usd=500.0,
            caps=[VendorBudgetCap(vendor="anthropic", limit_usd=50.0, warn_at_pct=0.8)],
        ),
    )
    d = enforcer.check("acme", vendor="anthropic", requested_cost_usd=1.0, month=MONTH)
    assert d.decision == DECISION_ALLOW


def test_unconfigured_vendor_ignores_vendor_cap():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)
    enforcer = _enforcer(
        ledger,
        _policy(
            "acme",
            cost_limit_usd=500.0,
            caps=[VendorBudgetCap(vendor="anthropic", limit_usd=50.0)],
        ),
    )
    d = enforcer.check("acme", vendor="deepseek", requested_cost_usd=100.0, month=MONTH)
    assert d.decision == DECISION_ALLOW  # deepseek has no cap; tenant under 500


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_no_policy_allows():
    enforcer = _enforcer(StaticLedger())
    d = enforcer.check("ghost", requested_cost_usd=999.0)
    assert d.decision == DECISION_ALLOW
    assert d.code == "budget.unconfigured"


def test_most_severe_wins_across_rails():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)
    ledger.seed_tokens("acme", DAY, 1_900_000)
    enforcer = _enforcer(
        ledger, _policy("acme", cost_limit_usd=500.0, token_limit=2_000_000)
    )
    d = enforcer.check("acme", requested_cost_usd=1.0,
                       requested_tokens=200_000, day=DAY, month=MONTH)
    assert d.decision == DECISION_BLOCK          # token rail over -> block
    assert d.code == "budget.tokens.exceeded"


def test_default_config_loads():
    policies = load_budget_policies()
    assert "acme" in policies
    assert policies["acme"].mode == MODE_ENFORCE
    assert "tenant-omega" in policies
    assert policies["tenant-omega"].mode == MODE_OBSERVE


def test_invalid_token_month_window_rejected():
    with pytest.raises(ValueError):
        TenantBudgetPolicy(
            tenant_id="x",
            token_limit=BudgetLimit(window="month", limit=100),
        )
