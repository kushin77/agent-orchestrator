"""The stock ledger, and the movement a delivery applies (ERP-03, issue #648).

Acceptance criterion 1 of #648 names the stock half explicitly: "stock movement
applied on delivery", and criterion 2 requires that cancelling a document
"reverses prior effects". Both live here, and both are *values*: the ledger is a
tuple of movements and :meth:`StockLedger.apply` returns a new ledger, so a
movement cannot be edited out of a ledger it is already on.

Three rules are read off ERP-02 rather than restated:

* **whether a line moves stock at all** is the item master's ``is_stock_item``
  flag — the field ``integrations/erp/core/schemas/item.schema.json`` documents as
  "the flag the transactional spine reads to decide whether a document commits
  stock", and a movement for an item the master says is not stocked is
  ``not-a-stock-item`` rather than a silent no-op;
* **what a movement is valued at** is the item's ``valuation_rate``, unless the
  line carries its own rate — the same schema's "the rate the stock ledger entry
  is written at";
* **which warehouse** the stock leaves is the line's ``warehouse`` when it has one
  and the document's otherwise, which is the delivery note's own documented
  behaviour ("a line may override it").

The item master and the warehouse set are both *inputs* (scenario data held by
:class:`~.spine.Workspace`). The item master is a set of ERP-02 item documents and
so is validated by ERP-02; the warehouse set is supplied because ERP-02 declares
no warehouse family — the indexer's catalogue does, but a declared document with
no schema cannot be validated, and this lane will not invent one.

---knowledge---
module_id: integrations.erp.tx.stock
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [money, quantity, Movement, StockLedger, movements_for, invert, reversal_is_exact]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import Finding, Refused

#: The item-master fields this lane reads. Named once so the rules below read as
#: rules rather than as string literals.
ITEM_FLAG = "is_stock_item"
ITEM_VALUATION = "valuation_rate"
LINE_ITEM_CODE = "item_code"
LINE_QTY = "qty"
LINE_RATE = "rate"
LINE_VALUATION = "valuation_rate"
LINE_WAREHOUSE = "warehouse"


def money(value: Any) -> float:
    """A monetary amount rounded to minor units, so two runs compare exactly.

    ERP-02 carries money as a JSON number; rounding at the boundary is what keeps
    "deterministic" from depending on the order additions happened to take.
    """
    return round(float(value) + 0.0, 2)


def quantity(value: Any) -> float:
    """A quantity, kept at the precision the document declared it with."""
    return round(float(value) + 0.0, 6)


@dataclass(frozen=True)
class Movement:
    """One stock movement: a signed quantity of an item in a warehouse.

    ``qty`` is signed — stock entering a warehouse is positive, stock leaving is
    negative — so a reversal is the same movement with its sign flipped and the
    ledger's arithmetic is plain addition. ``reversal`` records that this
    movement *is* a reversal, which is what lets a ledger name the pair rather
    than merely show a net of zero.
    """

    document: str
    kind: str
    item_code: str
    warehouse: str
    qty: float
    rate: float
    at: str
    reversal: bool = False

    @property
    def amount(self) -> float:
        return money(self.qty * self.rate)

    def inverted(self, *, at: str) -> "Movement":
        """The movement that exactly undoes this one."""
        return Movement(
            document=self.document,
            kind=self.kind,
            item_code=self.item_code,
            warehouse=self.warehouse,
            qty=-self.qty,
            rate=self.rate,
            at=at,
            reversal=not self.reversal,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document": self.document,
            "kind": self.kind,
            "itemCode": self.item_code,
            "warehouse": self.warehouse,
            "qty": self.qty,
            "rate": self.rate,
            "amount": self.amount,
            "at": self.at,
            "reversal": self.reversal,
        }


@dataclass(frozen=True)
class StockLedger:
    """An immutable stock ledger: the movements applied so far."""

    movements: Tuple[Movement, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.movements)

    def __iter__(self) -> Iterable[Movement]:
        return iter(self.movements)

    def apply(self, movements: Iterable[Movement]) -> "StockLedger":
        """Append movements and return the extended ledger."""
        return StockLedger(movements=self.movements + tuple(movements))

    def for_document(self, document: str) -> Tuple[Movement, ...]:
        return tuple(m for m in self.movements if m.document == document)

    def net_for_document(self, document: str) -> float:
        """The net quantity a document has left on the ledger (0 when reversed)."""
        return quantity(sum(m.qty for m in self.for_document(document)))

    def balance(self, item_code: str, warehouse: str) -> float:
        """The quantity of ``item_code`` on hand in ``warehouse``."""
        return quantity(
            sum(
                m.qty
                for m in self.movements
                if m.item_code == item_code and m.warehouse == warehouse
            )
        )

    def value(self, item_code: str, warehouse: str) -> float:
        """The value on hand, at the rates the movements were written at."""
        return money(
            sum(
                m.amount
                for m in self.movements
                if m.item_code == item_code and m.warehouse == warehouse
            )
        )

    def to_list(self) -> List[Dict[str, Any]]:
        return [movement.to_dict() for movement in self.movements]

    def verify(self) -> List[Finding]:
        """Every way this ledger contradicts itself (empty is OK)."""
        findings: List[Finding] = []
        for movement in self.movements:
            if not movement.document or not movement.item_code or not movement.warehouse:
                findings.append(
                    Finding(
                        "missing-field",
                        "a movement must name its document, item and warehouse "
                        f"(got {movement.to_dict()!r})",
                        ref=movement.document,
                    )
                )
            if movement.qty == 0:
                findings.append(
                    Finding(
                        "invalid-value",
                        f"{movement.document}: a movement of zero moves nothing",
                        ref=movement.document,
                    )
                )
            if movement.rate < 0:
                findings.append(
                    Finding(
                        "invalid-value",
                        f"{movement.document}: a negative rate is not a valuation",
                        ref=movement.document,
                    )
                )
        return findings


def _valuation_rate(line: Mapping[str, Any], item: Mapping[str, Any]) -> float:
    """The rate a stock ledger entry is written at.

    The **cost basis**, not the selling price. A line's own ``valuation_rate``
    when it declares one — ERP-02's stock-entry schema documents it as "the rate
    the stock ledger entry is written at" — otherwise the item master's
    ``valuation_rate``, which that schema documents as "what a stock ledger entry
    debits". The line's ``rate`` is the *selling* rate and is deliberately not
    consulted first: a delivery priced at 20.00 still leaves the warehouse at the
    12.00 it cost, and valuing the outflow at the selling price would post the
    margin into the stock ledger.
    """
    for source, field_name in ((line, LINE_VALUATION), (item, ITEM_VALUATION)):
        value = source.get(field_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return money(value)
    fallback = line.get(LINE_RATE)
    if isinstance(fallback, (int, float)) and not isinstance(fallback, bool):
        return money(fallback)
    return 0.0


def _line_warehouse(line: Mapping[str, Any], document: Mapping[str, Any]) -> Optional[str]:
    """The warehouse a line moves stock through: the line's, else the document's."""
    for source in (line, document):
        value = source.get(LINE_WAREHOUSE)
        if isinstance(value, str) and value.strip():
            return value
    return None


def movements_for(
    document: Mapping[str, Any],
    *,
    kind: str,
    reference: str,
    items: Mapping[str, Mapping[str, Any]],
    warehouses: Sequence[str],
    at: str,
    sign: int = -1,
) -> Tuple[Movement, ...]:
    """The movements a stock-committing document applies, or the refusal.

    ``sign`` is the direction of the *effect* the document has: a delivery takes
    stock out (``-1``), and a reversal of that delivery takes stock back in, so
    the reversal is produced by calling this again with the opposite sign rather
    than by re-deriving anything.

    Every refusal names the offender: an item the master does not carry
    (``unknown-item``), an item the master says is not stocked
    (``not-a-stock-item``), a warehouse outside the declared set
    (``unknown-warehouse``), or a line that names no warehouse at all when the
    document declares none either (``missing-field``).
    """
    lines = document.get("lines")
    if not isinstance(lines, list) or not lines:
        raise Refused("missing-field", f"{reference}: the document declares no lines")

    known_warehouses = set(warehouses)
    movements: List[Movement] = []
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            raise Refused("invalid-value", f"{reference}: lines[{index}] must be an object")
        item_code = line.get(LINE_ITEM_CODE)
        if not isinstance(item_code, str) or not item_code.strip():
            raise Refused(
                "missing-field", f"{reference}: lines[{index}] names no item_code"
            )
        item = items.get(item_code)
        if item is None:
            raise Refused(
                "unknown-item",
                f"{reference}: lines[{index}] cites item {item_code!r}, which the "
                f"item master does not carry (known: {', '.join(sorted(items)) or 'none'})",
            )
        if item.get(ITEM_FLAG) is not True:
            raise Refused(
                "not-a-stock-item",
                f"{reference}: line {index} cites {item_code!r}, which the item "
                f"master declares is not a stock item ({ITEM_FLAG} is false)",
            )
        warehouse = _line_warehouse(line, document)
        if warehouse is None:
            raise Refused(
                "missing-field",
                f"{reference}: line {index} names no warehouse and the document "
                "declares no default",
            )
        if warehouse not in known_warehouses:
            raise Refused(
                "unknown-warehouse",
                f"{reference}: line {index} names warehouse {warehouse!r}, which is "
                f"not declared (known: {', '.join(sorted(known_warehouses)) or 'none'})",
            )
        rate = _valuation_rate(line, item)
        try:
            qty = float(line.get(LINE_QTY))
        except (TypeError, ValueError) as exc:
            raise Refused(
                "invalid-value", f"{reference}: lines[{index}] has no usable quantity"
            ) from exc
        movements.append(
            Movement(
                document=reference,
                kind=kind,
                item_code=item_code,
                warehouse=warehouse,
                qty=quantity(sign * qty),
                rate=money(rate),
                at=at,
            )
        )
    return tuple(movements)


def invert(movements: Iterable[Movement], *, at: str) -> Tuple[Movement, ...]:
    """The exact inverse of a set of movements, at ``at``."""
    return tuple(movement.inverted(at=at) for movement in movements)


def reversal_is_exact(ledger: StockLedger, document: str) -> bool:
    """Whether ``document``'s net effect on the ledger is exactly nothing.

    The check criterion 2 needs: a cancellation has to *undo* the effect, not
    merely reduce it. A non-zero net after reversal is ``reversal-mismatch`` at
    the caller, naming the residual.
    """
    return ledger.net_for_document(document) == 0
