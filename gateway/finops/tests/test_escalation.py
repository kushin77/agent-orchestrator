"""Escalation tests: on difficulty and on failure (issue #17 AC 1 + 5).

Covers:
- difficulty score raises the target tier through the escalation thresholds
- escalation caps at the task class maxTier (per-task-type tier caps)
- failure escalation climbs one tier per call and refuses to go past maxTier
"""

from __future__ import annotations

import pytest

from chooser import EscalationCapReached, ModelChooser


@pytest.fixture()
def chooser(table):
    return ModelChooser(table=table)


# ------------------------------------------------------- difficulty escalation
def test_research_escalates_on_difficulty(chooser) -> None:
    assert chooser.choose(task_class="research", complexity=10.0).tier == "L0"
    assert chooser.choose(task_class="research", complexity=50.0).tier == "L1"
    assert chooser.choose(task_class="research", complexity=80.0).tier == "L2"


def test_difficulty_below_threshold_keeps_cheapest(chooser) -> None:
    # L0->L1 threshold is 40.0; 39 stays L0.
    assert chooser.choose(task_class="research", complexity=39.0).tier == "L0"


def test_code_author_capped_at_l1_even_when_very_hard(chooser) -> None:
    # code-author maxTier = L1: per-task-type cap beats raw difficulty.
    choice = chooser.choose(task_class="code-author", complexity=95.0)
    assert choice.tier == "L1"


def test_mechanical_classes_capped_at_l0(chooser) -> None:
    # finops-meter / memory-ops maxTier = L0: they never escalate.
    assert chooser.choose(task_class="finops-meter", complexity=95.0).tier == "L0"
    assert chooser.choose(task_class="memory-ops", complexity=95.0).tier == "L0"


def test_data_analysis_escalates_to_l2(chooser) -> None:
    # data-analysis maxTier = L2 (no cap below the top).
    assert chooser.choose(task_class="data-analysis", complexity=85.0).tier == "L2"


# ---------------------------------------------------------- failure escalation
def test_failure_escalation_climbs_one_tier_at_a_time(chooser) -> None:
    choice = chooser.choose(task_class="research", complexity=5.0)
    assert choice.tier == "L0"

    step1 = chooser.escalate_on_failure(choice, trigger="failure")
    assert step1.tier == "L1"
    assert any("escalate L0 -> L1" in r for r in step1.reasons)

    step2 = chooser.escalate_on_failure(step1, trigger="timeout")
    assert step2.tier == "L2"
    assert any("escalate L1 -> L2" in r for r in step2.reasons)


def test_failure_escalation_refuses_past_max_tier(chooser) -> None:
    choice = chooser.choose(task_class="research", complexity=5.0)  # L0
    step1 = chooser.escalate_on_failure(choice)  # -> L1
    step2 = chooser.escalate_on_failure(step1)  # -> L2 (max)
    with pytest.raises(EscalationCapReached, match="capped at L2"):
        chooser.escalate_on_failure(step2)


def test_failure_escalation_respects_class_cap_from_the_start(chooser) -> None:
    # code-author maxTier = L1: escalating from an L1 choice must refuse.
    choice = chooser.choose(task_class="code-author", complexity=90.0)  # capped L1
    assert choice.tier == "L1"
    with pytest.raises(EscalationCapReached, match="capped at L1"):
        chooser.escalate_on_failure(choice)


def test_failure_escalation_on_l0_capped_class(chooser) -> None:
    choice = chooser.choose(task_class="finops-meter", complexity=5.0)  # L0 max L0
    with pytest.raises(EscalationCapReached):
        chooser.escalate_on_failure(choice)
