"""Unit controls for the model and the audit rail."""

from __future__ import annotations

import pytest

from integrations.erp.tx import audit, model

AT = "2026-03-02T09:00:00Z"


def test_action_vocabulary_is_closed() -> None:
    rail = audit.Rail()
    with pytest.raises(model.Refused) as caught:
        rail.append(at=AT, actor="nc", action="signed", kind="quotation", ref="QUO-1")
    assert caught.value.reason == "unknown-action"


def test_a_rail_entry_needs_an_actor_a_time_and_a_subject() -> None:
    rail = audit.Rail()
    for kwargs, reason in (
        ({"actor": "", "at": AT, "ref": "QUO-1"}, "invalid-value"),
        ({"actor": "nc", "at": "", "ref": "QUO-1"}, "invalid-value"),
        ({"actor": "nc", "at": AT, "ref": ""}, "invalid-value"),
    ):
        with pytest.raises(model.Refused) as caught:
            rail.append(action=model.ACTION_DRAFT, kind="quotation", **kwargs)
        assert caught.value.reason == reason


def test_appending_returns_a_new_rail_and_leaves_the_old_one_alone() -> None:
    first = audit.Rail().append(
        at=AT, actor="nc", action=model.ACTION_DRAFT, kind="quotation", ref="QUO-1"
    )
    second = first.append(
        at=AT, actor="nc", action=model.ACTION_SUBMIT, kind="quotation", ref="QUO-1"
    )
    assert len(first) == 1
    assert len(second) == 2
    assert first.head != second.head


def test_the_chain_is_tamper_evident() -> None:
    rail = audit.Rail().append(
        at=AT, actor="nc", action=model.ACTION_DRAFT, kind="quotation", ref="QUO-1"
    )
    tampered = [entry.to_dict() for entry in rail]
    tampered[0]["note"] = "rewritten"
    findings = audit.Rail.from_list(tampered).verify()
    codes = {finding.code for finding in findings}
    assert codes == {"audit-broken"}
    assert "does not reproduce" in findings[0].detail


def test_reordering_two_entries_breaks_the_back_link() -> None:
    rail = audit.Rail()
    for action in (model.ACTION_DRAFT, model.ACTION_SUBMIT):
        rail = rail.append(at=AT, actor="nc", action=action, kind="quotation", ref="QUO-1")
    reversed_entries = list(rail.to_list())[::-1]
    findings = audit.Rail.from_list(reversed_entries).verify()
    assert {finding.code for finding in findings} == {"audit-broken"}


def test_a_rail_is_deterministic_given_the_same_inputs() -> None:
    def build() -> str:
        rail = audit.Rail()
        rail = rail.append(at=AT, actor="nc", action=model.ACTION_DRAFT, kind="quotation", ref="QUO-1")
        rail = rail.append(at=AT, actor="nc", action=model.ACTION_SUBMIT, kind="quotation", ref="QUO-1")
        return rail.head

    assert build() == build()
    assert audit.GENESIS == "0" * 64
    assert audit.Rail().head == audit.GENESIS


def test_a_document_views_its_own_body_rather_than_copying_it() -> None:
    document = model.TxDocument.of(
        "quotation", "QUO-1", {"doctype": "quotation", "state": "draft", "docstatus": 0}
    )
    assert (document.state, document.docstatus) == ("draft", 0)
    moved = document.advanced("submitted", 1)
    assert (moved.state, moved.docstatus) == ("submitted", 1)
    assert document.state == "draft", "advancing mutated the original document"
    assert moved.body["state"] == "submitted", "the body and the view disagree"


def test_a_document_without_a_state_or_docstatus_is_refused() -> None:
    with pytest.raises(model.Refused) as caught:
        model.TxDocument.of("quotation", "QUO-1", {"doctype": "quotation"})
    assert caught.value.reason == "missing-field"
    with pytest.raises(model.Refused) as second:
        model.TxDocument.of("quotation", "QUO-1", {"doctype": "quotation", "state": "draft"})
    assert second.value.reason == "missing-field"


def test_a_document_copies_the_body_it_is_given() -> None:
    source = {"doctype": "quotation", "state": "draft", "docstatus": 0}
    document = model.TxDocument.of("quotation", "QUO-1", source)
    source["state"] = "cancelled"
    assert document.state == "draft"
