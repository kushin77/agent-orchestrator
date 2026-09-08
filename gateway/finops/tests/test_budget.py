"""Per-tenant budget enforcement tests: stop/warn/fallback (issue #17 AC 3).

Uses a synthetic tier table with easy cost math (L0 ~ $1, L1 ~ $2, L2 ~ $10
per call at 1M tokens/call) and a $100 monthly budget with an 80% warn
threshold, so every decision boundary is exact and deterministic:

- policy stop    -> BudgetBlocked once projected spend crosses 80%
- policy warn    -> call allowed, flagged, up to the hard cap (100%)
- policy fallback-> tier downgraded toward the class's cheapest-capable floor
                    instead of blocking
- hard cap       -> always stops, whatever the policy
"""

from __future__ import annotations

import pytest
import yaml

import loader
from budget import (
    BudgetBlocked,
    BudgetEnforcer,
    BudgetLedger,
    BudgetPolicy,
    TenantBudget,
)
from chooser import ModelChooser

TENANT = "tenant-budget-test"
MONTHLY = 100.00
WARN_AT = 80.0  # 80% of $100 = $80.00 projected


def _synthetic_table():
    """Shipped table with costs overridden to $1/$2/$10 per call (1M tokens)."""
    data = yaml.safe_load(loader.TIERS_PATH.read_text(encoding="utf-8"))
    costs = {"L0": (1.0, 2.0), "L1": (2.0, 4.0), "L2": (10.0, 20.0)}
    for tier_key, (primary, fallback) in costs.items():
        models = data["ladder"][tier_key]["models"]
        models[0]["costPerMTok"] = primary
        models[1]["costPerMTok"] = fallback
    return loader.parse_tier_table(data)


@pytest.fixture()
def table():
    return _synthetic_table()


def _chooser(table, policy: BudgetPolicy, spend: float, warn_at: float = WARN_AT):
    ledger = BudgetLedger()
    ledger.add_spend(TENANT, spend)
    enforcer = BudgetEnforcer(ledger=ledger)
    enforcer.add_budget(TenantBudget(TENANT, MONTHLY, policy, warn_at_pct=warn_at))
    return ModelChooser(
        table=table,
        budget_enforcer=enforcer,
        tokens_per_call=1_000_000,
    ), enforcer


# ---------------------------------------------------------------------- stop
def test_stop_policy_blocks_at_warn_threshold(table) -> None:
    chooser, _ = _chooser(table, BudgetPolicy.STOP, spend=85.0)  # 85% used
    with pytest.raises(BudgetBlocked, match="budget stop"):
        chooser.choose(task_class="code-author", tenant_id=TENANT, complexity=5.0)


def test_stop_policy_allows_below_warn_threshold(table) -> None:
    chooser, _ = _chooser(table, BudgetPolicy.STOP, spend=50.0)  # 50% used
    choice = chooser.choose(task_class="code-author", tenant_id=TENANT, complexity=5.0)
    assert choice.tier == "L0"
    assert choice.budget_action == "allow"


# ---------------------------------------------------------------------- warn
def test_warn_policy_allows_and_flags(table) -> None:
    chooser, _ = _chooser(table, BudgetPolicy.WARN, spend=85.0)  # over 80%
    choice = chooser.choose(task_class="code-author", tenant_id=TENANT, complexity=5.0)
    assert choice.tier == "L0"  # allowed, not downgraded
    assert choice.budget_action == "warn"
    assert choice.warning is not None
    assert "80%" in choice.warning or "budget" in choice.warning


def test_warn_policy_below_threshold_is_clean(table) -> None:
    chooser, _ = _chooser(table, BudgetPolicy.WARN, spend=50.0)
    choice = chooser.choose(task_class="code-author", tenant_id=TENANT, complexity=5.0)
    assert choice.budget_action == "allow"
    assert choice.warning is None


# ------------------------------------------------------------------ fallback
def test_fallback_downgrades_toward_cheapest_floor(table) -> None:
    """research escalated to L2 by difficulty; fallback walks it back to L0
    where the projected spend finally drops below the warn threshold."""
    chooser, _ = _chooser(table, BudgetPolicy.FALLBACK, spend=78.5)
    choice = chooser.choose(task_class="research", tenant_id=TENANT, complexity=90.0)
    assert choice.tier == "L0"
    assert any("budget-fallback" in r for r in choice.reasons)


def test_fallback_respects_class_cheapest_capable_floor(table) -> None:
    """code-review's cheapest capable tier is L1; fallback cannot go below it."""
    chooser, _ = _chooser(table, BudgetPolicy.FALLBACK, spend=85.0)
    choice = chooser.choose(task_class="code-review", tenant_id=TENANT, complexity=90.0)
    assert choice.tier == "L1"  # downgraded from L2, floored at L1
    assert choice.budget_action == "fallback"
    assert choice.warning is not None


def test_fallback_downgrade_can_land_in_allow(table) -> None:
    """code-review at L2 breaks budget; at its L1 floor it is within budget."""
    chooser, _ = _chooser(table, BudgetPolicy.FALLBACK, spend=77.5)
    choice = chooser.choose(task_class="code-review", tenant_id=TENANT, complexity=90.0)
    assert choice.tier == "L1"
    assert choice.budget_action == "allow"
    assert choice.warning is None


# ------------------------------------------------------------------ hard cap
def test_hard_cap_blocks_even_under_warn_policy(table) -> None:
    chooser, _ = _chooser(table, BudgetPolicy.WARN, spend=99.5)
    with pytest.raises(BudgetBlocked):
        chooser.choose(task_class="code-author", tenant_id=TENANT, complexity=5.0)


# ------------------------------------------------------ no budget enforcer
def test_no_enforcer_allows_everything(table) -> None:
    chooser = ModelChooser(table=table, tokens_per_call=1_000_000)
    choice = chooser.choose(task_class="code-author", complexity=5.0)
    assert choice.budget_action == "allow"
    assert choice.warning is None


# -------------------------------------------------- seeded budgets behavior
def test_seeded_stop_tenant_beta_blocks(seeded_enforcer, table) -> None:
    """tenant-beta ships with policy=stop; exhaust it and expect a block."""
    seeded_enforcer.ledger.add_spend("tenant-beta", 50.0)  # 100% of $50
    chooser = ModelChooser(table=table, budget_enforcer=seeded_enforcer)
    with pytest.raises(BudgetBlocked):
        chooser.choose(task_class="code-author", tenant_id="tenant-beta", complexity=5.0)
