"""telemetry/budgets — spend alerts on warning/alert thresholds (issue #341).

The alert rail is separate from enforcement: an alert FIRES when spend reaches
the alert threshold (the cap by default), warns at the warning threshold, and
reports NO_DATA — never a fabricated zero — when the feed has metered nothing.
A soft cap fires the alert but never blocks; a hard cap in enforce mode fires
and blocks.
"""

from __future__ import annotations

import pytest

from telemetry.budgets.alerts import (
    KIND_COST,
    KIND_TOKENS,
    KIND_VENDOR,
    SEVERITY_ALERT,
    SEVERITY_NONE,
    SEVERITY_WARNING,
    SpendAlert,
    SpendAlertEvaluator,
    evaluate_all,
)
from telemetry.budgets.budget import (
    BudgetLimit,
    TenantBudgetPolicy,
    VendorBudgetCap,
)
from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import (
    CAP_SOFT,
    MODE_ENFORCE,
    MODE_OBSERVE,
)
from telemetry.observability.slos import (
    VERDICT_AT_RISK,
    VERDICT_BREACHED,
    VERDICT_NO_DATA,
    VERDICT_OK,
)

DAY = "2026-09-08"
MONTH = "2026-09"


def _policy(
    *,
    tenant: str = "acme",
    mode: str = MODE_ENFORCE,
    cost_limit: float = 0.0,
    cap: str = "hard",
    token_limit: int = 0,
    vendor_cap: VendorBudgetCap | None = None,
) -> TenantBudgetPolicy:
    return TenantBudgetPolicy(
        tenant_id=tenant,
        mode=mode,
        cost_limit=(
            BudgetLimit(window="month", limit=cost_limit, cap=cap)
            if cost_limit
            else None
        ),
        token_limit=(
            BudgetLimit(window="day", limit=token_limit) if token_limit else None
        ),
        vendor_caps=(vendor_cap,) if vendor_cap else (),
    )


def _evaluator(
    ledger: StaticLedger, *policies: TenantBudgetPolicy, probe=None
) -> SpendAlertEvaluator:
    return SpendAlertEvaluator(
        ledger, {p.tenant_id: p for p in policies}, data_probe=probe
    )


def _by_kind(state, kind: str) -> SpendAlert:
    return next(alert for alert in state.alerts if alert.kind == kind)


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #
def test_below_the_warning_threshold_is_ok():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)
    state = _evaluator(ledger, _policy(cost_limit=100.0)).evaluate(
        "acme", month=MONTH
    )
    alert = _by_kind(state, KIND_COST)
    assert alert.verdict == VERDICT_OK
    assert alert.severity == SEVERITY_NONE
    assert alert.fires is False
    assert state.verdict == VERDICT_OK


def test_warning_threshold_warns_without_firing():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 85.0)  # warnAt 80, alert 100
    state = _evaluator(ledger, _policy(cost_limit=100.0)).evaluate(
        "acme", month=MONTH
    )
    alert = _by_kind(state, KIND_COST)
    assert alert.verdict == VERDICT_AT_RISK
    assert alert.severity == SEVERITY_WARNING
    assert alert.fires is False
    assert alert.code == "budget.cost.warn"
    assert state.verdict == VERDICT_AT_RISK


def test_alert_threshold_fires_the_breach():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)  # past the 120 cap
    state = _evaluator(ledger, _policy(cost_limit=120.0)).evaluate(
        "acme", month=MONTH
    )
    alert = _by_kind(state, KIND_COST)
    assert alert.verdict == VERDICT_BREACHED
    assert alert.severity == SEVERITY_ALERT
    assert alert.fires is True
    assert alert.blocks is True  # hard cap + enforce mode
    assert alert.code == "budget.cost.breach"
    assert alert.current == pytest.approx(130.0)
    assert alert.alert_at == pytest.approx(120.0)
    assert state.fired == (alert,)


def test_a_declared_alert_threshold_below_the_cap_fires_early():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 90.0)
    policy = TenantBudgetPolicy(
        tenant_id="acme",
        mode=MODE_ENFORCE,
        cost_limit=BudgetLimit(
            window="month", limit=100.0, warn_at_pct=0.8, alert_at_pct=0.9
        ),
    )
    state = _evaluator(ledger, policy).evaluate("acme", month=MONTH)
    alert = _by_kind(state, KIND_COST)
    assert alert.fires is True
    assert alert.alert_at == pytest.approx(90.0)


def test_soft_cap_breach_fires_but_never_blocks():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)
    state = _evaluator(ledger, _policy(cost_limit=120.0, cap=CAP_SOFT)).evaluate(
        "acme", month=MONTH
    )
    alert = _by_kind(state, KIND_COST)
    assert alert.fires is True
    assert alert.severity == SEVERITY_ALERT
    assert alert.blocks is False  # advisory cap: alerts, does not refuse
    assert alert.cap == CAP_SOFT


def test_observe_mode_breach_alerts_without_claiming_enforcement():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)
    state = _evaluator(
        ledger, _policy(cost_limit=120.0, mode=MODE_OBSERVE)
    ).evaluate("acme", month=MONTH)
    alert = _by_kind(state, KIND_COST)
    assert alert.fires is True
    assert alert.blocks is False  # observe never refuses


# --------------------------------------------------------------------------- #
# NO_DATA: unknown, never a fabricated zero
# --------------------------------------------------------------------------- #
def test_no_metered_data_is_no_data_not_zero():
    state = _evaluator(StaticLedger(), _policy(cost_limit=100.0)).evaluate(
        "acme", month=MONTH
    )
    alert = _by_kind(state, KIND_COST)
    assert alert.verdict == VERDICT_NO_DATA
    assert alert.severity == SEVERITY_NONE
    assert alert.current is None  # NOT 0.0 — that would read as "no spend"
    assert alert.fires is False
    assert alert.code == "budget.cost.no_data"
    assert "unknown" in alert.message
    assert state.has_data is False
    assert state.verdict == VERDICT_NO_DATA


def test_a_measured_zero_is_ok_not_no_data():
    """Data exists and sums to zero (cache hits, local models) -> an honest OK."""
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 0.0)
    state = _evaluator(ledger, _policy(cost_limit=100.0)).evaluate(
        "acme", month=MONTH
    )
    alert = _by_kind(state, KIND_COST)
    assert alert.verdict == VERDICT_OK
    assert alert.current == 0.0
    assert state.has_data is True


def test_every_limit_reports_no_data_when_the_feed_is_empty():
    ledger = StaticLedger()
    policy = _policy(
        cost_limit=100.0,
        token_limit=1_000_000,
        vendor_cap=VendorBudgetCap(vendor="anthropic", limit_usd=50.0),
    )
    state = _evaluator(ledger, policy).evaluate("acme", day=DAY, month=MONTH)
    assert {alert.kind for alert in state.alerts} == {KIND_COST, KIND_TOKENS, KIND_VENDOR}
    assert all(alert.verdict == VERDICT_NO_DATA for alert in state.alerts)
    assert all(alert.current is None for alert in state.alerts)
    # the declared policy is still reported: only the *measurement* is unknown
    cost_alert = _by_kind(state, KIND_COST)
    assert cost_alert.limit == pytest.approx(100.0)
    assert cost_alert.alert_at == pytest.approx(100.0)


def test_data_probe_overrides_the_ledger_capability():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)
    state = _evaluator(
        ledger, _policy(cost_limit=120.0), probe=lambda _tenant: False
    ).evaluate("acme", month=MONTH)
    assert _by_kind(state, KIND_COST).verdict == VERDICT_NO_DATA


# --------------------------------------------------------------------------- #
# Rails, feed and edge cases
# --------------------------------------------------------------------------- #
def test_tokens_and_vendor_rails_alert_independently():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 10.0)                       # under the cap
    ledger.seed_tokens("acme", DAY, 1_900_000)                  # 95% of 2M -> warn
    ledger.seed_vendor_cost("acme", "anthropic", MONTH, 60.0)   # over the 50 cap
    policy = _policy(
        cost_limit=100.0,
        token_limit=2_000_000,
        vendor_cap=VendorBudgetCap(vendor="anthropic", limit_usd=50.0),
    )
    state = _evaluator(ledger, policy).evaluate("acme", day=DAY, month=MONTH)
    assert _by_kind(state, KIND_COST).verdict == VERDICT_OK
    assert _by_kind(state, KIND_TOKENS).verdict == VERDICT_AT_RISK
    assert _by_kind(state, KIND_VENDOR).verdict == VERDICT_BREACHED
    assert state.verdict == VERDICT_BREACHED  # worst wins
    assert [alert.kind for alert in state.fired] == [KIND_VENDOR]


def test_fired_feed_lists_only_breaches():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)     # breach (cap 120)
    ledger.seed_tokens("acme", DAY, 10)        # far under
    policy = _policy(cost_limit=120.0, token_limit=1_000_000)
    evaluator = _evaluator(ledger, policy)
    assert [alert.kind for alert in evaluator.fired("acme", day=DAY, month=MONTH)] == [
        KIND_COST
    ]
    assert len(evaluator.alerts("acme", day=DAY, month=MONTH)) == 2


def test_tenant_without_a_policy_has_no_alerts():
    state = _evaluator(StaticLedger()).evaluate("ghost")
    assert state.alerts == ()
    assert state.fired == ()
    assert state.has_data is False
    assert state.verdict == VERDICT_NO_DATA


def test_evaluate_all_covers_every_policy_tenant():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)
    states = evaluate_all(
        _evaluator(
            ledger,
            _policy(tenant="acme", cost_limit=120.0),
            _policy(tenant="globex", cost_limit=50.0),
        ),
        month=MONTH,
    )
    by_tenant = {state.tenant_id: state for state in states}
    assert by_tenant["acme"].verdict == VERDICT_BREACHED
    assert by_tenant["globex"].verdict == VERDICT_NO_DATA  # never metered


def test_alert_serialization_carries_the_thresholds_and_units():
    ledger = StaticLedger()
    ledger.seed_cost("acme", MONTH, 130.0)
    payload = _evaluator(ledger, _policy(cost_limit=120.0)).evaluate(
        "acme", month=MONTH
    ).to_dict()
    fired = payload["fired"][0]
    assert fired["unit"] == "usd"
    assert fired["window"] == "month"
    assert fired["fires"] is True
    assert fired["blocks"] is True
    assert fired["alertAt"] == pytest.approx(120.0)
    assert fired["warnAt"] == pytest.approx(96.0)
