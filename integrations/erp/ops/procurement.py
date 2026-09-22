"""The purchase cycle: RFQ to order to receipt to invoice.

Acceptance criterion 1 of issue #649 is that this sequence "posts stock and GL
effects correctly", so the sequence is a function rather than a diagram:

1. :func:`raise_rfq` — an invitation to quote, sent to one or more suppliers.
2. :func:`convert_rfq` — the invitation becomes a purchase order, whose lines
   carry the supplier's quoted rates. The RFQ closes on the same call, which is
   what makes a second conversion ``already-converted`` rather than a second
   order.
3. :func:`receive_order` — goods arrive against the order. The receipt commits
   stock (an ERP-02 ``stock-entry`` with purpose ``material_receipt`` validated
   by ERP-02's own schema) and posts the ledger row that moves the value from
   Stock Received Not Billed into Stock In Hand.
4. :func:`invoice_order` — the supplier's bill, raised against the receipt it
   settles, which posts the payable: Stock Received Not Billed and Input Tax
   out, Accounts Payable in.

Two refusals carry the cycle's own invariants, and both are provoked in
``negative_control.py``:

* **a receipt without an order is refused.** Material arriving with no
  commitment behind it is a stock adjustment, which this model expresses as a
  stock entry; a receipt that names no order — or an order that is not
  submitted — is refused by name before any stock moves.
* **an order cannot be over-received.** What has already been received against
  the order is *derived* from the receipts themselves rather than tracked in a
  counter, so there is no second copy of the fact to drift.

---knowledge---
module_id: integrations.erp.ops.procurement
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [received, raise_rfq, convert_rfq, receive_order, invoice_order, purchase_cycle]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .model import (
    ACTION_CONVERT,
    ACTION_INVOICE,
    ACTION_RECEIVE,
    KIND_PURCHASE_INVOICE,
    KIND_PURCHASE_ORDER,
    KIND_PURCHASE_RECEIPT,
    KIND_RFQ,
    Refused,
)
from .workspace import Workspace

__all__ = [
    "AP_CLERK",
    "BUYER",
    "STOREKEEPER",
    "convert_rfq",
    "invoice_order",
    "purchase_cycle",
    "raise_rfq",
    "receive_order",
    "received",
]

#: The actors the rail attributes each step to, so the audit reads as a process
#: rather than as one anonymous writer.
BUYER = "buyer"
STOREKEEPER = "storekeeper"
AP_CLERK = "ap-clerk"

#: The account the posted tax lands in. Named here AND in the catalogue's
#: posting rule, so a document and its ledger row cite one account.
TAX_ACCOUNT = "INPUT-TAX"


def _date(at: str) -> str:
    """The calendar date of an instant — the transaction date documents carry."""
    return at[:10]


def _purchase_order(space: Workspace, order_id: Any, *, what: str) -> Mapping[str, Any]:
    """The submitted purchase order a buying document cites, or a refusal.

    Three failures, three messages, one vocabulary: an order that does not
    exist and one that exists but is not a purchase order are both
    ``receipt-without-order`` (there is no commitment behind the document), and
    an order that is still a draft is ``purchase-order-not-submitted`` — a
    different mistake with a different fix.

    "Submitted" is read as ``docstatus`` 1, not as the state name, because an
    order that has been received against in full is *completed* and still
    submitted: its ``complete`` action is Mark Received, and both states carry
    docstatus 1. Reading the state name instead would refuse to bill an order
    that had just been fully delivered.
    """
    order = space.find(order_id)
    if order is None:
        raise Refused(
            "receipt-without-order",
            f"{what}: no purchase order {order_id!r} exists",
        )
    if order.get("doctype") != KIND_PURCHASE_ORDER:
        raise Refused(
            "receipt-without-order",
            f"{what}: {order_id!r} is a {order.get('doctype')!r}, not a purchase order",
        )
    if order.get("docstatus") != 1:
        raise Refused(
            "purchase-order-not-submitted",
            f"{what}: purchase order {order_id!r} is in state "
            f"{order.get('state')!r} with docstatus {order.get('docstatus')!r}, not "
            "submitted — goods are received against a commitment, not a draft",
        )
    return order


def received(space: Workspace, order_id: str) -> Dict[str, float]:
    """What has arrived against ``order_id``, derived from its receipts.

    Derived rather than tracked: a counter would be a second copy of a fact the
    receipts already carry, and the two would drift the first time a receipt was
    cancelled.
    """
    totals: Dict[str, float] = {}
    for receipt in space.of_kind(KIND_PURCHASE_RECEIPT):
        if receipt.get("against_purchase_order") != order_id:
            continue
        if receipt.get("state") == "cancelled":
            continue
        for line in receipt.get("lines", []):
            code = line["item_code"]
            totals[code] = round(totals.get(code, 0.0) + float(line["qty"]), 6)
    return totals


def raise_rfq(
    space: Workspace,
    *,
    rfq_id: str,
    suppliers: Sequence[str],
    lines: Sequence[Mapping[str, Any]],
    at: str,
    actor: str = BUYER,
    valid_till: Optional[str] = None,
) -> Dict[str, Any]:
    """Create and send a request for quotation."""
    if not suppliers:
        raise Refused(
            "invalid-value", f"rfq {rfq_id}: an invitation needs at least one supplier"
        )
    for supplier in suppliers:
        space.party(supplier, expect="supplier")
    for line in lines:
        space.item(line.get("item_code"))
    document: Dict[str, Any] = {
        "doctype": KIND_RFQ,
        "id": rfq_id,
        "state": "draft",
        "docstatus": 0,
        "company": space.company,
        "currency": space.catalog.currency,
        "transaction_date": _date(at),
        "suppliers": list(suppliers),
        "lines": [dict(line) for line in lines],
    }
    if valid_till is not None:
        document["valid_till"] = valid_till
    created = space.create(document, at=at, actor=actor, where=rfq_id)
    return space.advance(created, "submit", at=at, actor=actor)


def convert_rfq(
    space: Workspace,
    *,
    rfq_id: str,
    order_id: str,
    at: str,
    actor: str = BUYER,
    expected_delivery: Optional[str] = None,
) -> Dict[str, Any]:
    """Raise a purchase order from a sent RFQ, and close the RFQ.

    An unpriced line is refused (``missing-field``) rather than defaulted to
    zero: a commitment made at a price nobody quoted is not a commitment.
    """
    rfq = space.find(rfq_id)
    if rfq is None or rfq.get("doctype") != KIND_RFQ:
        raise Refused(
            "unknown-document",
            f"{rfq_id!r} is not a request for quotation; known: "
            f"{[document['id'] for document in space.of_kind(KIND_RFQ)]}",
        )
    if rfq.get("state") == "completed":
        raise Refused(
            "already-converted",
            f"rfq {rfq_id} is already closed; an invitation becomes one order",
        )
    if rfq.get("state") != "submitted":
        raise Refused(
            "rfq-not-submitted",
            f"rfq {rfq_id} is {rfq.get('state')!r}; only a sent invitation converts",
        )
    unpriced = [
        position
        for position, line in enumerate(rfq.get("lines", []))
        if line.get("rate") is None
    ]
    if unpriced:
        raise Refused(
            "missing-field",
            f"rfq {rfq_id}: lines {unpriced} carry no rate, and an order cannot be "
            "raised from an unpriced line",
        )
    for line in rfq["lines"]:
        space.item(line["item_code"])
    supplier = rfq["suppliers"][0]
    space.party(supplier, expect="supplier")
    lines = [
        {
            "item_code": line["item_code"],
            "qty": line["qty"],
            "rate": line["rate"],
        }
        for line in rfq["lines"]
    ]
    net = round(sum(float(line["qty"]) * float(line["rate"]) for line in lines), 2)
    document: Dict[str, Any] = {
        "doctype": KIND_PURCHASE_ORDER,
        "id": order_id,
        "state": "draft",
        "docstatus": 0,
        "company": space.company,
        "currency": rfq["currency"],
        "transaction_date": _date(at),
        "supplier": supplier,
        "supplier_quotation": rfq_id,
        "lines": lines,
        "net_total": net,
        "total": net,
    }
    if expected_delivery is not None:
        document["expected_delivery"] = expected_delivery
    created = space.create(document, at=at, actor=actor, where=order_id)
    submitted = space.advance(created, "submit", at=at, actor=actor)
    space.advance(space.get(rfq_id), "complete", at=at, actor=actor)
    space.rail = space.rail.append(
        at=at,
        actor=actor,
        action=ACTION_CONVERT,
        document=rfq_id,
        detail=(
            f"rfq {rfq_id} -> purchase-order {order_id} "
            f"({len(lines)} line(s), {net:.2f} {rfq['currency']})"
        ),
    )
    return submitted


def receive_order(
    space: Workspace,
    *,
    receipt_id: str,
    order_id: str,
    lines: Sequence[Mapping[str, Any]],
    warehouse: str,
    at: str,
    actor: str = STOREKEEPER,
) -> Dict[str, Any]:
    """Receive goods against an order: commit stock and post the ledger row."""
    order = _purchase_order(space, order_id, what=f"receipt {receipt_id}")
    for line in lines:
        space.item(line.get("item_code"))
    ordered = {
        line["item_code"]: float(line["qty"]) for line in order.get("lines", [])
    }
    running = dict(received(space, order_id))
    receipt_lines: List[Dict[str, Any]] = []
    for line in lines:
        code = line.get("item_code")
        if code not in ordered:
            raise Refused(
                "invalid-value",
                f"receipt {receipt_id}: purchase order {order_id} orders no {code!r}",
            )
        qty = float(line["qty"])
        standing = round(running.get(code, 0.0) + qty, 6)
        if standing > ordered[code]:
            raise Refused(
                "over-receipt",
                f"receipt {receipt_id}: {code} would stand at {standing:g} against an "
                f"order of {ordered[code]:g}",
            )
        running[code] = standing
        rate = next(
            float(entry["rate"])
            for entry in order["lines"]
            if entry["item_code"] == code
        )
        receipt_lines.append({"item_code": code, "qty": qty, "rate": rate})
    total = round(
        sum(float(line["qty"]) * float(line["rate"]) for line in receipt_lines), 2
    )
    document: Dict[str, Any] = {
        "doctype": KIND_PURCHASE_RECEIPT,
        "id": receipt_id,
        "state": "draft",
        "docstatus": 0,
        "company": space.company,
        "currency": order["currency"],
        "transaction_date": _date(at),
        "supplier": order["supplier"],
        "against_purchase_order": order_id,
        "warehouse": warehouse,
        "lines": receipt_lines,
        "total": total,
    }
    created = space.create(document, at=at, actor=actor, where=receipt_id)
    submitted = space.advance(created, "submit", at=at, actor=actor)
    space.post(
        "purchase-receipt",
        submitted,
        at=at,
        actor=actor,
        action=ACTION_RECEIVE,
        lines=[
            {
                "item_code": line["item_code"],
                "qty": line["qty"],
                "warehouse": warehouse,
                "valuation_rate": line["rate"],
            }
            for line in receipt_lines
        ],
        where=f"receipt {receipt_id}",
    )
    completed = space.advance(submitted, "complete", at=at, actor=actor)

    # The order reaches its terminal state when what it committed is delivered
    # in full — that is what its own `complete` action means (Mark Received).
    # Leaving it in-flight would leave the cycle holding a document no action
    # will ever close.
    standing = received(space, order_id)
    current = space.get(order_id)
    if current.get("state") == "submitted" and all(
        standing.get(code, 0.0) >= qty for code, qty in ordered.items()
    ):
        space.advance(current, "complete", at=at, actor=actor)
    return completed


def invoice_order(
    space: Workspace,
    *,
    invoice_id: str,
    order_id: str,
    receipt_id: str,
    at: str,
    actor: str = AP_CLERK,
    tax_rate: float = 0.0,
) -> Dict[str, Any]:
    """Raise the supplier's bill against a completed receipt, and post the payable."""
    order = _purchase_order(space, order_id, what=f"invoice {invoice_id}")
    receipt = space.get(receipt_id)
    if (
        receipt.get("doctype") != KIND_PURCHASE_RECEIPT
        or receipt.get("against_purchase_order") != order_id
    ):
        raise Refused(
            "receipt-without-order",
            f"invoice {invoice_id}: {receipt_id!r} is not a purchase receipt against "
            f"{order_id!r}, so there is nothing to bill",
        )
    if receipt.get("state") != "completed":
        raise Refused(
            "invalid-value",
            f"invoice {invoice_id}: receipt {receipt_id} is {receipt.get('state')!r}, "
            "not completed; goods are billed once they are booked in",
        )
    lines = [
        {"item_code": line["item_code"], "qty": line["qty"], "rate": line["rate"]}
        for line in receipt["lines"]
    ]
    net = round(sum(float(line["qty"]) * float(line["rate"]) for line in lines), 2)
    taxes: List[Dict[str, Any]] = []
    if tax_rate:
        taxes.append(
            {
                "account": TAX_ACCOUNT,
                "rate": round(float(tax_rate) * 100, 4),
                "amount": round(net * float(tax_rate), 2),
                "description": f"Input tax at {float(tax_rate) * 100:g}%",
            }
        )
    total = round(net + sum(float(tax["amount"]) for tax in taxes), 2)
    document: Dict[str, Any] = {
        "doctype": KIND_PURCHASE_INVOICE,
        "id": invoice_id,
        "state": "draft",
        "docstatus": 0,
        "company": space.company,
        "currency": order["currency"],
        "transaction_date": _date(at),
        "posting_date": _date(at),
        "supplier": order["supplier"],
        "against_purchase_order": order_id,
        "against_purchase_receipt": receipt_id,
        "lines": lines,
        "net_total": net,
        "total": total,
    }
    if taxes:
        document["taxes"] = taxes
    created = space.create(document, at=at, actor=actor, where=invoice_id)
    submitted = space.advance(created, "submit", at=at, actor=actor)
    space.post(
        "purchase-invoice",
        submitted,
        at=at,
        actor=actor,
        action=ACTION_INVOICE,
        currency=order["currency"],
        where=f"invoice {invoice_id}",
    )
    return space.advance(submitted, "complete", at=at, actor=actor)


def purchase_cycle(
    space: Workspace,
    *,
    ids: Mapping[str, str],
    supplier: str,
    lines: Sequence[Mapping[str, Any]],
    warehouse: str,
    at: Mapping[str, str],
    tax_rate: float = 0.0,
) -> Dict[str, Dict[str, Any]]:
    """Drive RFQ -> order -> receipt -> invoice and return each document.

    ``lines`` are the RFQ's lines, each carrying the supplier's quoted rate;
    the order, the receipt and the bill inherit them, so a price enters the
    cycle exactly once. The documents returned are re-read from the store after
    the whole cycle, so a caller sees each one's FINAL state rather than the
    snapshot the step that produced it happened to hold.
    """
    raise_rfq(
        space,
        rfq_id=ids["rfq"],
        suppliers=[supplier],
        lines=lines,
        at=at["rfq"],
    )
    convert_rfq(
        space, rfq_id=ids["rfq"], order_id=ids["order"], at=at["order"]
    )
    receive_order(
        space,
        receipt_id=ids["receipt"],
        order_id=ids["order"],
        lines=lines,
        warehouse=warehouse,
        at=at["receipt"],
    )
    invoice_order(
        space,
        invoice_id=ids["invoice"],
        order_id=ids["order"],
        receipt_id=ids["receipt"],
        at=at["invoice"],
        tax_rate=tax_rate,
    )
    return {
        "rfq": space.get(ids["rfq"]),
        "purchase_order": space.get(ids["order"]),
        "purchase_receipt": space.get(ids["receipt"]),
        "purchase_invoice": space.get(ids["invoice"]),
    }
