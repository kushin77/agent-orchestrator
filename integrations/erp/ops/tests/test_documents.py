"""Envelope parsing: each field-level mistake refused under its own code."""

from __future__ import annotations

from typing import Any, Dict

from integrations.erp.ops import documents
from integrations.erp.ops.model import Model
from .helpers import assert_refused


def valid_rfq(**overrides: Any) -> Dict[str, Any]:
    """A request for quotation that validates, with any field overridden."""
    document: Dict[str, Any] = {
        "doctype": "rfq",
        "id": "RFQ-T1",
        "state": "draft",
        "docstatus": 0,
        "company": "COMPANY-1",
        "currency": "EUR",
        "transaction_date": "2026-09-15",
        "suppliers": ["SUP-1"],
        "lines": [{"item_code": "RAW-A", "qty": 1, "rate": 5.0}],
    }
    document.update(overrides)
    return document


def test_a_valid_document_parses(model: Model) -> None:
    parsed = documents.parse(model, valid_rfq(), kind="rfq")
    assert parsed["doctype"] == "rfq"
    assert parsed["suppliers"] == ["SUP-1"]


def test_a_parsed_document_is_a_copy(model: Model) -> None:
    """A caller's mapping must not become the stored document."""
    source = valid_rfq()
    parsed = documents.parse(model, source, kind="rfq")
    assert parsed is not source


def test_a_non_mapping_is_refused(model: Model) -> None:
    assert_refused(
        lambda: documents.parse(model, ["not", "a", "document"]),
        "invalid-value",
        needle="must be a mapping",
    )


def test_a_missing_required_field_names_it(model: Model) -> None:
    detail = assert_refused(
        lambda: documents.parse(model, {"doctype": "rfq", "id": "RFQ-T1"}),
        "missing-field",
        needle="suppliers",
    )
    assert "lines" in detail


def test_an_unknown_field_is_refused(model: Model) -> None:
    assert_refused(
        lambda: documents.parse(model, valid_rfq(planet="Mars"), kind="rfq"),
        "unknown-field",
        needle="planet",
    )


def test_a_field_of_the_wrong_shape_is_refused(model: Model) -> None:
    assert_refused(
        lambda: documents.parse(model, valid_rfq(suppliers="SUP-1"), kind="rfq"),
        "invalid-value",
        needle="suppliers must be array",
    )


def test_a_value_outside_its_enum_is_a_schema_violation(model: Model) -> None:
    detail = assert_refused(
        lambda: documents.parse(model, valid_rfq(docstatus=7), kind="rfq"),
        "schema-violation",
        needle="docstatus",
    )
    assert "not one of" in detail


def test_an_unknown_family_is_refused(model: Model) -> None:
    assert_refused(
        lambda: documents.parse(model, {"doctype": "sprocket", "id": "SPR-1"}),
        "unknown-kind",
        needle="sprocket",
    )


def test_a_document_with_no_doctype_is_refused(model: Model) -> None:
    assert_refused(
        lambda: documents.parse(model, {"id": "SPR-1"}), "unknown-kind", needle="doctype"
    )


def test_a_mismatched_doctype_is_refused(model: Model) -> None:
    assert_refused(
        lambda: documents.parse(model, valid_rfq(), kind="bom"),
        "invalid-value",
        needle="rfq",
    )


def test_the_masters_are_validated_by_erp_02(model: Model) -> None:
    """An item is ERP-02's family, and its schema refuses a bad one."""
    assert_refused(
        lambda: documents.parse(
            model,
            {"doctype": "item", "id": "RAW-A", "name": "A", "uom": "Kg"},
            kind="item",
        ),
        "missing-field",
        needle="is_stock_item",
    )


def test_a_boolean_is_not_an_integer(model: Model) -> None:
    """``True`` is a Python ``int``: the type check must not accept it as one."""
    assert documents.json_type_name(True) == "boolean"
    assert documents.json_type_name(1) == "integer"
