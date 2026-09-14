"""telemetry/budgets — soft-cap vs hard-cap semantics (issue #341).

A **hard** cap refuses the call once the limit is reached (enforce mode); a
**soft** cap is an advisory target — crossing it warns and fires the spend
alert (see ``test_alerts.py``) but never refuses. The shipped policies declare
no ``cap`` and therefore keep their hard-cap behaviour exactly as before.
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
    CAP_HARD,
    CAP_SOFT,
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WARN,
    DECISION_WOULD_WARN,
    EnforcerDecision,
    MODE_ENFORCE,
    MODE_OBSERVE,
)

DAY = "2026-09-08"
MONTH = "2026-09"


def _policy(*, cap: str, mode: str = MODE_ENFORCE, limit: float = 100.0) -> TenantBudgetPolicy:
    return TenantBudgetPolicy(
        tenant_id="acme",
        mode=mode,
        cost_limit=BudgetLimit(window="month", limit=limit, cap=cap),
    )


def _enforcer(ledger: StaticLedger, policy: TenantBudgetPolicy) -> BudgetEnforcer:
    return BudgetEnforcer(ledger, {policy.tenant_id: policy})


# --------------------------------------------------------------------------- #
# Hard cap (the shipped default) — unchanged ladder
# --------------------------------------------------------------------------- #
def test_hard_cap_blocks_over_limit_in_enforce_mode():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 120.0)  # already past the 100 cap
    decision = _enforcer(ledger, _policy(cap=CAP_HARD)).check(
        "acme", requested_cost_usd=1.0, month=MONTH
    )
    assert decision.decision == DECISION_BLOCK
    assert decision.allowed is False
    assert decision.code == "budget.cost.exceeded"
    assert decision.cap == CAP_HARD


def test_hard_cap_is_the_default_when_unspecified():
    limit = BudgetLimit(window="month", limit=100.0)
    assert limit.cap == CAP_HARD
    assert limit.soft is False


# --------------------------------------------------------------------------- #
# Soft cap — alerts, never refuses
# --------------------------------------------------------------------------- #
def test_soft_cap_over_limit_warns_and_allows_in_enforce_mode():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 120.0)  # well past the 100 soft cap
    decision = _enforcer(ledger, _policy(cap=CAP_SOFT)).check(
        "acme", requested_cost_usd=50.0, month=MONTH
    )
    assert decision.decision == DECISION_WARN
    assert decision.allowed is True
    assert decision.code == "budget.cost.soft_exceeded"
    assert decision.cap == CAP_SOFT
    assert decision.outcome is None  # not a refusal: no non-billable outcome


def test_soft_cap_over_limit_is_would_warn_in_observe_mode():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 120.0)
    decision = _enforcer(
        ledger, _policy(cap=CAP_SOFT, mode=MODE_OBSERVE)
    ).check("acme", requested_cost_usd=50.0, month=MONTH)
    assert decision.decision == DECISION_WOULD_WARN
    assert decision.allowed is True


def test_soft_cap_warn_threshold_still_warns_below_the_cap():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 85.0)  # +5 = 90: over warnAt 80, under cap 100
    decision = _enforcer(ledger, _policy(cap=CAP_SOFT)).check(
        "acme", requested_cost_usd=5.0, month=MONTH
    )
    assert decision.decision == DECISION_WARN
    assert decision.code == "budget.cost.warn"
    assert decision.cap == CAP_SOFT


def test_soft_cap_below_warn_threshold_allows():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)
    decision = _enforcer(ledger, _policy(cap=CAP_SOFT)).check(
        "acme", requested_cost_usd=1.0, month=MONTH
    )
    assert decision.decision == DECISION_ALLOW
    assert decision.cap == CAP_SOFT


def test_soft_vendor_cap_never_blocks():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)
    ledger.seed_vendor_cost("acme", "anthropic", MONTH, 60.0)  # cap 50, soft
    policy = TenantBudgetPolicy(
        tenant_id="acme",
        mode=MODE_ENFORCE,
        vendor_caps=(
            VendorBudgetCap(vendor="anthropic", limit_usd=50.0, cap=CAP_SOFT),
        ),
    )
    decision = _enforcer(ledger, policy).check(
        "acme", vendor="anthropic", requested_cost_usd=10.0, month=MONTH
    )
    assert decision.decision == DECISION_WARN
    assert decision.allowed is True
    assert decision.code == "budget.vendor.anthropic.soft_exceeded"


# --------------------------------------------------------------------------- #
# Vocabulary / config
# --------------------------------------------------------------------------- #
def test_unknown_cap_semantics_is_rejected():
    with pytest.raises(ValueError, match="unknown cap semantics"):
        BudgetLimit(window="month", limit=10.0, cap="wiggle")
    with pytest.raises(ValueError, match="unknown cap semantics"):
        EnforcerDecision(
            tenant_id="acme", kind="budget", decision="allow",
            reason="x", code="y", cap="wiggle",
        )


def test_alert_threshold_must_not_precede_the_warning_threshold():
    with pytest.raises(ValueError, match="alertAtPct"):
        BudgetLimit(window="month", limit=10.0, warn_at_pct=0.9, alert_at_pct=0.5)
    with pytest.raises(ValueError, match="alertAtPct"):
        BudgetLimit(window="month", limit=10.0, alert_at_pct=0.0)


def test_alert_threshold_defaults_to_the_limit():
    limit = BudgetLimit(window="month", limit=100.0)
    assert limit.alert_at == 100.0
    assert limit.warn_at == 80.0


def test_loader_reads_cap_and_alert_threshold(tmp_path):
    config = tmp_path / "policies.yaml"
    config.write_text(
        "schemaVersion: 1\n"
        "policies:\n"
        "  - tenantId: acme\n"
        "    mode: enforce\n"
        "    cost:\n"
        "      window: month\n"
        "      limitUsd: 100.0\n"
        "      warnAtPct: 0.8\n"
        "      alertAtPct: 0.9\n"
        "      cap: soft\n"
        "    vendorCaps:\n"
        "      - vendor: anthropic\n"
        "        limitUsd: 50.0\n"
        "        cap: soft\n",
        encoding="utf-8",
    )
    policies = load_budget_policies(config)
    policy = policies["acme"]
    assert policy.cost_limit.cap == CAP_SOFT
    assert policy.cost_limit.alert_at == pytest.approx(90.0)
    assert policy.vendor_caps[0].cap == CAP_SOFT
    assert policy.cost_limit.to_dict()["cap"] == CAP_SOFT
    assert policy.cost_limit.to_dict()["alertAtPct"] == 0.9


def test_shipped_policies_keep_their_hard_cap_behaviour():
    """The repo's own policies declare no cap -> hard, exactly as before."""
    policies = load_budget_policies()
    assert "acme" in policies
    assert policies["acme"].cost_limit.cap == CAP_HARD
    assert policies["acme"].cost_limit.alert_at == policies["acme"].cost_limit.limit
