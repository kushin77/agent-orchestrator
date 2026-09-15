"""The corpus: one valid document per family, plus the mutants that must fail.

A validator tested only on well-formed input is a formality. So every family
here is paired with **mutations that must be refused**, each named by the reason
it is wrong, and the negative-control suite proves the refusal comes from the
schema rule under test rather than from something incidental (it neuters that
one rule in a scratch tree and requires the previously-refused document to be
accepted).

The builders are deliberately small and explicit rather than generated from the
schemas: a fixture derived from the schema under test cannot disagree with it,
and a corpus that cannot disagree proves nothing.
"""

from __future__ import annotations

import copy
import datetime as _dt
from typing import Any, Callable, Dict, List

TODAY = _dt.date(2026, 9, 15).isoformat()


def _line(**over: Any) -> Dict[str, Any]:
    line: Dict[str, Any] = {"item_code": "ITEM-1", "qty": 2, "rate": 10.0}
    line.update(over)
    return line


def _stock_line(**over: Any) -> Dict[str, Any]:
    """A stock-entry line: a movement, not a price. It carries no rate."""
    line: Dict[str, Any] = {"item_code": "ITEM-1", "qty": 2, "warehouse": "MAIN"}
    line.update(over)
    return line


def _header(doctype: str, **over: Any) -> Dict[str, Any]:
    header: Dict[str, Any] = {
        "doctype": doctype,
        "id": f"{doctype.upper()}-0001",
        "state": "draft",
        "docstatus": 0,
        "company": "ACME",
        "currency": "USD",
        "transaction_date": TODAY,
        "lines": [_line()],
    }
    header.update(over)
    return header


def party(**over: Any) -> Dict[str, Any]:
    document = {
        "doctype": "party",
        "id": "CUST-0001",
        "party_type": "customer",
        "name": "Northwind Traders",
        "currency": "USD",
    }
    document.update(over)
    return document


def item(**over: Any) -> Dict[str, Any]:
    document = {
        "doctype": "item",
        "id": "ITEM-1",
        "name": "Widget",
        "uom": "Nos",
        "is_stock_item": True,
        "standard_rate": 10.0,
    }
    document.update(over)
    return document


def quotation(**over: Any) -> Dict[str, Any]:
    return _header("quotation", party="CUST-0001", valid_till=TODAY, **over)


def sales_order(**over: Any) -> Dict[str, Any]:
    return _header("sales-order", party="CUST-0001", **over)


def delivery_note(**over: Any) -> Dict[str, Any]:
    return _header(
        "delivery-note",
        party="CUST-0001",
        against_sales_order="SALES-ORDER-0001",
        warehouse="MAIN",
        **over,
    )


def sales_invoice(**over: Any) -> Dict[str, Any]:
    return _header(
        "sales-invoice", party="CUST-0001", posting_date=TODAY, **over
    )


def purchase_order(**over: Any) -> Dict[str, Any]:
    return _header("purchase-order", supplier="SUPP-0001", **over)


def purchase_receipt(**over: Any) -> Dict[str, Any]:
    return _header(
        "purchase-receipt",
        supplier="SUPP-0001",
        against_purchase_order="PURCHASE-ORDER-0001",
        warehouse="MAIN",
        **over,
    )


def stock_entry(**over: Any) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "doctype": "stock-entry",
        "id": "STOCK-ENTRY-0001",
        "state": "draft",
        "docstatus": 0,
        "company": "ACME",
        "purpose": "material_receipt",
        "to_warehouse": "MAIN",
        "transaction_date": TODAY,
        "lines": [_stock_line()],
    }
    document.update(over)
    return document


def gl_posting(**over: Any) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "doctype": "gl-posting",
        "id": "GL-POSTING-0001",
        "state": "draft",
        "docstatus": 0,
        "company": "ACME",
        "currency": "USD",
        "posting_date": TODAY,
        "voucher_type": "sales-invoice",
        "voucher_id": "SALES-INVOICE-0001",
        "lines": [
            {"account": "DEBTORS", "debit": 100.0},
            {"account": "REVENUE", "credit": 100.0},
        ],
    }
    document.update(over)
    return document


def valid_documents() -> Dict[str, Dict[str, Any]]:
    """One valid document per document family."""
    return {
        "party": party(),
        "item": item(),
        "quotation": quotation(),
        "sales-order": sales_order(),
        "delivery-note": delivery_note(),
        "sales-invoice": sales_invoice(),
        "purchase-order": purchase_order(),
        "purchase-receipt": purchase_receipt(),
        "stock-entry": stock_entry(),
        "gl-posting": gl_posting(),
    }


def _drop(field: str) -> Callable[[Dict[str, Any]], None]:
    def edit(document: Dict[str, Any]) -> None:
        document.pop(field, None)

    return edit


def _set(field: str, value: Any) -> Callable[[Dict[str, Any]], None]:
    def edit(document: Dict[str, Any]) -> None:
        document[field] = value

    return edit


def _nested(index: int, field: str, value: Any) -> Callable[[Dict[str, Any]], None]:
    def edit(document: Dict[str, Any]) -> None:
        document["lines"][index][field] = value

    return edit


def _uneven_balance(document: Dict[str, Any]) -> None:
    document["lines"][0]["debit"] = 100.0
    document["lines"][1]["credit"] = 99.0


def _both_sides(document: Dict[str, Any]) -> None:
    document["lines"][0]["credit"] = 100.0


def _neither_side(document: Dict[str, Any]) -> None:
    document["lines"][0].pop("debit")
    document["lines"][1]["credit"] = 0


def mutants() -> List[Dict[str, Any]]:
    """Documents that must be refused, each naming the rule it violates."""
    cases: List[Dict[str, Any]] = [
        {
            "kind": "party",
            "why": "party_type outside the closed customer/supplier vocabulary",
            "edit": _set("party_type", "vendor"),
        },
        {
            "kind": "party",
            "why": "currency is not a three-letter ISO code",
            "edit": _set("currency", "usd"),
        },
        {
            "kind": "party",
            "why": "an unknown field (additionalProperties is false)",
            "edit": _set("credit_limit_note", "free text"),
        },
        {
            "kind": "item",
            "why": "uom is missing, so a quantity would have no unit",
            "edit": _drop("uom"),
        },
        {
            "kind": "item",
            "why": "is_stock_item is the string 'yes' rather than a boolean",
            "edit": _set("is_stock_item", "yes"),
        },
        {
            "kind": "quotation",
            "why": "an unknown field on a document family",
            "edit": _set("discount_note", "5 percent"),
        },
        {
            "kind": "quotation",
            "why": "valid_till is not an ISO date",
            "edit": _set("valid_till", "15-09-2026"),
        },
        {
            "kind": "sales-order",
            "why": "party is missing",
            "edit": _drop("party"),
        },
        {
            "kind": "sales-order",
            "why": "state is not one of the workflow's states",
            "edit": _set("state", "approved"),
        },
        {
            "kind": "sales-order",
            "why": "docstatus is outside the closed 0/1/2 vocabulary",
            "edit": _set("docstatus", 5),
        },
        {
            "kind": "sales-order",
            "why": "docstatus is the boolean true, which Python would accept as an int",
            "edit": _set("docstatus", True),
        },
        {
            "kind": "sales-order",
            "why": "lines is empty, so nothing is being ordered",
            "edit": _set("lines", []),
        },
        {
            "kind": "sales-order",
            "why": "a line quantity of zero",
            "edit": _nested(0, "qty", 0),
        },
        {
            "kind": "sales-order",
            "why": "a negative line rate",
            "edit": _nested(0, "rate", -1),
        },
        {
            "kind": "sales-order",
            "why": "a line is missing its item_code",
            "edit": lambda document: document["lines"][0].pop("item_code"),
        },
        {
            "kind": "delivery-note",
            "why": "against_sales_order is missing, so the delivery fulfils nothing",
            "edit": _drop("against_sales_order"),
        },
        {
            "kind": "sales-invoice",
            "why": "posting_date is missing, so the invoice has no accounting period",
            "edit": _drop("posting_date"),
        },
        {
            "kind": "purchase-order",
            "why": "supplier is missing on the buying side",
            "edit": _drop("supplier"),
        },
        {
            "kind": "purchase-receipt",
            "why": "against_purchase_order is missing",
            "edit": _drop("against_purchase_order"),
        },
        {
            "kind": "stock-entry",
            "why": "a material_transfer naming no destination warehouse",
            "edit": _set(
                "purpose",
                "material_transfer",
            ),
        },
        {
            "kind": "stock-entry",
            "why": "a material_receipt that also names a source warehouse",
            "edit": _set("from_warehouse", "MAIN"),
        },
        {
            "kind": "stock-entry",
            "why": "a transfer into the warehouse it came from (no stock moves)",
            "edit": lambda document: document.update(
                purpose="material_transfer", from_warehouse="MAIN", to_warehouse="MAIN"
            ),
        },
        {
            "kind": "stock-entry",
            "why": "purpose is outside the closed vocabulary",
            "edit": _set("purpose", "material_donation"),
        },
        {
            "kind": "gl-posting",
            "why": "a line carries both a debit and a credit",
            "edit": _both_sides,
        },
        {
            "kind": "gl-posting",
            "why": "a line carries neither a debit nor a credit",
            "edit": _neither_side,
        },
        {
            "kind": "gl-posting",
            "why": "a single-line posting is not a double entry",
            "edit": _set("lines", [{"account": "DEBTORS", "debit": 100.0}]),
        },
        {
            "kind": "gl-posting",
            "why": "debits do not equal credits",
            "edit": _uneven_balance,
        },
        {
            "kind": "gl-posting",
            "why": "voucher_type is outside the closed source vocabulary",
            "edit": _set("voucher_type", "spreadsheet"),
        },
    ]
    return cases


def valid_for(kind: str) -> Dict[str, Any]:
    """A fresh copy of the valid document for ``kind``."""
    return copy.deepcopy(valid_documents()[kind])


def mutated(case: Dict[str, Any]) -> Dict[str, Any]:
    """The document a mutant case describes, applied to a fresh valid copy."""
    document = valid_for(case["kind"])
    case["edit"](document)
    return document
