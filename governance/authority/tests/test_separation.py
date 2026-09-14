"""Separation of duties is schema-enforced: executor != reviewer != auditor.

The assignment is REPLACED wholesale by the ``_item`` helper, so a test that
omits a duty really omits it (an overlay would merge the missing key back in).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

import model

AO_ITEM = "kushin77/agent-orchestrator#150"
CU_ITEM = "kushin77/capital-underwriting#1391"


def _item(document: Dict[str, Any], item_id: str, **sod: str) -> model.Matrix:
    """Return a matrix whose work item carries EXACTLY the given sod assignment."""
    patched = deepcopy(document)
    for item in patched["work_items"]:
        if item["id"] == item_id:
            item["sod"] = dict(sod)
    return model.build_matrix(patched)


def test_the_shipped_assignment_is_accepted(matrix: model.Matrix) -> None:
    for item_id in (AO_ITEM, CU_ITEM):
        decision = model.separation_of_duties(matrix, item_id)
        assert decision.ok, decision.render()


def test_the_reviewer_may_not_be_the_executor(document) -> None:
    matrix = _item(
        document, AO_ITEM, executor="ao-soldier-1", reviewer="ao-soldier-1", auditor="ao-auditor-1"
    )
    assert matrix.valid
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-executor-equals-reviewer"


def test_the_auditor_may_not_be_the_executor(document) -> None:
    matrix = _item(
        document, AO_ITEM, executor="ao-soldier-1", reviewer="ao-platoon-leader-1", auditor="ao-soldier-1"
    )
    assert model.separation_of_duties(matrix, AO_ITEM).reason == "sod-executor-equals-auditor"


def test_the_auditor_may_not_be_the_reviewer(document) -> None:
    matrix = _item(
        document, AO_ITEM, executor="ao-soldier-1", reviewer="ao-auditor-1", auditor="ao-auditor-1"
    )
    assert model.separation_of_duties(matrix, AO_ITEM).reason == "sod-reviewer-equals-auditor"


def test_an_auditor_posture_can_never_fill_the_executor_duty(document) -> None:
    matrix = _item(
        document,
        AO_ITEM,
        executor="ao-auditor-1",
        reviewer="ao-platoon-leader-1",
        auditor="ao-auditor-1",
    )
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-executor-equals-auditor"


def test_an_auditor_can_never_execute_what_it_audits_even_with_a_third_principal(document) -> None:
    matrix = _item(
        document,
        AO_ITEM,
        executor="ao-auditor-1",
        reviewer="ao-platoon-leader-1",
        auditor="ao-general",
    )
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-auditor-cannot-execute"


def test_a_duty_filled_by_the_wrong_posture_is_denied(document) -> None:
    matrix = _item(
        document,
        AO_ITEM,
        executor="ao-platoon-leader-1",
        reviewer="ao-general",
        auditor="ao-auditor-1",
    )
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-posture-mismatch"


def test_a_duty_with_no_assignment_is_denied(document) -> None:
    matrix = _item(document, AO_ITEM, executor="ao-soldier-1")
    assert matrix.valid, "a missing duty is a decision-level denial, not a document defect"
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-duty-unassigned"


def test_an_empty_duty_id_is_a_document_defect_not_a_pass(document) -> None:
    """An empty actor id is not a valid principal id: the matrix is invalid, so CANNOT-ASSESS."""
    matrix = _item(document, AO_ITEM, executor="ao-soldier-1", reviewer="", auditor="ao-auditor-1")
    assert not matrix.valid
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.cannot_assess
    assert decision.reason == "matrix-invalid"


def test_an_unknown_principal_in_a_duty_is_denied(document) -> None:
    matrix = _item(
        document, AO_ITEM, executor="ghost-agent", reviewer="ao-platoon-leader-1", auditor="ao-auditor-1"
    )
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-actor-unknown"


def test_a_principal_from_another_repos_fleet_is_denied(document) -> None:
    matrix = _item(
        document, AO_ITEM, executor="cu-soldier-1", reviewer="ao-platoon-leader-1", auditor="ao-auditor-1"
    )
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-actor-out-of-scope"


def test_an_auditor_dispatched_below_the_executor_is_denied(document) -> None:
    matrix = _item(
        document, AO_ITEM, executor="ao-lieutenant", reviewer="ao-platoon-leader-1", auditor="ao-auditor-1"
    )
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-auditor-tier-below-executor"


def test_an_auditor_at_or_above_the_executor_tier_is_accepted(document) -> None:
    """The tier floor is relative: the auditor must not be dispatched below the executor."""
    below = _item(
        document, AO_ITEM, executor="ao-lieutenant", reviewer="ao-platoon-leader-1", auditor="ao-auditor-1"
    )
    assert model.separation_of_duties(below, AO_ITEM).reason == "sod-auditor-tier-below-executor"
    matched = model.matrix_from_overlay(
        document,
        {
            "principals": [
                {"id": "ao-fleet", "actors": [{"id": "ao-auditor-1", "model_tier": "MAX"}]},
            ],
            "work_items": [
                {
                    "id": AO_ITEM,
                    "sod": {
                        "executor": "ao-lieutenant",
                        "reviewer": "ao-platoon-leader-1",
                        "auditor": "ao-auditor-1",
                    },
                }
            ],
        },
    )
    assert matched.valid
    decision = model.separation_of_duties(matched, AO_ITEM)
    assert decision.ok, decision.render()


def test_an_unknown_work_item_is_cannot_assess(matrix: model.Matrix) -> None:
    decision = model.separation_of_duties(matrix, "kushin77/agent-orchestrator#999999")
    assert decision.cannot_assess
    assert decision.reason == "work-item-unknown"


def test_separation_of_duties_on_an_invalid_matrix_is_cannot_assess(document) -> None:
    matrix = model.matrix_from_overlay(document, {"principals": [{"id": "ao-fleet", "cross_repo": True}]})
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.cannot_assess
    assert decision.reason == "matrix-invalid"


def test_the_shipped_fleets_can_satisfy_separation_of_duties(matrix: model.Matrix) -> None:
    """Every fleet declares an executor, a reviewer and an auditor posture."""
    for principal in matrix.principals:
        if principal.kind != "repo-fleet":
            continue
        postures = {actor.posture for actor in principal.actors}
        assert set(model.DUTIES) <= postures, principal.id


def test_the_distinctness_rule_names_the_collision_on_its_own(document) -> None:
    """Unit-level: the named rule reports the colliding duty pair."""
    matrix = _item(
        document, AO_ITEM, executor="ao-soldier-1", reviewer="ao-soldier-1", auditor="ao-auditor-1"
    )
    item = matrix.work_item(AO_ITEM)
    assert item is not None
    assigned = {duty: matrix.actor(item.sod[duty]) for duty in model.DUTIES}
    collision: Optional[Any] = model._distinct_duty_violation(assigned)
    assert collision == ("sod-executor-equals-reviewer", ("executor", "reviewer"))


def test_the_two_rules_are_defence_in_depth(monkeypatch, document) -> None:
    """Weakening ONE rule still denies: the posture rule and the distinctness rule overlap."""
    matrix = _item(
        document, AO_ITEM, executor="ao-soldier-1", reviewer="ao-soldier-1", auditor="ao-auditor-1"
    )
    monkeypatch.setattr(model, "_distinct_duty_violation", lambda assigned: None)
    still_denied = model.separation_of_duties(matrix, AO_ITEM)
    assert still_denied.denied
    assert still_denied.reason == "sod-posture-mismatch"
    monkeypatch.undo()

    monkeypatch.setattr(model, "_posture_violation", lambda assigned: None)
    still_denied = model.separation_of_duties(matrix, AO_ITEM)
    assert still_denied.denied
    assert still_denied.reason == "sod-executor-equals-reviewer"
    monkeypatch.undo()


def test_weakening_both_rules_lets_a_collision_through(monkeypatch, document) -> None:
    """Negative control: the SoD DENIAL really comes from these two rules, not from noise."""
    matrix = _item(
        document, AO_ITEM, executor="ao-soldier-1", reviewer="ao-soldier-1", auditor="ao-auditor-1"
    )
    assert model.separation_of_duties(matrix, AO_ITEM).denied
    monkeypatch.setattr(model, "_distinct_duty_violation", lambda assigned: None)
    monkeypatch.setattr(model, "_posture_violation", lambda assigned: None)
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.ok, decision.render()
    monkeypatch.undo()
    assert model.separation_of_duties(matrix, AO_ITEM).denied


def test_mutating_the_distinctness_rule_alone_is_still_denied(monkeypatch, document) -> None:
    """Disabling only the named distinctness rule still denies — see the defence-in-depth test."""
    matrix = _item(
        document, AO_ITEM, executor="ao-soldier-1", reviewer="ao-soldier-1", auditor="ao-auditor-1"
    )
    monkeypatch.setattr(model, "_distinct_duty_violation", lambda assigned: None)
    decision = model.separation_of_duties(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "sod-posture-mismatch"
    monkeypatch.undo()
    assert model.separation_of_duties(matrix, AO_ITEM).reason == "sod-executor-equals-reviewer"
