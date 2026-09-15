"""Unit controls for the stock ledger and the general ledger."""

from __future__ import annotations

import pytest

from integrations.erp.tx import ledger as gl
from integrations.erp.tx import model, stock

AT = "2026-03-05T08:00:00Z"
ITEM = {"is_stock_item": True, "valuation_rate": 12.0}


def _delivery(**overrides: object) -> dict:
    body = {
        "doctype": "delivery-note",
        "id": "DN-1",
        "warehouse": "WH-1",
        "lines": [{"item_code": "ITEM-1", "qty": 5, "rate": 20.0}],
    }
    body.update(overrides)
    return body


# --- stock ------------------------------------------------------------------


def test_a_movement_moves_the_quantity_it_is_given() -> None:
    movements = stock.movements_for(
        _delivery(),
        kind="delivery-note",
        reference="DN-1",
        items={"ITEM-1": ITEM},
        warehouses=("WH-1",),
        at=AT,
        sign=-1,
    )
    assert len(movements) == 1
    assert movements[0].qty == -5.0
    assert movements[0].rate == 12.0, (
        "stock leaves at its cost basis, so the delivery line's selling rate is not "
        "the rate the stock ledger is written at"
    )
    assert movements[0].amount == -60.0


def test_a_movement_without_a_rate_uses_the_item_valuation() -> None:
    body = _delivery(lines=[{"item_code": "ITEM-1", "qty": 2}])
    movements = stock.movements_for(
        body,
        kind="delivery-note",
        reference="DN-1",
        items={"ITEM-1": ITEM},
        warehouses=("WH-1",),
        at=AT,
        sign=-1,
    )
    assert movements[0].rate == 12.0
    assert movements[0].amount == -24.0


def test_a_line_may_override_the_documents_warehouse() -> None:
    body = _delivery(lines=[{"item_code": "ITEM-1", "qty": 1, "warehouse": "WH-2"}])
    movements = stock.movements_for(
        body,
        kind="delivery-note",
        reference="DN-1",
        items={"ITEM-1": ITEM},
        warehouses=("WH-1", "WH-2"),
        at=AT,
        sign=-1,
    )
    assert movements[0].warehouse == "WH-2"


@pytest.mark.parametrize(
    "override,reason,needle",
    [
        ({"items": {}}, "unknown-item", "ITEM-1"),
        ({"items": {"ITEM-1": {"is_stock_item": False}}}, "not-a-stock-item", "ITEM-1"),
        ({"warehouses": ()}, "unknown-warehouse", "WH-1"),
    ],
)
def test_a_movement_names_the_offender_it_refuses(
    override: dict, reason: str, needle: str
) -> None:
    call = {"items": {"ITEM-1": ITEM}, "warehouses": ("WH-1",)}
    call.update(override)
    with pytest.raises(model.Refused) as caught:
        stock.movements_for(
            _delivery(),
            kind="delivery-note",
            reference="DN-1",
            at=AT,
            sign=-1,
            **call,
        )
    assert caught.value.reason == reason
    assert needle in caught.value.detail


def test_a_line_naming_no_warehouse_at_all_is_refused() -> None:
    body = _delivery(warehouse=None)
    body.pop("warehouse")
    with pytest.raises(model.Refused) as caught:
        stock.movements_for(
            body,
            kind="delivery-note",
            reference="DN-1",
            items={"ITEM-1": ITEM},
            warehouses=("WH-1",),
            at=AT,
            sign=-1,
        )
    assert caught.value.reason == "missing-field"


def test_a_document_without_lines_moves_nothing() -> None:
    with pytest.raises(model.Refused) as caught:
        stock.movements_for(
            _delivery(lines=[]),
            kind="delivery-note",
            reference="DN-1",
            items={"ITEM-1": ITEM},
            warehouses=("WH-1",),
            at=AT,
        )
    assert caught.value.reason == "missing-field"


def test_inversion_is_exact_and_flips_the_flag() -> None:
    movements = stock.movements_for(
        _delivery(),
        kind="delivery-note",
        reference="DN-1",
        items={"ITEM-1": ITEM},
        warehouses=("WH-1",),
        at=AT,
        sign=-1,
    )
    ledger = stock.StockLedger().apply(movements)
    assert ledger.balance("ITEM-1", "WH-1") == -5.0
    ledger = ledger.apply(stock.invert(movements, at=AT))
    assert ledger.balance("ITEM-1", "WH-1") == 0.0
    assert ledger.value("ITEM-1", "WH-1") == 0.0
    assert stock.reversal_is_exact(ledger, "DN-1")
    assert [movement.reversal for movement in ledger].count(True) == 1


def test_a_ledger_reports_its_own_inconsistencies() -> None:
    zero = stock.Movement(
        document="DN-1", kind="delivery-note", item_code="ITEM-1", warehouse="WH-1",
        qty=0.0, rate=1.0, at=AT,
    )
    negative = stock.Movement(
        document="DN-1", kind="delivery-note", item_code="ITEM-1", warehouse="WH-1",
        qty=-1.0, rate=-1.0, at=AT,
    )
    findings = stock.StockLedger(movements=(zero, negative)).verify()
    assert {finding.code for finding in findings} == {"invalid-value"}
    assert len(findings) == 2


def test_an_empty_ledger_is_consistent() -> None:
    assert stock.StockLedger().verify() == []


# --- the general ledger -----------------------------------------------------


def _invoice(**overrides: object) -> model.TxDocument:
    body = {
        "doctype": "sales-invoice",
        "id": "INV-1",
        "company": "CO",
        "currency": "USD",
        "state": "submitted",
        "docstatus": 1,
        "posting_date": "2026-03-06",
        "lines": [{"item_code": "ITEM-1", "qty": 5, "rate": 20.0}],
        "taxes": [{"account": "TAX-PAYABLE", "amount": 5.0}],
        "net_total": 100.0,
        "total": 105.0,
    }
    body.update(overrides)
    return model.TxDocument.of("sales-invoice", body["id"], body)


def test_the_posting_policy_must_declare_every_role_it_is_asked_for() -> None:
    policy = gl.PostingPolicy(accounts={"receivable": "AR"})
    assert policy.resolve("receivable") == "AR"
    assert policy.missing() == ("income",)
    with pytest.raises(model.Refused) as caught:
        policy.resolve("income")
    assert caught.value.reason == "unknown-account-role"
    assert "income" in caught.value.detail


def test_the_invoice_posting_is_balanced_double_entry() -> None:
    entries = gl.posting_entries(
        _invoice(), gl.PostingPolicy(accounts={"receivable": "AR", "income": "REV"}), at=AT
    )
    assert [(entry.account, entry.debit, entry.credit) for entry in entries] == [
        ("AR", 105.0, 0.0),
        ("REV", 0.0, 100.0),
        ("TAX-PAYABLE", 0.0, 5.0),
    ]
    general = gl.GeneralLedger().apply(entries)
    assert general.totals() == (105.0, 105.0)
    assert general.verify() == []


def test_a_tax_account_may_not_be_guessed() -> None:
    with pytest.raises(model.Refused) as caught:
        gl.line_totals(_invoice(taxes=[{"amount": 5.0}]))
    assert caught.value.reason == "missing-field"
    assert "tax account" in caught.value.detail


def test_a_total_that_disagrees_with_the_lines_is_refused() -> None:
    with pytest.raises(model.Refused) as caught:
        gl.line_totals(_invoice(total=999.0))
    assert caught.value.reason == "unbalanced-posting"
    assert "999.00" in caught.value.detail


def test_a_zero_tax_row_is_omitted_rather_than_posted_as_zero() -> None:
    entries = gl.posting_entries(
        _invoice(taxes=[{"account": "TAX-PAYABLE", "amount": 0.0}], total=100.0),
        gl.PostingPolicy(accounts={"receivable": "AR", "income": "REV"}),
        at=AT,
    )
    lines = gl.posting_lines(entries)
    assert lines == [{"account": "AR", "debit": 100.0}, {"account": "REV", "credit": 100.0}]
    for line in lines:
        assert ("debit" in line) != ("credit" in line)


def test_a_posting_line_that_carries_neither_side_is_refused() -> None:
    with pytest.raises(model.Refused) as caught:
        gl.posting_lines(
            [gl.Entry(voucher_type="sales-invoice", voucher_id="INV-1", account="AR")]
        )
    assert caught.value.reason == "unbalanced-posting"


def test_a_reversal_swaps_the_sides_and_nets_the_voucher_to_zero() -> None:
    entries = gl.posting_entries(
        _invoice(), gl.PostingPolicy(accounts={"receivable": "AR", "income": "REV"}), at=AT
    )
    general = gl.GeneralLedger().apply(entries)
    assert general.net_for_document("INV-1") == 0.0, (
        "a voucher's own debits and credits already net to zero"
    )
    general = general.apply(gl.invert(entries, at=AT))
    assert general.balances() == {"AR": 0.0, "REV": 0.0, "TAX-PAYABLE": 0.0}
    assert [entry.reversal for entry in general].count(True) == 3


def test_a_row_with_two_sides_is_reported() -> None:
    row = gl.Entry(
        voucher_type="sales-invoice", voucher_id="INV-1", account="AR", debit=1.0, credit=1.0
    )
    findings = gl.GeneralLedger(entries=(row,)).verify()
    assert [finding.code for finding in findings] == ["unbalanced-posting"]
    assert "both a debit and a credit" in findings[0].detail
    assert "INV-1" in findings[0].detail
