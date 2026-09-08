"""Security guardrail: security/IaC/governance never below L1 (issue #17 AC 2).

A task class tagged with a security guardrail must never resolve to an L0
model — even at trivial difficulty, and even if the config mistakenly declares
a cheap default tier. The floor is enforced in the chooser at route time, so
this is a negative test on the routing behavior, not just a config assertion.
"""

from __future__ import annotations

import pytest
import yaml

import loader
from chooser import ModelChooser


@pytest.fixture()
def chooser(table):
    return ModelChooser(table=table)


GUARDED_CLASSES = ["security-review", "infra-authoring", "governance-audit", "architecture-decision"]


@pytest.mark.parametrize("task_class", GUARDED_CLASSES)
def test_guarded_classes_never_route_l0(chooser, table, task_class) -> None:
    """At trivial difficulty a guarded class must still route >= security floor."""
    floor_rank = table.rank(table.security_floor)
    for complexity in (0.0, 1.0, 10.0):
        choice = chooser.choose(task_class=task_class, complexity=complexity)
        assert table.rank(choice.tier) >= floor_rank, (
            f"{task_class} routed to {choice.tier} at complexity {complexity}"
        )
        assert choice.tier != "L0"


def test_security_review_l0_model_never_selected(chooser, table) -> None:
    l0_model_ids = {m.id for m in table.tier("L0").models}
    for complexity in (0.0, 25.0, 50.0, 95.0):
        choice = chooser.choose(task_class="security-review", complexity=complexity)
        assert choice.model.id not in l0_model_ids, (
            f"security-review picked L0 model {choice.model.id} "
            f"at complexity {complexity}"
        )


def test_guardrail_floor_wins_over_misconfigured_low_default() -> None:
    """A guarded class whose config default is L0 still cannot route to L0.

    The floor clamp in the chooser is the enforcement point; the loader only
    requires maxTier to reach the floor (L0 default with L2 max is legal
    config, and routing must still refuse L0).
    """
    data = yaml.safe_load(loader.TIERS_PATH.read_text(encoding="utf-8"))
    data["taskClasses"]["misconfigured-sec"] = {
        "capability": "security-review",
        "defaultTier": "L0",  # wrong: should be L1
        "maxTier": "L2",
        "guardrail": "security",
    }
    table = loader.parse_tier_table(data)
    chooser = ModelChooser(table=table)
    choice = chooser.choose(task_class="misconfigured-sec", complexity=0.0)
    assert choice.tier == "L1"  # floored at the security floor, never L0


def test_unguarded_classes_may_route_l0(chooser) -> None:
    """Control: a normal class is allowed at L0 (guardrail is not global)."""
    assert chooser.choose(task_class="code-author", complexity=5.0).tier == "L0"
