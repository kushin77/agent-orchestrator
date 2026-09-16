"""The posting layer: stock effects by purpose, and ledger rows that balance."""

from __future__ import annotations

from typing import Dict

import pytest

from integrations.erp.ops import catalog as catalog_mod
from integrations.erp.ops import ledger
from integrations.erp.ops.catalog import Catalog, Posting
from integrations.erp.ops.model import Model, Refused
from .helpers import assert_refused

PROBE: Dict[str, float] = {"net_total": 100.0, "taxes": 20.0, "total": 120.0}


def test_every_posting_rule_balances(catalog: Catalog) -> None:
    for rule in catalog.postings:
        assert ledger.balances(rule, PROBE), rule.key


def test_a_rule_that_does_not_balance_is_detected(catalog: Catalog) -> None:
    """The probe is a real check: a lopsided rule must fail it."""
    lopsided = Posting(
        key="lopsided",
        document="purchase-receipt",
        purpose="material_receipt",
        voucher_type="stock-entry",
        gl=(
            {"account": "STOCK-IN-HAND", "side": "debit", "basis": "total"},
            {"account": "STOCK-RECEIVED-NOT-BILLED", "side": "credit", "basis": "net_total"},
        ),
    )
    assert not ledger.balances(lopsided, PROBE)


def test_amounts_are_derived_from_the_lines() -> None:
    document = {
        "id": "X-1",
        "lines": [
            {"item_code": "RAW-A", "qty": 2, "rate": 5.0},
            {"item_code": "RAW-B", "qty": 1, "rate": 2.0},
        ],
        "taxes": [{"account": "INPUT-TAX", "amount": 2.4}],
    }
    assert ledger.amounts(document) == {"net_total": 12.0, "taxes": 2.4, "total": 14.4}


def test_a_declared_total_that_disagrees_is_refused() -> None:
    document = {
        "id": "X-1",
        "lines": [{"item_code": "RAW-A", "qty": 2, "rate": 5.0}],
        "total": 99.0,
    }
    assert_refused(
        lambda: ledger.amounts(document), "invalid-value", needle="X-1"
    )


def test_a_stock_line_is_valued_from_the_item_master() -> None:
    items = {"RAW-A": {"valuation_rate": 5.0}}
    assert ledger.line_amount({"item_code": "RAW-A", "qty": 3}, items) == 15.0


def test_an_item_with_no_rate_and_no_master_entry_is_refused() -> None:
    assert_refused(
        lambda: ledger.line_amount({"item_code": "RAW-Z", "qty": 3}, {}),
        "unknown-item",
        needle="RAW-Z",
    )


def test_the_stock_effects_are_what_the_catalogue_declares(catalog: Catalog) -> None:
    assert catalog.stock_effect("material_receipt") == {"to_warehouse": 1}
    assert catalog.stock_effect("material_issue") == {"from_warehouse": -1}
    assert catalog.stock_effect("material_transfer") == {
        "from_warehouse": -1,
        "to_warehouse": 1,
    }
    assert catalog.stock_effect("manufacture") == {"to_warehouse": 1}


def test_a_single_sided_movement_honours_the_line_warehouse(catalog: Catalog) -> None:
    """A component's own warehouse overrides the order's default."""
    entry = {
        "id": "STE-1",
        "purpose": "material_issue",
        "from_warehouse": "WH-RAW",
        "lines": [
            {"item_code": "RAW-A", "qty": 1},
            {"item_code": "SUB-1", "qty": 2, "warehouse": "WH-FG"},
        ],
    }
    moved = ledger.apply_stock(
        catalog,
        {"WH-RAW": {"RAW-A": 3.0}, "WH-FG": {"SUB-1": 5.0}},
        entry,
        items={"RAW-A": {}, "SUB-1": {}},
    )
    assert moved == {"WH-RAW": {"RAW-A": 2.0}, "WH-FG": {"SUB-1": 3.0}}


def test_a_two_sided_movement_refuses_a_line_warehouse(catalog: Catalog) -> None:
    """A transfer's line warehouse would be ambiguous between source and target."""
    entry = {
        "id": "STE-1",
        "purpose": "material_transfer",
        "from_warehouse": "WH-A",
        "to_warehouse": "WH-B",
        "lines": [{"item_code": "RAW-A", "qty": 1, "warehouse": "WH-C"}],
    }
    assert_refused(
        lambda: ledger.apply_stock(
            catalog, {"WH-A": {"RAW-A": 5.0}}, entry, items={"RAW-A": {}}
        ),
        "invalid-value",
        needle="ambiguous",
    )


def test_a_movement_moves_stock_both_ways(catalog: Catalog) -> None:
    entry = {
        "id": "STE-1",
        "purpose": "material_transfer",
        "from_warehouse": "WH-A",
        "to_warehouse": "WH-B",
        "lines": [{"item_code": "RAW-A", "qty": 3}],
    }
    stock = {"WH-A": {"RAW-A": 5.0}}
    moved = ledger.apply_stock(catalog, stock, entry, items={"RAW-A": {"valuation_rate": 5.0}})
    assert moved == {"WH-A": {"RAW-A": 2.0}, "WH-B": {"RAW-A": 3.0}}


def test_a_movement_of_stock_that_is_not_there_is_refused(catalog: Catalog) -> None:
    entry = {
        "id": "STE-1",
        "purpose": "material_issue",
        "from_warehouse": "WH-A",
        "lines": [{"item_code": "RAW-A", "qty": 9}],
    }
    assert_refused(
        lambda: ledger.apply_stock(
            catalog, {"WH-A": {"RAW-A": 1.0}}, entry, items={"RAW-A": {}}
        ),
        "insufficient-stock",
        needle="RAW-A",
    )


def test_a_refused_movement_leaves_the_ledger_alone(catalog: Catalog) -> None:
    entry = {
        "id": "STE-1",
        "purpose": "material_issue",
        "from_warehouse": "WH-A",
        "lines": [{"item_code": "RAW-A", "qty": 9}],
    }
    stock = {"WH-A": {"RAW-A": 1.0}}
    with pytest.raises(Refused):
        ledger.apply_stock(catalog, stock, entry, items={"RAW-A": {}})
    assert stock == {"WH-A": {"RAW-A": 1.0}}


def test_an_item_the_master_does_not_hold_is_refused(catalog: Catalog) -> None:
    entry = {
        "id": "STE-1",
        "purpose": "material_receipt",
        "to_warehouse": "WH-A",
        "lines": [{"item_code": "RAW-Z", "qty": 1}],
    }
    assert_refused(
        lambda: ledger.apply_stock(catalog, {}, entry, items={}),
        "unknown-item",
        needle="RAW-Z",
    )


def test_a_rule_that_derives_only_one_non_zero_side_is_refused(
    model: Model, catalog: Catalog
) -> None:
    """Zero-amount lines are dropped, and a row with no counter-row is refused."""
    assert_refused(
        lambda: ledger.build_gl_posting(
            model,
            catalog,
            "material-issue",
            {"id": "STE-ZERO", "company": "COMPANY-1", "lines": []},
            posting_id="GL-ZERO",
            currency="EUR",
            items={},
        ),
        "unbalanced-posting",
        needle="a double entry needs two",
    )


def test_the_gl_posting_is_erp_02s_own_family(model: Model) -> None:
    """The posting is validated by ERP-02's model, whose own balance rule refuses
    an unbalanced ledger row — this lane does not certify its own arithmetic."""
    unbalanced = {
        "doctype": "gl-posting",
        "id": "GL-BAD",
        "state": "submitted",
        "docstatus": 1,
        "company": "COMPANY-1",
        "currency": "EUR",
        "posting_date": "2026-09-15",
        "voucher_type": "journal-entry",
        "voucher_id": "JV-1",
        "lines": [
            {"account": "STOCK-IN-HAND", "debit": 10.0},
            {"account": "ACCOUNTS-PAYABLE", "credit": 9.0},
        ],
    }
    assert_refused(
        lambda: model.validate("gl-posting", unbalanced),
        "unbalanced-posting",
        needle="debits 10.00 do not equal credits 9.00",
    )


def test_a_balanced_gl_posting_validates(model: Model) -> None:
    balanced = {
        "doctype": "gl-posting",
        "id": "GL-OK",
        "state": "submitted",
        "docstatus": 1,
        "company": "COMPANY-1",
        "currency": "EUR",
        "posting_date": "2026-09-15",
        "voucher_type": "journal-entry",
        "voucher_id": "JV-1",
        "lines": [
            {"account": "STOCK-IN-HAND", "debit": 10.0},
            {"account": "ACCOUNTS-PAYABLE", "credit": 10.0},
        ],
    }
    assert model.validate("gl-posting", balanced)["id"] == "GL-OK"


def test_a_catalogue_with_a_rule_posted_outside_its_vocabulary_is_refused(
    tmp_path,
) -> None:
    """Semantic checks are separate from the schema, and they name the offender."""
    import json
    import shutil
    from integrations.erp.ops.catalog import CATALOG_FILE, CATALOG_ROOT

    root = tmp_path / "catalog"
    shutil.copytree(CATALOG_ROOT, root)
    target = root / CATALOG_FILE
    document = json.loads(target.read_text(encoding="utf-8"))
    document["stock_effects"]["material_teleport"] = {"to_warehouse": 1}
    target.write_text(json.dumps(document), encoding="utf-8")
    assert_refused(
        lambda: catalog_mod.load(root),
        "declarations-invalid",
        needle="material_teleport",
    )
