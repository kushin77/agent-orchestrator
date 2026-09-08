"""Daily-token-budget tests (issue #33): observe->enforce toggle semantics.

Observe mode (the safe default) reports what WOULD be blocked and never
blocks; enforce mode returns a hard ``block`` the consuming caller refuses.
A tenant with no configured limit is always allowed.  Current daily usage
comes from the durable store, so the budget is correct across restarts.
"""

from __future__ import annotations

import pytest

from telemetry.metering.budget import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WOULD_BLOCK,
    BUDGET_MODE_ENFORCE,
    BUDGET_MODE_OBSERVE,
    BudgetPolicy,
    DailyTokenBudget,
    load_budget_config,
)
from telemetry.metering.intake import MeteringIntake
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import MemoryUsageStore

from conftest import T_SEP_08, model_call_event


def _budget_store(records):
    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)
    for record in records:
        intake.ingest(record)
    return store


@pytest.fixture()
def over_budget_store():
    """acme already burned 2.1M tokens today (limit 2M)."""
    return _budget_store(
        [
            model_call_event(tenant="acme", input_tokens=2_000_000,
                             output_tokens=100_000, ts=T_SEP_08),
            model_call_event(tenant="acme", input_tokens=1_000,
                             output_tokens=500, ts=T_SEP_08),
        ]
    )


def _budget(store, policies=None, **kwargs):
    return DailyTokenBudget(
        UsageReporter(store), policies=policies or {}, **kwargs
    )


def test_observe_mode_never_blocks(over_budget_store):
    budget = _budget(
        over_budget_store,
        {"acme": BudgetPolicy("acme", 2_000_000, BUDGET_MODE_OBSERVE)},
    )
    verdict = budget.check("acme", requested_tokens=0, day="2026-09-08")
    assert verdict.mode == BUDGET_MODE_OBSERVE
    assert verdict.decision == DECISION_WOULD_BLOCK  # advisory only
    assert verdict.current_tokens == 2_101_500
    assert verdict.daily_token_limit == 2_000_000


def test_enforce_mode_blocks_when_over(over_budget_store):
    budget = _budget(
        over_budget_store,
        {"acme": BudgetPolicy("acme", 2_000_000, BUDGET_MODE_ENFORCE)},
    )
    verdict = budget.check("acme", requested_tokens=0, day="2026-09-08")
    assert verdict.mode == BUDGET_MODE_ENFORCE
    assert verdict.decision == DECISION_BLOCK


def test_enforce_mode_allows_when_within(over_budget_store):
    budget = _budget(
        over_budget_store,
        {"acme": BudgetPolicy("acme", 3_000_000, BUDGET_MODE_ENFORCE)},
    )
    verdict = budget.check("acme", requested_tokens=500, day="2026-09-08")
    assert verdict.decision == DECISION_ALLOW


def test_requested_tokens_push_over_in_enforce():
    store = MemoryUsageStore()  # acme has used 0 tokens
    budget = _budget(
        store, {"acme": BudgetPolicy("acme", 1000, BUDGET_MODE_ENFORCE)}
    )
    within = budget.check("acme", requested_tokens=900, day="2026-09-08")
    over = budget.check("acme", requested_tokens=1001, day="2026-09-08")
    assert within.decision == DECISION_ALLOW
    assert over.decision == DECISION_BLOCK


def test_observe_flip_to_enforce_is_the_safe_rollout_path():
    """The SAME over-budget tenant goes would_block -> block on the flip."""
    store = _budget_store(
        [model_call_event(tenant="acme", input_tokens=2_500_000,
                          output_tokens=0, ts=T_SEP_08)]
    )
    observe = _budget(
        store, {"acme": BudgetPolicy("acme", 2_000_000, BUDGET_MODE_OBSERVE)}
    )
    enforce = _budget(
        store, {"acme": BudgetPolicy("acme", 2_000_000, BUDGET_MODE_ENFORCE)}
    )
    assert observe.check("acme", day="2026-09-08").decision == DECISION_WOULD_BLOCK
    assert enforce.check("acme", day="2026-09-08").decision == DECISION_BLOCK


def test_no_policy_no_default_is_allow():
    budget = _budget(MemoryUsageStore())
    verdict = budget.check("acme", day="2026-09-08")
    assert verdict.decision == DECISION_ALLOW
    assert "no daily token budget" in verdict.reason
    assert verdict.daily_token_limit is None


def test_default_limit_applies_to_unlisted_tenant():
    store = _budget_store(
        [model_call_event(tenant="acme", input_tokens=1_000, output_tokens=0,
                          ts=T_SEP_08)]
    )
    budget = _budget(store, default_limit=500, default_mode=BUDGET_MODE_ENFORCE)
    verdict = budget.check("acme", day="2026-09-08")
    assert verdict.decision == DECISION_BLOCK


def test_budget_policy_validates():
    with pytest.raises(ValueError):
        BudgetPolicy("acme", 0, BUDGET_MODE_OBSERVE)
    with pytest.raises(ValueError):
        BudgetPolicy("acme", 1000, "nope")


def test_default_config_loads_observe_default():
    from telemetry.metering.budget import DEFAULT_BUDGET_CONFIG

    policies = load_budget_config(DEFAULT_BUDGET_CONFIG)
    assert "acme" in policies
    assert policies["acme"].mode == BUDGET_MODE_OBSERVE
    assert "globex" in policies
    assert policies["globex"].mode == BUDGET_MODE_ENFORCE
