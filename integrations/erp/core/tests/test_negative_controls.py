"""The negative controls: every refusal is shown to be *caused* by its rule.

A validator that refuses bad input is only half proven — the refusal has to come
from the rule under test, not from something incidental. So each control here
does both halves:

* the accepted document is accepted and the broken one is refused (so the
  control is not always-red);
* the rule is then **neutered in a scratch copy of the tree** and the same
  broken document must become accepted.

If neutering the rule changes nothing, the refusal was caused by something else
and the control is a formality — which is what this file exists to prevent.

The two invariants a schema cannot express (double-entry balance, and a transfer
that actually moves stock) are proven differently, because there is no schema
clause to remove: the case shows the document passing the schema check while the
declared family rule refuses it, so the rule is demonstrably what refuses.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from integrations.erp.core import validators
from integrations.erp.core.errors import ErpError
from integrations.erp.core.schema import Validator

from . import documents
from .conftest import CORE, SCHEMAS


def scratch(tmp_path: Path) -> Path:
    target = tmp_path / "core"
    shutil.copytree(CORE, target, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    return target


def _edit_json(path: Path, edit: Callable[[Dict[str, Any]], None]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    edit(document)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def accepts(model, kind: str, document: Dict[str, Any]) -> bool:
    try:
        model.validate_document(kind, document)
    except ErpError:
        return False
    return True


# --- the refusal is not always-red -----------------------------------------


def test_a_valid_and_an_invalid_document_differ_only_in_the_broken_field(model) -> None:
    good = documents.sales_order()
    broken = documents.sales_order()
    broken.pop("party")
    assert accepts(model, "sales-order", good) is True
    assert accepts(model, "sales-order", broken) is False


def test_the_legal_path_is_accepted_while_the_jump_is_refused(model) -> None:
    order = model.workflow_for("sales-order")
    assert order.assert_move("draft", "submitted") == "submitted"
    with pytest.raises(ErpError):
        order.assert_move("draft", "completed")


# --- neutering a rule makes the previously-refused document accepted --------


def test_neutering_required_accepts_the_document_it_refused(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    broken = documents.sales_order()
    broken.pop("party")
    assert accepts(validators.load_model(root), "sales-order", broken) is False
    _edit_json(
        root / "schemas" / "sales-order.schema.json",
        lambda doc: doc["required"].remove("party"),
    )
    assert accepts(validators.load_model(root), "sales-order", broken) is True


def test_neutering_the_closed_enum_accepts_a_party_type_outside_it(
    tmp_path: Path,
) -> None:
    root = scratch(tmp_path)
    broken = documents.party(party_type="vendor")
    assert accepts(validators.load_model(root), "party", broken) is False
    _edit_json(
        root / "schemas" / "party.schema.json",
        lambda doc: doc["properties"]["party_type"].pop("enum"),
    )
    assert accepts(validators.load_model(root), "party", broken) is True


def test_neutering_additional_properties_accepts_an_unknown_field(
    tmp_path: Path,
) -> None:
    root = scratch(tmp_path)
    broken = documents.party(credit_limit_note="free text")
    assert accepts(validators.load_model(root), "party", broken) is False
    _edit_json(
        root / "schemas" / "party.schema.json",
        lambda doc: doc.__setitem__("additionalProperties", True),
    )
    assert accepts(validators.load_model(root), "party", broken) is True


def test_removing_the_one_of_accepts_a_line_with_both_sides(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    # Balanced overall, so the only rule this posting breaks is the per-line one
    # (exactly one non-zero side): the neutering test below is then unambiguous.
    broken = documents.gl_posting(
        lines=[
            {"account": "DEBTORS", "debit": 100.0, "credit": 100.0},
            {"account": "REVENUE", "credit": 100.0},
            {"account": "CONTRA", "debit": 100.0},
        ]
    )
    assert accepts(validators.load_model(root), "gl-posting", broken) is False
    _edit_json(
        root / "schemas" / "gl-posting.schema.json",
        lambda doc: doc["properties"]["lines"]["items"].pop("oneOf"),
    )
    assert accepts(validators.load_model(root), "gl-posting", broken) is True


def test_removing_the_conditional_accepts_a_receipt_with_a_source_warehouse(
    tmp_path: Path,
) -> None:
    root = scratch(tmp_path)
    broken = documents.stock_entry(from_warehouse="MAIN")
    assert accepts(validators.load_model(root), "stock-entry", broken) is False
    _edit_json(
        root / "schemas" / "stock-entry.schema.json",
        lambda doc: doc.__setitem__("allOf", []),
    )
    assert accepts(validators.load_model(root), "stock-entry", broken) is True


def test_adding_the_transition_accepts_the_move_it_previously_refused(
    tmp_path: Path,
) -> None:
    """The jump refusal is caused by the declared transitions, not by prose."""
    root = scratch(tmp_path)
    workflow = validators.load_model(root).workflow_for("sales-order")
    with pytest.raises(ErpError):
        workflow.assert_move("draft", "completed")
    target = root / "workflows" / "sales-order.yaml"
    target.write_text(
        target.read_text(encoding="utf-8")
        + "  - action: shortcut\n"
        + "    from: draft\n"
        + "    to: completed\n"
        + "    label: Shortcut\n",
        encoding="utf-8",
    )
    assert (
        validators.load_model(root).workflow_for("sales-order").assert_move(
            "draft", "completed"
        )
        == "completed"
    )


def test_neutering_the_state_enum_accepts_a_state_the_workflow_lacks(
    tmp_path: Path,
) -> None:
    root = scratch(tmp_path)
    broken = documents.sales_order(state="finished")
    assert accepts(validators.load_model(root), "sales-order", broken) is False
    _edit_json(
        root / "schemas" / "sales-order.schema.json",
        lambda doc: doc["properties"]["state"].pop("enum"),
    )
    assert accepts(validators.load_model(root), "sales-order", broken) is True


# --- the rules a schema cannot express -------------------------------------


def test_the_balance_refusal_comes_from_the_declared_family_rule(model) -> None:
    """Schema-valid, rule-refused: so the rule is what refused it."""
    posting = documents.gl_posting(
        lines=[
            {"account": "DEBTORS", "debit": 100.0},
            {"account": "REVENUE", "credit": 90.0},
        ]
    )
    schema = model.schema_for("gl-posting")
    assert Validator(base_dir=SCHEMAS).violations(posting, schema) == []
    with pytest.raises(ErpError) as refusal:
        model.validate_document("gl-posting", posting)
    assert refusal.value.code == "unbalanced_posting"


def test_the_transfer_refusal_comes_from_the_declared_family_rule(model) -> None:
    entry = documents.stock_entry(
        purpose="material_transfer", from_warehouse="MAIN", to_warehouse="MAIN"
    )
    schema = model.schema_for("stock-entry")
    assert Validator(base_dir=SCHEMAS).violations(entry, schema) == []
    with pytest.raises(ErpError) as refusal:
        model.validate_document("stock-entry", entry)
    assert refusal.value.code == "invalid_transfer"


def test_removing_the_family_rule_accepts_the_document_it_refused(
    model, monkeypatch
) -> None:
    """Neuter the registry entry, so the rule is shown to be the cause.

    ``FAMILY_RULES`` is a mapping of the live module, so the mutation happens in
    process: a scratch copy of ``validators.py`` would prove nothing, because
    ``load_model`` reads assets from a root but takes its rules from the module
    it was imported as.
    """
    broken = documents.gl_posting(
        lines=[
            {"account": "DEBTORS", "debit": 100.0},
            {"account": "REVENUE", "credit": 90.0},
        ]
    )
    assert accepts(model, "gl-posting", broken) is False
    monkeypatch.delitem(validators.FAMILY_RULES, "gl-posting")
    assert model.rules_for("gl-posting") == ()
    assert accepts(model, "gl-posting", broken) is True


# --- a refusal that cannot be provoked would be a formality -----------------


def test_every_mutant_is_refused_while_its_valid_twin_is_accepted(model) -> None:
    """One pass over the whole corpus, asserting both halves for each case."""
    refused: List[str] = []
    for case in documents.mutants():
        assert accepts(model, case["kind"], documents.valid_for(case["kind"])) is True
        assert accepts(model, case["kind"], documents.mutated(case)) is False, case["why"]
        refused.append(case["why"])
    assert len(refused) == len(documents.mutants())
    assert len(set(refused)) == len(refused), "two mutants share a description"
