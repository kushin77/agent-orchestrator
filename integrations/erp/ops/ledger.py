"""Stock and ledger effects: what a document does to the books, as data.

Two things live here, and both are data-driven from the catalogue rather than
hard-coded, so a change of declaration is a change of behaviour and not a code
change:

* **the stock effect of a purpose.** The catalogue's ``stock_effects`` table
  says which warehouse a purpose moves and which way (a receipt adds to the
  receiving warehouse, an issue removes from the source, a transfer does both,
  a manufacture adds the produced item). :func:`apply_stock` walks that table,
  so all four of ERP-02's purposes are exercised by the same six lines rather
  than by four special cases.
* **the ledger effect of a family.** The catalogue's posting rules say which
  accounts a family debits and credits and what each amount is derived from
  (``net_total``, ``taxes`` or ``total``). :func:`build_gl_posting` renders them,
  and the result is validated by **ERP-02's own model**, whose declared family
  rule refuses an unbalanced posting by name — so "the posting balances" is not
  this lane's assertion about itself, it is the core model's verdict.

Two invariants this module will not let slide:

* **a stock movement cannot take stock that is not there.** :func:`apply_stock`
  refuses ``insufficient-stock`` before returning, and it refuses by building
  the new ledger first and returning it only if nothing went negative, so a
  refused movement leaves the caller's ledger untouched.
* **a derived total cannot disagree with its lines.** When a document declares
  ``net_total`` or ``total``, :func:`amounts` cross-checks it against the lines
  and refuses ``invalid-value`` on a mismatch, rather than silently preferring
  one of the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import documents
from .catalog import Catalog, Posting
from .model import KIND_GL_POSTING, KIND_STOCK_ENTRY, Model, Refused

__all__ = [
    "Posted",
    "amounts",
    "apply_stock",
    "balances",
    "build_gl_posting",
    "build_stock_entry",
    "line_amount",
    "post",
    "posting_date_for",
]


def line_amount(line: Mapping[str, Any], items: Optional[Mapping[str, Any]] = None) -> float:
    """One line's amount: declared, or derived from qty and a rate.

    The rate comes from the line (``rate`` on a trading line, ``valuation_rate``
    on a stock line) and, when the line carries neither, from the item master —
    which is why an item the master does not know is refused here rather than
    silently valued at zero.
    """
    if not isinstance(line, Mapping):
        raise Refused("invalid-value", f"a line must be a mapping, got {type(line).__name__}")
    declared = line.get("amount")
    if declared is not None:
        return float(declared)
    rate = line.get("rate")
    if rate is None:
        rate = line.get("valuation_rate")
    if rate is None:
        item_code = line.get("item_code")
        item = items.get(item_code) if items else None
        if item is None:
            raise Refused(
                "unknown-item",
                f"{item_code!r} declares no rate and is not in the item master, so "
                "its amount cannot be derived",
            )
        rate = item.get("valuation_rate")
        if rate is None:
            raise Refused(
                "invalid-value",
                f"{item_code!r}: the item master declares no valuation_rate, so the "
                "line's amount cannot be derived",
            )
    return float(line.get("qty", 0) or 0) * float(rate)


def amounts(
    document: Mapping[str, Any], *, items: Optional[Mapping[str, Any]] = None
) -> Dict[str, float]:
    """``net_total``, ``taxes`` and ``total`` for a document, derived and checked."""
    lines = document.get("lines") or []
    if not isinstance(lines, list):
        raise Refused(
            "invalid-value", f"{document.get('id')}: lines must be a list"
        )
    net = round(sum(line_amount(line, items) for line in lines), 2)
    taxes = round(
        sum(
            float(tax.get("amount", 0) or 0)
            for tax in (document.get("taxes") or [])
        ),
        2,
    )
    total = round(net + taxes, 2)
    declared_net = document.get("net_total")
    if declared_net is not None and round(float(declared_net), 2) != net:
        raise Refused(
            "invalid-value",
            f"{document.get('id')}: net_total {declared_net} is not the lines' {net}",
        )
    declared_total = document.get("total")
    if declared_total is not None and round(float(declared_total), 2) != total:
        raise Refused(
            "invalid-value",
            f"{document.get('id')}: total {declared_total} is not net_total + taxes "
            f"= {total}",
        )
    return {"net_total": net, "taxes": taxes, "total": total}


def balances(rule: Posting, figures: Mapping[str, float]) -> bool:
    """Whether a posting rule's account lines balance for these figures."""
    debits = sum(
        figures[line["basis"]] for line in rule.gl if line["side"] == "debit"
    )
    credits = sum(
        figures[line["basis"]] for line in rule.gl if line["side"] == "credit"
    )
    return abs(round(debits, 2) - round(credits, 2)) <= 0.005


def stock_effect(catalog: Catalog, purpose: str) -> Mapping[str, int]:
    """Which warehouses a purpose moves and which way, from the catalogue."""
    catalog.term("stock-purposes", purpose)
    return catalog.stock_effects[purpose]


def posting_date_for(document: Mapping[str, Any]) -> str:
    """The accounting date: declared, or the document's own transaction date.

    Never the wall clock — a posting whose period depended on when it was
    written could not be reproduced, and the whole lane is offline and
    deterministic.
    """
    for field in ("posting_date", "transaction_date"):
        value = document.get(field)
        if isinstance(value, str) and value.strip():
            return value
    raise Refused(
        "invalid-value",
        f"{document.get('id')}: no posting_date or transaction_date, so the ledger "
        "row has no period",
    )


def _warehouse(document: Mapping[str, Any], field: str) -> Optional[str]:
    """The warehouse a stock effect names, from the document that drives it."""
    value = document.get(field)
    if isinstance(value, str) and value.strip():
        return value
    fallback = document.get("warehouse")
    if isinstance(fallback, str) and fallback.strip():
        return fallback
    return None


def build_stock_entry(
    model: Model,
    catalog: Catalog,
    key: str,
    document: Mapping[str, Any],
    *,
    entry_id: str,
    lines: Sequence[Mapping[str, Any]],
    where: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """The ``stock-entry`` a posting rule produces, or ``None`` when it moves none.

    The warehouses emitted are the ones the catalogue says the purpose must
    *name* — not merely the ones it moves — so ERP-02's ``if/then`` conditionals
    hold by construction: a receipt names its receiving warehouse and no source,
    an issue the reverse, and a manufacture names both even though it adds only
    the produced item.
    """
    rule = catalog.posting(key)
    if rule.purpose is None:
        return None
    label = where or entry_id
    if not lines:
        raise Refused("invalid-value", f"{label}: a {rule.purpose} must move at least one line")
    entry: Dict[str, Any] = {
        "doctype": KIND_STOCK_ENTRY,
        "id": entry_id,
        "state": "submitted",
        "docstatus": 1,
        "company": document["company"],
        "purpose": rule.purpose,
        "lines": [dict(line) for line in lines],
    }
    if isinstance(document.get("transaction_date"), str):
        entry["transaction_date"] = document["transaction_date"]
    for field in catalog.stock_warehouses_for(rule.purpose):
        value = _warehouse(document, field)
        if value is None:
            raise Refused(
                "invalid-value",
                f"{label}: a {rule.purpose} must name {field}, and "
                f"{document.get('id')} names none",
            )
        entry[field] = value
    return documents.parse(model, entry, kind=KIND_STOCK_ENTRY, where=label)


def apply_stock(
    catalog: Catalog,
    stock: Mapping[str, Mapping[str, float]],
    entry: Mapping[str, Any],
    *,
    items: Mapping[str, Any],
) -> Dict[str, Dict[str, float]]:
    """The ledger after ``entry`` moves stock, or a refusal that moves nothing.

    The new ledger is built first and returned only when nothing went negative,
    so a refused movement cannot leave a partially-applied ledger behind.

    A **single-sided** movement (a receipt, an issue, a manufacture) honours the
    warehouse named on each line, falling back to the document's own: a
    sub-assembly is produced into one warehouse and consumed from it, which is
    not necessarily where its parent's other components live. A **two-sided**
    movement (a transfer) refuses a per-line warehouse, because there the line's
    warehouse would be ambiguous between the source and the destination — and an
    ambiguity that resolves silently is worse than a refusal.
    """
    purpose = entry.get("purpose")
    effects = stock_effect(catalog, purpose)
    lines = list(entry.get("lines", []))
    overridden = [line for line in lines if line.get("warehouse")]
    if len(effects) > 1 and overridden:
        raise Refused(
            "invalid-value",
            f"{entry.get('id')}: a {purpose} moves between two named warehouses, so "
            f"the per-line warehouse on {overridden[0].get('item_code')!r} would be "
            "ambiguous between them",
        )
    ledger: Dict[str, Dict[str, float]] = {
        warehouse: dict(bucket) for warehouse, bucket in stock.items()
    }
    for field, sign in sorted(effects.items()):
        default = entry.get(field)
        if default is None:
            raise Refused(
                "invalid-value", f"{entry.get('id')}: a {purpose} names no {field}"
            )
        for line in lines:
            item_code = line.get("item_code")
            if item_code not in items:
                raise Refused(
                    "unknown-item",
                    f"{entry.get('id')}: {item_code!r} is not in the item master",
                )
            warehouse = line.get("warehouse") or default
            bucket = ledger.setdefault(warehouse, {})
            bucket[item_code] = round(
                bucket.get(item_code, 0.0) + sign * float(line.get("qty", 0) or 0), 6
            )
    short = sorted(
        (warehouse, item_code, qty)
        for warehouse, bucket in ledger.items()
        for item_code, qty in bucket.items()
        if qty < 0
    )
    if short:
        rendered = ", ".join(
            f"{warehouse}/{item_code} would be {qty:g}" for warehouse, item_code, qty in short
        )
        raise Refused(
            "insufficient-stock",
            f"{entry.get('id')} ({purpose}) cannot move stock that is not there: {rendered}",
        )
    return ledger


def build_gl_posting(
    model: Model,
    catalog: Catalog,
    key: str,
    document: Mapping[str, Any],
    *,
    posting_id: str,
    currency: str,
    items: Optional[Mapping[str, Any]] = None,
    where: Optional[str] = None,
    source: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The ``gl-posting`` a posting rule produces, validated by ERP-02's model.

    The amounts come from ``source`` — the document whose **lines** the posting
    is the ledger image of. For a purchase invoice that is the invoice itself;
    for a receipt or a work order it is the stock entry the movement produced,
    because a work order carries no lines of its own and the value that moved is
    exactly what its stock entry says moved.

    Zero-amount lines are dropped rather than emitted: the core schema requires
    every row to carry exactly one *non-zero* side, so a rule that names an
    account the document does not use produces fewer rows, not an invalid row.
    A rule left with only one side after that is refused as
    ``unbalanced-posting`` — the vocabulary's own name for a ledger row with no
    counter-row.
    """
    rule = catalog.posting(key)
    label = where or posting_id
    figures = amounts(source if source is not None else document, items=items)
    lines: List[Dict[str, Any]] = []
    for line in rule.gl:
        value = round(float(figures[line["basis"]]), 2)
        if value <= 0:
            continue
        lines.append({"account": line["account"], line["side"]: value})
    if len(lines) < 2:
        raise Refused(
            "unbalanced-posting",
            f"{label}: {rule.key} derives {figures} from {document.get('id')}, so only "
            f"{len(lines)} side(s) carry a non-zero amount — a double entry needs two",
        )
    posting: Dict[str, Any] = {
        "doctype": KIND_GL_POSTING,
        "id": posting_id,
        "state": "submitted",
        "docstatus": 1,
        "company": document["company"],
        "currency": currency,
        "posting_date": posting_date_for(document),
        "voucher_type": rule.voucher_type,
        "voucher_id": document["id"],
        "remarks": f"{rule.key} from {document['id']}",
        "lines": lines,
    }
    return documents.parse(model, posting, kind=KIND_GL_POSTING, where=label)


@dataclass(frozen=True)
class Posted:
    """What one posting produced: the stock entry, the ledger row, the ledger."""

    key: str
    stock_entry: Optional[Dict[str, Any]]
    gl_posting: Dict[str, Any]
    stock: Mapping[str, Mapping[str, float]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "stock_entry": self.stock_entry,
            "gl_posting": self.gl_posting,
            "stock": {warehouse: dict(bucket) for warehouse, bucket in self.stock.items()},
        }


def post(
    model: Model,
    catalog: Catalog,
    key: str,
    document: Mapping[str, Any],
    *,
    entry_id: Optional[str] = None,
    posting_id: Optional[str] = None,
    lines: Optional[Sequence[Mapping[str, Any]]] = None,
    stock: Mapping[str, Mapping[str, float]],
    items: Mapping[str, Any],
    currency: Optional[str] = None,
    where: Optional[str] = None,
) -> Posted:
    """Post one document's effects: stock movement (if any) and the ledger row.

    The return value carries the new ledger, so the caller applies the movement
    by adopting it — there is no in-place mutation to forget.
    """
    rule = catalog.posting(key)
    label = where or f"{key} from {document.get('id')}"
    entry = build_stock_entry(
        model,
        catalog,
        key,
        document,
        entry_id=entry_id or f"STE-{document.get('id')}",
        lines=lines if lines is not None else list(document.get("lines") or []),
        where=label,
    )
    ledger = stock
    if entry is not None:
        ledger = apply_stock(catalog, stock, entry, items=items)
    posting = build_gl_posting(
        model,
        catalog,
        key,
        document,
        posting_id=posting_id or f"GL-{document.get('id')}",
        currency=currency or document.get("currency") or catalog.currency,
        items=items,
        where=label,
        source=entry if entry is not None else document,
    )
    return Posted(key=rule.key, stock_entry=entry, gl_posting=posting, stock=ledger)
