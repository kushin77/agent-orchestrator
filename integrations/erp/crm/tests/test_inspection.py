"""Quality-inspection records and their outcome transitions (issue #650, AC3).

Acceptance criterion 3 asks for "quality-inspection records and their outcome
transitions ... covered by tests with negative controls". The controls here are
the ones that matter: an outcome the declaration does not declare is refused by
name, an outcome the current state cannot reach is refused by name, and a passed
inspection is terminal *because the declaration says so*.
"""

from __future__ import annotations

import pytest

from integrations.erp.crm import flows, workflow
from integrations.erp.crm.model import KIND_INSPECTION, Document, Refused

AT = "2026-09-09T09:00:00Z"
FIELDS = {"template": "incoming", "subject": "PROJ-0001 batch 1", "sample_size": 20}


def started(space):
    return flows.start_inspection(space, "INSP-0001", FIELDS, actor="qa-1", at=AT, title="Incoming")


def test_the_carried_outcomes_are_the_declared_ones(definitions) -> None:
    """The vocabulary the code carries cannot drift from the declaration."""
    declared = tuple(
        state for state in definitions.kind(KIND_INSPECTION).states if state in flows.INSPECTION_OUTCOMES
    )
    assert tuple(sorted(declared)) == tuple(sorted(flows.INSPECTION_OUTCOMES))
    assert flows.INSPECTION_OUTCOMES == ("failed", "passed")


def test_an_inspection_starts_pending_and_is_moved_on_by_start(definitions) -> None:
    assert definitions.kind(KIND_INSPECTION).initial == "pending"
    assert definitions.kind(KIND_INSPECTION).allows("pending", "in-progress")


def test_the_golden_path_fails_then_re_inspects_then_passes(golden) -> None:
    entries = golden.workspace.rail.for_ref("INSP-0001")
    assert [(entry.action, entry.from_state, entry.to_state) for entry in entries] == [
        ("create", "", "pending"),
        ("advance", "pending", "in-progress"),
        ("outcome", "in-progress", "failed"),
        ("reinspect", "failed", "pending"),
        ("advance", "pending", "in-progress"),
        ("outcome", "in-progress", "passed"),
    ]
    assert golden.workspace.get("INSP-0001").state == "passed"


def test_a_failed_inspection_is_re_inspectable_rather_than_terminal(definitions) -> None:
    declared = definitions.kind(KIND_INSPECTION)
    assert declared.allows("failed", "pending")
    assert not declared.allows("in-progress", "pending")


def test_a_passed_inspection_is_terminal_because_the_declaration_says_so(definitions) -> None:
    declared = definitions.kind(KIND_INSPECTION)
    assert declared.transitions["passed"] == ()
    document = Document(
        kind=KIND_INSPECTION, id="INSP-0001", tenant="acme", state="passed", fields=FIELDS
    )
    assert workflow.is_terminal(document, definitions)
    assert workflow.transition_targets(document, definitions) == ()


def test_outcomes_are_offered_from_the_current_state_only(space) -> None:
    space = started(space)
    assert flows.inspection_outcomes(space, "INSP-0001") == ("failed", "passed")
    space = flows.record_inspection_outcome(space, "INSP-0001", "failed", actor="qa-1", at=AT)
    assert flows.inspection_outcomes(space, "INSP-0001") == ()


def test_an_outcome_outside_the_declared_set_is_refused_by_name(space) -> None:
    space = started(space)
    with pytest.raises(Refused, match="unknown-state") as caught:
        flows.record_inspection_outcome(space, "INSP-0001", "probably", actor="qa-1", at=AT)
    assert "probably" in caught.value.detail
    assert "failed, passed" in caught.value.detail


def test_an_outcome_the_current_state_cannot_reach_is_refused_by_name(space) -> None:
    """A declared outcome that this state does not license is illegal, not unknown."""
    space = flows.create_document(
        space, KIND_INSPECTION, "INSP-0001", FIELDS, actor="qa-1", at=AT
    )
    with pytest.raises(Refused, match="illegal-transition") as caught:
        flows.record_inspection_outcome(space, "INSP-0001", "passed", actor="qa-1", at=AT)
    assert "pending" in caught.value.detail and "in-progress" in caught.value.detail


def test_re_inspecting_an_in_progress_inspection_is_refused_by_name(space) -> None:
    space = started(space)
    with pytest.raises(Refused, match="illegal-transition") as caught:
        flows.reinspect(space, "INSP-0001", actor="qa-1", at=AT)
    assert "pending" in caught.value.detail


def test_a_passed_inspection_cannot_be_re_outcomed(space) -> None:
    space = started(space)
    space = flows.record_inspection_outcome(space, "INSP-0001", "passed", actor="qa-1", at=AT)
    with pytest.raises(Refused, match="illegal-transition"):
        flows.record_inspection_outcome(space, "INSP-0001", "failed", actor="qa-1", at=AT)
    with pytest.raises(Refused, match="illegal-transition"):
        flows.reinspect(space, "INSP-0001", actor="qa-1", at=AT)


def test_an_inspection_must_declare_its_template_and_subject(space) -> None:
    with pytest.raises(Refused, match="missing-field") as caught:
        flows.create_document(space, KIND_INSPECTION, "INSP-0001", {"sample_size": 1}, actor="qa-1", at=AT)
    assert "template" in caught.value.detail or "subject" in caught.value.detail


def test_an_undeclared_template_is_refused_by_name(space) -> None:
    with pytest.raises(Refused, match="unknown-vocabulary-term") as caught:
        flows.create_document(
            space,
            KIND_INSPECTION,
            "INSP-0001",
            {"template": "vibes", "subject": "batch"},
            actor="qa-1",
            at=AT,
        )
    assert "vibes" in caught.value.detail


def test_the_outcome_transitions_are_audited(golden) -> None:
    rail = golden.workspace.rail
    assert [entry.action for entry in rail.for_ref("INSP-0001")].count("outcome") == 2
    assert [entry.action for entry in rail.for_ref("INSP-0001")].count("reinspect") == 1
    assert rail.verify() == []
