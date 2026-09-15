"""Unit controls for the two seams that resolve this lane's definitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.erp.tx import definitions as definitions_module
from integrations.erp.tx import indexer, model
from integrations.erp.tx.definitions import DefinitionSet


def test_the_index_is_required_not_optional(tmp_path: Path) -> None:
    with pytest.raises(model.Refused) as caught:
        indexer.load_index(tmp_path)
    assert caught.value.reason == "index-unavailable"
    assert "does not exist" in caught.value.detail


def test_an_unreadable_index_is_a_refusal_not_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / indexer.INDEX_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(model.Refused) as caught:
        indexer.load_index(tmp_path)
    assert caught.value.reason == "index-unavailable"


def test_the_index_must_declare_items(tmp_path: Path) -> None:
    path = tmp_path / indexer.INDEX_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": "x"}), encoding="utf-8")
    with pytest.raises(model.Refused) as caught:
        indexer.load_index(tmp_path)
    assert caught.value.reason == "index-unavailable"
    assert "declares no items" in caught.value.detail


def test_a_catalogue_document_that_will_not_load_is_named(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"id": "broken"}), encoding="utf-8")
    with pytest.raises(model.Refused) as caught:
        indexer.read_lane_document(path, where="catalogue/broken.json")
    assert caught.value.reason == "catalogue-invalid"
    assert "broken.json" in caught.value.detail


def test_a_catalogue_document_claiming_copied_code_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "copied.json"
    path.write_text(
        json.dumps(
            {
                "id": "copied",
                "family": "selling",
                "owning_issue": 648,
                "provenance": {"code_copied": True},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(model.Refused) as caught:
        indexer.read_lane_document(path, where="catalogue/copied.json")
    assert caught.value.reason == "catalogue-invalid"
    assert "GR-10" in caught.value.detail


def test_query_filters_are_conjunctive_and_ordered() -> None:
    items = (
        indexer.IndexItem(id="b", kind="pattern-template", path="p/b.json", title="B"),
        indexer.IndexItem(id="a", kind="pattern-template", path="p/a.json", title="A"),
        indexer.IndexItem(id="c", kind="governance", path="p/c.md", title="C"),
    )
    assert [item.id for item in indexer.query(items, kind="pattern-template")] == ["a", "b"]
    assert [
        item.id for item in indexer.query(items, kind="pattern-template", path_prefix="p/a")
    ] == ["a"]


def test_an_issue_with_no_declarations_is_refused() -> None:
    with pytest.raises(model.Refused) as caught:
        indexer.catalogue_documents(issue=999999)
    assert caught.value.reason == "undeclared-document"


def test_the_definition_set_derives_its_chain(defs: DefinitionSet) -> None:
    assert defs.chain == ("quotation", "sales-order", "delivery-note", "sales-invoice")
    assert defs.stock_kinds == ("delivery-note",)
    assert defs.accounting_kind == "gl-posting"
    assert defs.voucher_sources == (
        "sales-invoice",
        "purchase-invoice",
        "stock-entry",
        "journal-entry",
        "payment",
    )


def test_the_link_fields_are_the_ones_the_schemas_declare(defs: DefinitionSet) -> None:
    assert defs.link_field("sales-order") == "quotation"
    assert defs.link_field("delivery-note") == "against_sales_order"
    assert defs.link_field("sales-invoice") == "against_delivery_note"
    assert defs.raised_against("sales-invoice") == "delivery-note"


def test_a_family_with_no_link_has_no_link_field(defs: DefinitionSet) -> None:
    assert defs.link_of("gl-posting") is None
    with pytest.raises(model.Refused) as caught:
        defs.link_field("gl-posting")
    assert caught.value.reason == "definitions-invalid"


def test_a_kind_the_spine_does_not_drive_is_refused_by_reason(defs: DefinitionSet) -> None:
    with pytest.raises(model.Refused) as outside:
        defs.require("stock-entry")
    assert outside.value.reason == "not-drivable"
    assert "outside it" in outside.value.detail
    with pytest.raises(model.Refused) as unknown:
        defs.require("sprocket")
    assert unknown.value.reason == "unknown-document-kind"
    with pytest.raises(model.Refused) as empty:
        defs.require("")
    assert empty.value.reason == "invalid-value"


def test_the_submit_and_cancel_states_are_read_off_the_workflows(defs: DefinitionSet) -> None:
    for kind in defs.chain:
        initial = defs.initial_state(kind)
        assert defs.docstatus_of(kind, initial) == 0
        submitted = defs.submitted_state(kind)
        assert defs.docstatus_of(kind, submitted) == 1
        assert defs.cancellation_state(kind)
        assert defs.docstatus_of(kind, defs.cancellation_state(kind)) == 2


def test_the_submit_action_is_derived_from_the_workflow(defs: DefinitionSet) -> None:
    """The action is found by what it reaches, not by its name."""
    for kind in defs.chain:
        action = defs.action_reaching(
            kind,
            defs.initial_state(kind),
            lambda state: state.docstatus == 1 and not state.terminal,
        )
        assert action in defs.actions_from(kind, defs.initial_state(kind))
        assert (
            defs.advance(
                {"doctype": kind, "state": defs.initial_state(kind)}, action
            )
            == defs.submitted_state(kind)
        )


def test_an_ambiguous_or_impossible_step_is_refused(defs: DefinitionSet) -> None:
    with pytest.raises(model.Refused) as impossible:
        defs.action_reaching(
            "sales-order",
            "completed",
            lambda state: state.docstatus == 1 and not state.terminal,
        )
    assert impossible.value.reason == "illegal-transition"
    with pytest.raises(model.Refused) as ambiguous:
        defs.action_reaching("sales-order", "submitted", lambda state: state.terminal)
    assert ambiguous.value.reason == "definitions-invalid"


def test_a_master_is_validated_but_not_driven(defs: DefinitionSet) -> None:
    item = defs.validate_master(
        "item",
        {
            "doctype": "item",
            "id": "ITEM-X",
            "name": "X",
            "uom": "Nos",
            "is_stock_item": True,
        },
    )
    assert item["id"] == "ITEM-X"
    with pytest.raises(model.Refused) as caught:
        defs.validate_master("sprocket", {})
    assert caught.value.reason == "unknown-document-kind"
    with pytest.raises(model.Refused) as shaped:
        defs.validate_master("item", {"doctype": "item", "id": "ITEM-X"})
    assert shaped.value.reason == "schema-violation"


def test_the_stock_family_is_required_to_be_unique(defs: DefinitionSet) -> None:
    import dataclasses

    with pytest.raises(model.Refused) as caught:
        dataclasses.replace(defs, stock_kinds=()).stock_kind()
    assert caught.value.reason == "definitions-invalid"
    assert "0 families" in caught.value.detail


def test_the_report_names_what_is_not_driven(defs: DefinitionSet) -> None:
    report = defs.to_dict()
    assert report["issue"] == definitions_module.LANE_ISSUE
    assert report["chain"] == list(defs.chain)
    undriven = {row["id"]: row["reason"] for row in report["undriven"]}
    assert set(undriven) == {"journal-entry", "payment-entry", "stock-entry"}
    assert all(reason for reason in undriven.values())
