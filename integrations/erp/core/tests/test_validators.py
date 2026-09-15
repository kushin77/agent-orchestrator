"""The validators: what they accept, what they refuse, and in what envelope.

Every family in the corpus is put through the same entry point the platform
calls, and each mutant must be refused with a code from the declared vocabulary.
The family rules (the two invariants a schema cannot express) are covered
separately, including a check that every family which declares a rule is
actually provoked by the corpus — so a rule cannot sit in the registry untested.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from integrations.erp.core import validators
from integrations.erp.core.errors import CODES, ErpError

from . import documents

REQUESTS = "req_0123456789ab"


# --- the corpus -------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(documents.valid_documents()))
def test_a_valid_document_validates(model, kind: str) -> None:
    validated = model.validate_document(kind, documents.valid_for(kind))
    assert validated["doctype"] == kind


@pytest.mark.parametrize(
    "case", documents.mutants(), ids=[case["why"] for case in documents.mutants()]
)
def test_every_mutant_is_refused(model, case: Dict[str, Any]) -> None:
    document = documents.mutated(case)
    with pytest.raises(ErpError) as refusal:
        model.validate_document(case["kind"], document)
    assert refusal.value.code in CODES
    assert refusal.value.message


def test_every_refusal_code_used_is_in_the_declared_vocabulary(model) -> None:
    used = set()
    for case in documents.mutants():
        try:
            model.validate_document(case["kind"], documents.mutated(case))
        except ErpError as refusal:
            used.add(refusal.code)
    assert used <= set(CODES)


def test_the_refusal_vocabulary_is_closed_and_unique() -> None:
    assert len(CODES) == len(set(CODES))
    assert "schema_violation" in CODES
    assert "state_jumped" in CODES


def test_a_valid_document_is_returned_unchanged(model) -> None:
    original = documents.sales_order()
    validated = model.validate_document("sales-order", original)
    assert validated == original
    assert validated is not original


# --- the entry point's own refusals ----------------------------------------


def test_an_unknown_document_kind_is_refused(model) -> None:
    with pytest.raises(ErpError) as refusal:
        model.validate_document("milestone", {"doctype": "milestone"})
    assert refusal.value.code == "unknown_document_kind"
    assert "sales-order" in refusal.value.details["known"]


def test_a_document_that_is_not_a_mapping_is_refused(model) -> None:
    for value in ([], "text", 7, None):
        with pytest.raises(ErpError) as refusal:
            model.validate_document("party", value)
        assert refusal.value.code == "invalid_body"


# --- the family rules -------------------------------------------------------


def test_the_declared_family_rules_are_exactly_the_ones_expected() -> None:
    assert sorted(validators.FAMILY_RULES) == ["gl-posting", "stock-entry"]
    for kind, rules in validators.FAMILY_RULES.items():
        assert rules, kind


def test_a_family_without_a_rule_declares_none(model) -> None:
    assert model.rules_for("sales-order") == ()
    assert model.rules_for("party") == ()


def test_every_declared_family_rule_is_provoked_by_the_corpus(model) -> None:
    """A rule nobody provokes is a rule nobody has shown to bite.

    Each rule refuses with its own code, so the observed codes name the rules
    that were exercised — and the count asserts the two agree, so a third rule
    added without a provoking mutant fails here.
    """
    observed = set()
    for case in documents.mutants():
        try:
            model.validate_document(case["kind"], documents.mutated(case))
        except ErpError as refusal:
            observed.add(refusal.code)
    rule_codes = {"unbalanced_posting", "invalid_transfer"}
    assert rule_codes <= observed
    assert len(validators.FAMILY_RULES) == len(rule_codes)


def test_the_double_entry_rule_refuses_an_unbalanced_posting(model) -> None:
    posting = documents.gl_posting(
        lines=[
            {"account": "DEBTORS", "debit": 100.0},
            {"account": "REVENUE", "credit": 90.0},
        ]
    )
    with pytest.raises(ErpError) as refusal:
        model.validate_document("gl-posting", posting)
    assert refusal.value.code == "unbalanced_posting"
    assert (refusal.value.details["debits"], refusal.value.details["credits"]) == (
        100.0,
        90.0,
    )


def test_the_transfer_rule_refuses_a_move_to_the_same_warehouse(model) -> None:
    entry = documents.stock_entry(
        purpose="material_transfer", from_warehouse="MAIN", to_warehouse="MAIN"
    )
    with pytest.raises(ErpError) as refusal:
        model.validate_document("stock-entry", entry)
    assert refusal.value.code == "invalid_transfer"
    assert refusal.value.details["from_warehouse"] == "MAIN"


def test_a_transfer_between_two_warehouses_is_accepted(model) -> None:
    entry = documents.stock_entry(
        purpose="material_transfer", from_warehouse="MAIN", to_warehouse="OVERFLOW"
    )
    assert model.validate_document("stock-entry", entry)["purpose"] == "material_transfer"


# --- the house envelope -----------------------------------------------------


def test_a_valid_document_returns_the_success_envelope() -> None:
    envelope = validators.envelope_for("party", documents.party(), REQUESTS)
    assert envelope["ok"] is True
    assert envelope["status"] == 200
    assert envelope["requestId"] == REQUESTS
    assert envelope["error"] is None
    assert envelope["data"]["kind"] == "party"
    assert envelope["data"]["document"]["id"] == "CUST-0001"


def test_an_invalid_document_returns_the_error_envelope() -> None:
    broken = documents.party()
    broken.pop("party_type")
    envelope = validators.envelope_for("party", broken, REQUESTS)
    assert envelope["ok"] is False
    assert envelope["status"] == 400
    assert envelope["data"] is None
    assert envelope["requestId"] == REQUESTS
    assert envelope["error"]["code"] == "schema_violation"
    assert envelope["error"]["details"]["problems"]


def test_the_envelope_derives_its_verdict_rather_than_accepting_one() -> None:
    """A caller cannot pass ``ok``: it is computed from the verdict."""
    good = validators.envelope_for("item", documents.item(), REQUESTS)
    bad = validators.envelope_for("item", {"doctype": "item"}, REQUESTS)
    assert (good["ok"], bad["ok"]) == (True, False)


def test_an_unknown_kind_reaches_the_envelope_as_a_refusal() -> None:
    envelope = validators.envelope_for("milestone", {}, REQUESTS)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "unknown_document_kind"


# --- loading ----------------------------------------------------------------


def test_loading_a_model_without_a_workflow_directory_is_refused(tmp_path) -> None:
    (tmp_path / "schemas").mkdir()
    with pytest.raises(ErpError) as refusal:
        validators.load_model(tmp_path)
    assert refusal.value.code == "workflow_invalid"


def test_loading_a_model_without_a_schemas_directory_is_refused(tmp_path) -> None:
    with pytest.raises(ErpError) as refusal:
        validators.load_model(tmp_path)
    assert refusal.value.code == "workflow_invalid"


def test_the_module_level_helpers_agree_with_the_loaded_model(model) -> None:
    assert validators.validate_document("party", documents.party()) == documents.party()


def test_the_module_roots_are_where_the_assets_live() -> None:
    assert validators.MODEL_ROOT.name == "core"
    assert validators.MODEL_ROOT.parent.name == "erp"
    assert validators.SCHEMAS_DIR == validators.MODEL_ROOT / "schemas"
    assert validators.WORKFLOWS_DIR == validators.MODEL_ROOT / "workflows"
