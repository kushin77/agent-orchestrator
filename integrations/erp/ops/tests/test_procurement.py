"""Acceptance criterion 1: RFQ to order to receipt to invoice, posting stock and GL.

Each step is asserted on the artefacts it is supposed to produce — the stock
ledger, the posted ledger rows and the documents' own states — rather than on
the flow having returned without raising.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

import pytest

from integrations.erp.ops import flows, procurement
from integrations.erp.ops.catalog import Catalog
from integrations.erp.ops.model import Model, Refused
from integrations.erp.ops.workspace import Workspace
from .helpers import assert_refused

IDS: Mapping[str, str] = {
    "rfq": "RFQ-0001",
    "order": "PO-0001",
    "receipt": "PR-0001",
    "invoice": "PI-0001",
}
AT: Mapping[str, str] = {
    "rfq": flows.T["rfq"],
    "order": flows.T["order"],
    "receipt": flows.T["receipt"],
    "invoice": flows.T["invoice"],
}
LINES = (
    {"item_code": "RAW-A", "qty": 40, "rate": 5.0},
    {"item_code": "RAW-B", "qty": 60, "rate": 2.0},
)


def run_cycle(space: Workspace, *, tax_rate: float = 0.10) -> Dict[str, Dict[str, Any]]:
    return procurement.purchase_cycle(
        space,
        ids=IDS,
        supplier="SUP-1",
        lines=LINES,
        warehouse=flows.RAW_WAREHOUSE,
        at=AT,
        tax_rate=tax_rate,
    )


@pytest.fixture()
def cycled(space: Workspace):
    return space, run_cycle(space)


def test_the_cycle_reaches_every_state_it_declares(cycled) -> None:
    _, documents_ = cycled
    assert documents_["rfq"]["state"] == "completed"
    assert documents_["purchase_order"]["state"] == "completed"
    assert documents_["purchase_receipt"]["state"] == "completed"
    assert documents_["purchase_invoice"]["state"] == "completed"


def test_the_receipt_commits_stock_into_the_receiving_warehouse(cycled) -> None:
    space, _ = cycled
    assert space.stock_qty(flows.RAW_WAREHOUSE, "RAW-A") == 40.0
    assert space.stock_qty(flows.RAW_WAREHOUSE, "RAW-B") == 60.0
    assert space.stock_qty(flows.FINISHED_WAREHOUSE, "RAW-A") == 0.0


def test_the_receipt_posts_one_stock_entry(cycled) -> None:
    space, _ = cycled
    entries = space.of_kind("stock-entry")
    assert len(entries) == 1
    assert entries[0]["purpose"] == "material_receipt"
    assert entries[0]["to_warehouse"] == flows.RAW_WAREHOUSE
    assert "from_warehouse" not in entries[0]


def test_the_stock_and_ledger_effects_balance(cycled) -> None:
    space, _ = cycled
    assert space.gl_imbalance() == 0.0
    balance = space.gl_balance()
    assert balance == {
        "ACCOUNTS-PAYABLE": -352.0,
        "INPUT-TAX": 32.0,
        "STOCK-IN-HAND": 320.0,
    }


def test_the_invoice_bills_the_receipt_it_cites(cycled) -> None:
    space, documents_ = cycled
    invoice = documents_["purchase_invoice"]
    assert invoice["against_purchase_order"] == IDS["order"]
    assert invoice["against_purchase_receipt"] == IDS["receipt"]
    assert invoice["net_total"] == 320.0
    assert invoice["total"] == 352.0
    assert space.of_kind("gl-posting")[1]["voucher_type"] == "purchase-invoice"


def test_two_runs_post_the_same_ledger(model: Model, catalog: Catalog) -> None:
    """The same inputs post the same ledger, or the cycle is not reproducible."""
    balances = []
    for _ in range(2):
        workspace = flows.workspace(model, catalog)
        run_cycle(workspace)
        balances.append(workspace.gl_balance())
    assert balances[0] == balances[1]


def test_a_receipt_without_an_order_is_refused(space: Workspace) -> None:
    assert_refused(
        lambda: procurement.receive_order(
            space,
            receipt_id="PR-X",
            order_id="PO-9999",
            lines=[{"item_code": "RAW-A", "qty": 1}],
            warehouse=flows.RAW_WAREHOUSE,
            at=AT["receipt"],
        ),
        "receipt-without-order",
        needle="PO-9999",
    )


def test_a_receipt_against_a_draft_order_is_refused(space: Workspace) -> None:
    space.create(
        {
            "doctype": "purchase-order",
            "id": "PO-DRAFT",
            "state": "draft",
            "docstatus": 0,
            "company": "COMPANY-1",
            "currency": "EUR",
            "transaction_date": "2026-09-15",
            "supplier": "SUP-1",
            "lines": [{"item_code": "RAW-A", "qty": 5, "rate": 5.0}],
        },
        at=AT["order"],
        actor="test",
    )
    assert_refused(
        lambda: procurement.receive_order(
            space,
            receipt_id="PR-X",
            order_id="PO-DRAFT",
            lines=[{"item_code": "RAW-A", "qty": 1}],
            warehouse=flows.RAW_WAREHOUSE,
            at=AT["receipt"],
        ),
        "purchase-order-not-submitted",
        needle="PO-DRAFT",
    )


def test_an_order_cannot_be_over_received(cycled) -> None:
    space, _ = cycled
    assert_refused(
        lambda: procurement.receive_order(
            space,
            receipt_id="PR-X",
            order_id=IDS["order"],
            lines=[{"item_code": "RAW-A", "qty": 1}],
            warehouse=flows.RAW_WAREHOUSE,
            at=AT["receipt"],
        ),
        "over-receipt",
        needle="RAW-A",
    )


def test_an_rfq_is_converted_once(cycled) -> None:
    space, _ = cycled
    assert_refused(
        lambda: procurement.convert_rfq(
            space, rfq_id=IDS["rfq"], order_id="PO-0002", at=AT["order"]
        ),
        "already-converted",
        needle=IDS["rfq"],
    )


def test_an_unsent_rfq_does_not_convert(space: Workspace) -> None:
    space.create(
        {
            "doctype": "rfq",
            "id": "RFQ-DRAFT",
            "state": "draft",
            "docstatus": 0,
            "company": "COMPANY-1",
            "currency": "EUR",
            "transaction_date": "2026-09-15",
            "suppliers": ["SUP-1"],
            "lines": [{"item_code": "RAW-A", "qty": 1, "rate": 5.0}],
        },
        at=AT["rfq"],
        actor="test",
    )
    assert_refused(
        lambda: procurement.convert_rfq(
            space, rfq_id="RFQ-DRAFT", order_id="PO-0002", at=AT["order"]
        ),
        "rfq-not-submitted",
        needle="RFQ-DRAFT",
    )


def test_an_unpriced_line_does_not_become_an_order(space: Workspace) -> None:
    procurement.raise_rfq(
        space,
        rfq_id="RFQ-0001",
        suppliers=["SUP-1"],
        lines=[{"item_code": "RAW-A", "qty": 4}],
        at=AT["rfq"],
    )
    assert_refused(
        lambda: procurement.convert_rfq(
            space, rfq_id="RFQ-0001", order_id="PO-0001", at=AT["order"]
        ),
        "missing-field",
        needle="carry no rate",
    )


def test_a_customer_cannot_be_used_as_a_supplier(space: Workspace) -> None:
    assert_refused(
        lambda: procurement.raise_rfq(
            space,
            rfq_id="RFQ-0001",
            suppliers=["CUST-1"],
            lines=[{"item_code": "RAW-A", "qty": 1, "rate": 1.0}],
            at=AT["rfq"],
        ),
        "unknown-supplier",
        needle="CUST-1",
    )


def test_an_item_outside_the_master_is_refused(space: Workspace) -> None:
    assert_refused(
        lambda: procurement.raise_rfq(
            space,
            rfq_id="RFQ-0001",
            suppliers=["SUP-1"],
            lines=[{"item_code": "RAW-Z", "qty": 1, "rate": 1.0}],
            at=AT["rfq"],
        ),
        "unknown-item",
        needle="RAW-Z",
    )


def test_a_refused_receipt_moves_no_stock(space: Workspace) -> None:
    """A refusal must leave the ledger exactly where it was."""
    before = {warehouse: dict(bucket) for warehouse, bucket in space.stock.items()}
    with pytest.raises(Refused):
        procurement.receive_order(
            space,
            receipt_id="PR-X",
            order_id="PO-9999",
            lines=[{"item_code": "RAW-A", "qty": 1}],
            warehouse=flows.RAW_WAREHOUSE,
            at=AT["receipt"],
        )
    assert space.stock == before
    assert space.of_kind("stock-entry") == ()
