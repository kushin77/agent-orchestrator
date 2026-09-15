"""The state machines and the audited advance (issue #650, AC1)."""

from __future__ import annotations

import pytest

from integrations.erp.crm import audit, flows, workflow
from integrations.erp.crm.model import ACTIONS, Document, Refused

AT = "2026-09-01T09:00:00Z"


def lead(state: str = "new") -> Document:
    return Document(
        kind="lead",
        id="LEAD-0001",
        tenant="acme",
        state=state,
        fields={"company": "Northwind Traders", "stage": "new"},
    )


def test_transition_targets_come_from_the_declaration(definitions) -> None:
    assert workflow.transition_targets(lead("new"), definitions) == ("contacted", "lost")
    assert workflow.transition_targets(lead("converted"), definitions) == ()


def test_is_terminal_follows_the_declaration(definitions) -> None:
    assert workflow.is_terminal(lead("converted"), definitions)
    assert not workflow.is_terminal(lead("new"), definitions)


def test_accepts_child_work_follows_the_declared_open_states(definitions) -> None:
    task = Document(kind="task", id="TASK-0001", tenant="acme", state="open", fields={})
    closed = task.with_state("done")
    assert workflow.accepts_child_work(task, definitions)
    assert not workflow.accepts_child_work(closed, definitions)


def test_a_legal_transition_moves_the_document(definitions) -> None:
    document, rail = workflow.advance(
        lead("new"),
        "contacted",
        actor="rep-1",
        at=AT,
        definitions=definitions,
        rail=audit.Rail(),
    )
    assert document.state == "contacted"
    assert lead("new").state == "new"  # the original was not touched
    assert rail.verify() == []
    assert len(rail) == 1


def test_the_rail_records_both_ends_of_the_transition(definitions) -> None:
    _, rail = workflow.advance(
        lead("qualified"),
        "converted",
        actor="rep-1",
        at=AT,
        definitions=definitions,
        rail=audit.Rail(),
        action="convert",
        note="signed",
    )
    entry = rail.entries[0]
    assert (entry.kind, entry.ref, entry.from_state, entry.to_state) == (
        "lead",
        "LEAD-0001",
        "qualified",
        "converted",
    )
    assert (entry.action, entry.actor, entry.note) == ("convert", "rep-1", "signed")


def test_an_illegal_transition_is_refused_and_names_the_reachable_states(definitions) -> None:
    with pytest.raises(Refused, match="illegal-transition") as caught:
        workflow.advance(
            lead("new"),
            "qualified",
            actor="rep-1",
            at=AT,
            definitions=definitions,
            rail=audit.Rail(),
        )
    assert "contacted, lost" in caught.value.detail


def test_a_terminal_state_says_so_when_it_refuses(definitions) -> None:
    with pytest.raises(Refused, match="illegal-transition") as caught:
        workflow.advance(
            lead("converted"),
            "qualified",
            actor="rep-1",
            at=AT,
            definitions=definitions,
            rail=audit.Rail(),
        )
    assert "terminal state" in caught.value.detail


def test_a_state_of_another_kind_is_refused_before_legality(definitions) -> None:
    with pytest.raises(Refused, match="unknown-state") as caught:
        workflow.assert_transition(lead("new"), "won", definitions)
    assert "won" in caught.value.detail


def test_viewing_legality_is_pure(definitions) -> None:
    """A check that mutated the document it inspects would make review unsafe."""
    document = lead("new")
    workflow.assert_transition(document, "contacted", definitions)
    workflow.is_terminal(document, definitions)
    assert document.state == "new"


def test_the_audited_actions_are_the_closed_vocabulary(definitions) -> None:
    for action in ACTIONS:
        _, rail = workflow.advance(
            lead("new"),
            "contacted",
            actor="rep-1",
            at=AT,
            definitions=definitions,
            rail=audit.Rail(),
            action=action,
        )
        assert rail.entries[0].action == action


def test_every_declared_transition_is_legal_when_asserted(definitions) -> None:
    """The machine and the declaration cannot disagree: every declared edge is accepted."""
    for name in sorted(definitions.kinds):
        kind = definitions.kind(name)
        for state, targets in kind.transitions.items():
            document = Document(kind=name, id="DOC-0001", tenant="acme", state=state, fields={})
            for target in targets:
                workflow.assert_transition(document, target, definitions)


def test_workspace_advance_uses_the_module_level_machine(space, monkeypatch, definitions) -> None:
    """The flow layer reaches the machine through the module, so the machine is the choke point."""
    space = flows.create_document(
        space,
        "lead",
        "LEAD-0001",
        {"company": "X", "stage": "new"},
        actor="rep-1",
        at=AT,
    )
    calls = []
    original = workflow.advance

    def recording(*args, **kwargs):
        calls.append(kwargs.get("target") or (args[1] if len(args) > 1 else None))
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow, "advance", recording)
    space = flows.advance(space, "LEAD-0001", "contacted", actor="rep-1", at=AT)
    assert calls == ["contacted"]
    assert space.get("LEAD-0001").state == "contacted"
