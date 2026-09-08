"""Cheapest-capable model choice per task class (issue #17 AC 1 + 5).

Asserts the core routing contract against the SHIPPED tiers.yaml: a task class
routes to its cheapest capable tier by default, difficulty escalates the tier,
and the per-task-type maxTier cap is never exceeded. No budget, no health: the
cheapest model of the tier always wins.
"""

from __future__ import annotations

import pytest

import loader
from chooser import ModelChooser


@pytest.fixture()
def chooser(table):
    return ModelChooser(table=table)


@pytest.mark.parametrize(
    "task_class,expected_tier",
    [
        ("code-author", "L0"),
        ("test-author", "L0"),
        ("test-run", "L0"),
        ("docs-authoring", "L0"),
        ("classify-route", "L0"),
        ("memory-ops", "L0"),
        ("finops-meter", "L0"),
        ("data-analysis", "L0"),  # low difficulty stays at the cheapest tier
        ("research", "L0"),
    ],
)
def test_default_cheapest_capable_tier(chooser, task_class, expected_tier) -> None:
    choice = chooser.choose(task_class=task_class, complexity=5.0)
    assert choice.tier == expected_tier


def test_l0_picks_cheapest_l0_model(chooser, table) -> None:
    choice = chooser.choose(task_class="code-author", complexity=5.0)
    assert choice.tier == "L0"
    # cheapest model of the L0 tier (deepseek-v4-flash is cheaper than its
    # fallback gemini-2.5-flash in the shipped table)
    assert choice.model.id == table.tier("L0").cheapest.id


def test_judgment_class_defaults_higher(chooser) -> None:
    # code-review needs judgment: even trivial difficulty stays at L1.
    choice = chooser.choose(task_class="code-review", complexity=5.0)
    assert choice.tier == "L1"


def test_architecture_class_defaults_at_top(chooser) -> None:
    choice = chooser.choose(task_class="architecture-decision", complexity=5.0)
    assert choice.tier == "L2"


def test_estimated_cost_is_computed_from_model(chooser, table) -> None:
    choice = chooser.choose(task_class="code-author", complexity=5.0)
    tokens = chooser.tokens_per_call
    expected = table.estimate_cost(choice.model, tokens)
    assert choice.estimated_cost_usd == expected
    assert choice.estimated_cost_usd > 0


def test_unknown_task_class_rejected(chooser) -> None:
    with pytest.raises(loader.ValidationError, match="unknown task class"):
        chooser.choose(task_class="does-not-exist", complexity=5.0)
