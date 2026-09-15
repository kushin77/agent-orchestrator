"""Manufacturing: bill-of-materials explosion and a work order consuming stock.

Acceptance criterion 2 of issue #649 is that "BOM explosion and a work order
consuming stock are correct and offline". The two halves are separate on
purpose:

* :func:`explode` is a **pure walk** over the bills of materials. It returns the
  leaf components a quantity of an item needs — expanding every component that
  is itself produced, and refusing ``bom-cycle`` when a bill transitively
  contains its own item rather than recursing until the stack runs out. It moves
  nothing, so an explosion can be inspected (and a plan costed) without touching
  a warehouse.
* :func:`complete_work_order` is the **only** place in this lane where a
  document moves stock twice: the components leave the source warehouse on a
  ``material_issue`` entry and the produced item enters the target one on a
  ``manufacture`` entry, and the two ledger rows move the value from Stock In
  Hand through Work In Process and back. Both entries are validated by ERP-02's
  own stock-entry schema, whose conditionals decide which warehouses each
  purpose must name.

``complete_work_order`` is also where the lane's third acceptance criterion
lands. Completing a work order twice is refused — **by name**, before anything
is exploded, issued or posted — because a work order consumes its bill of
materials exactly once. The state machine would also refuse it (``complete`` is
not an action available from ``completed``), and the named rule runs first so the
refusal says which order it was and what state it is in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .model import (
    ACTION_EXPLODE,
    ACTION_ISSUE,
    ACTION_PLAN,
    ACTION_PRODUCE,
    KIND_BOM,
    KIND_PRODUCTION_PLAN,
    KIND_WORK_ORDER,
    Refused,
)
from .workspace import Workspace

__all__ = [
    "ENGINEER",
    "OPERATOR",
    "PLANNER",
    "activate_bom",
    "bom_for",
    "complete_work_order",
    "components_of",
    "define_bom",
    "explode",
    "plan_production",
    "raise_work_order",
    "run_plan",
]

#: The actors the rail attributes each step to.
ENGINEER = "engineer"
PLANNER = "planner"
OPERATOR = "operator"


def _date(at: str) -> str:
    return at[:10]


def _bom(space: Workspace, bom_id: Any, *, what: str) -> Mapping[str, Any]:
    """One bill of materials, or a refusal naming what the store holds."""
    found = space.find(bom_id)
    if found is None or found.get("doctype") != KIND_BOM:
        raise Refused(
            "unknown-bom",
            f"{what}: {bom_id!r} is not a known bill of materials; known: "
            f"{[document['id'] for document in space.of_kind(KIND_BOM)]}",
        )
    return found


def bom_for(
    space: Workspace, item: Any, *, active_only: bool = True
) -> Optional[Mapping[str, Any]]:
    """The bill of materials that produces ``item``, or ``None``.

    ``None`` rather than a refusal: most components are bought rather than made,
    and an explosion is *supposed* to reach a leaf. A caller that needed an
    active bill (``run_plan``, a work order) refuses on the ``None`` itself, so
    the absence is handled where it matters rather than here.
    """
    for bom in space.of_kind(KIND_BOM):
        if bom.get("item") != item:
            continue
        if active_only and bom.get("state") != "submitted":
            continue
        return bom
    return None


def _valuation(space: Workspace, item_code: str) -> float:
    """An item's valuation rate, which is what its stock movement is written at."""
    item = space.item(item_code)
    rate = item.get("valuation_rate")
    if rate is None:
        raise Refused(
            "invalid-value",
            f"{item_code}: the item master declares no valuation_rate, so a stock "
            "movement of it has no value",
        )
    return float(rate)


def define_bom(
    space: Workspace,
    *,
    bom_id: str,
    item: str,
    quantity: float,
    components: Sequence[Mapping[str, Any]],
    at: str,
    actor: str = ENGINEER,
    rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Write a bill of materials, in draft until :func:`activate_bom`."""
    space.item(item)
    for component in components:
        space.item(component.get("item_code"))
    document: Dict[str, Any] = {
        "doctype": KIND_BOM,
        "id": bom_id,
        "state": "draft",
        "docstatus": 0,
        "company": space.company,
        "item": item,
        "quantity": quantity,
        "components": [dict(component) for component in components],
    }
    if rate is not None:
        document["rate"] = rate
    return space.create(document, at=at, actor=actor, where=bom_id)


def activate_bom(
    space: Workspace, *, bom_id: str, at: str, actor: str = ENGINEER
) -> Dict[str, Any]:
    """Put a bill of materials into force — the state an explosion requires."""
    bom = _bom(space, bom_id, what="activate")
    return space.advance(bom, "submit", at=at, actor=actor)


def explode(
    space: Workspace,
    bom: Mapping[str, Any],
    *,
    quantity: float,
    path: Tuple[str, ...] = (),
) -> Dict[str, float]:
    """The leaf components ``quantity`` of ``bom``'s item requires.

    Component quantities are scaled by ``quantity / bom['quantity']``, and a
    component that is itself produced is expanded rather than listed — so a
    two-level product reaches raw material in one call. ``path`` carries the
    items already exploded, which is what turns a cycle into ``bom-cycle``
    instead of unbounded recursion.
    """
    if not isinstance(bom, Mapping):
        raise Refused("invalid-value", f"a bill of materials must be a mapping, got {type(bom).__name__}")
    if bom.get("state") != "submitted":
        raise Refused(
            "bom-not-submitted",
            f"bom {bom.get('id')} is {bom.get('state')!r}; only an active bill is "
            "exploded",
        )
    item = bom.get("item")
    if item in path:
        chain = " -> ".join(list(path) + [str(item)])
        raise Refused(
            "bom-cycle",
            f"bom {bom.get('id')} explodes back into {item!r}: {chain}",
        )
    base = float(bom.get("quantity") or 0)
    if base <= 0:
        raise Refused(
            "invalid-value",
            f"bom {bom.get('id')}: quantity must be positive, got {bom.get('quantity')!r}",
        )
    factor = float(quantity) / base
    required: Dict[str, float] = {}
    for component in bom.get("components", []):
        code = component["item_code"]
        qty = round(float(component["qty"]) * factor, 6)
        if qty <= 0:
            continue
        nested = bom_for(space, code)
        if nested is None:
            required[code] = round(required.get(code, 0.0) + qty, 6)
            continue
        deeper = explode(space, nested, quantity=qty, path=path + (item,))
        for leaf, leaf_qty in deeper.items():
            required[leaf] = round(required.get(leaf, 0.0) + leaf_qty, 6)
    return {code: required[code] for code in sorted(required)}


def components_of(
    bom: Mapping[str, Any], *, quantity: float
) -> List[Dict[str, Any]]:
    """The bill's **direct** components, scaled to ``quantity``.

    One level, not the full explosion, and the distinction is the difference
    between a correct ledger and a double count. A work order consumes what its
    own bill declares: a component that is itself produced is consumed *as
    itself*, and the order that makes it is a separate piece of work — which is
    exactly what :func:`explode` exists to find when planning. Back-flushing the
    expanded leaves here would consume the sub-assembly's raw material a second
    time and leave the sub-assembly itself produced and never used.

    So ``explode`` is the planning walk (what the whole build needs from raw
    material) and this is the consuming list, and the completion records both.
    A component's declared ``warehouse`` is carried through, because a
    sub-assembly is produced into one warehouse and consumed from it, which is
    not necessarily where its parent's other components come from.
    """
    base = float(bom.get("quantity") or 0)
    if base <= 0:
        raise Refused(
            "invalid-value",
            f"bom {bom.get('id')}: quantity must be positive, got {bom.get('quantity')!r}",
        )
    factor = float(quantity) / base
    lines: List[Dict[str, Any]] = []
    for component in bom.get("components", []):
        qty = round(float(component["qty"]) * factor, 6)
        if qty <= 0:
            continue
        line: Dict[str, Any] = {"item_code": component["item_code"], "qty": qty}
        warehouse = component.get("warehouse")
        if isinstance(warehouse, str) and warehouse.strip():
            line["warehouse"] = warehouse
        lines.append(line)
    return sorted(lines, key=lambda entry: entry["item_code"])


def raise_work_order(
    space: Workspace,
    *,
    wo_id: str,
    bom_id: str,
    quantity: float,
    at: str,
    from_warehouse: str,
    to_warehouse: str,
    actor: str = PLANNER,
) -> Dict[str, Any]:
    """Start a production order against an active bill of materials."""
    bom = _bom(space, bom_id, what=f"work order {wo_id}")
    if bom.get("state") != "submitted":
        raise Refused(
            "bom-not-submitted",
            f"work order {wo_id} cites bom {bom_id}, which is {bom.get('state')!r}",
        )
    document: Dict[str, Any] = {
        "doctype": KIND_WORK_ORDER,
        "id": wo_id,
        "state": "draft",
        "docstatus": 0,
        "company": space.company,
        "transaction_date": _date(at),
        "bom": bom_id,
        "item": bom["item"],
        "quantity": quantity,
        "from_warehouse": from_warehouse,
        "to_warehouse": to_warehouse,
    }
    created = space.create(document, at=at, actor=actor, where=wo_id)
    return space.advance(created, "submit", at=at, actor=actor)


def complete_work_order(
    space: Workspace, *, wo_id: str, at: str, actor: str = OPERATOR
) -> Dict[str, Any]:
    """Consume the bill of materials and receive the output, exactly once.

    The named rule below runs before anything is exploded, issued or posted, so
    a repeated completion is refused by name and moves no stock — which is the
    acceptance criterion, and not merely a consequence of the state machine.
    """
    work_order = space.find(wo_id)
    if work_order is None or work_order.get("doctype") != KIND_WORK_ORDER:
        raise Refused(
            "unknown-work-order",
            f"{wo_id!r} is not a known work order; known: "
            f"{[document['id'] for document in space.of_kind(KIND_WORK_ORDER)]}",
        )
    if work_order.get("state") == "completed":
        raise Refused(
            "work-order-already-completed",
            f"work order {wo_id} is already completed; a work order consumes its "
            "bill of materials exactly once",
        )
    if work_order.get("state") != "submitted":
        raise Refused(
            "work-order-not-submitted",
            f"work order {wo_id} is {work_order.get('state')!r}; only a started "
            "order completes",
        )
    bom = _bom(space, work_order.get("bom"), what=f"work order {wo_id}")
    if bom.get("state") != "submitted":
        raise Refused(
            "bom-not-submitted",
            f"work order {wo_id} cites bom {bom.get('id')}, which is "
            f"{bom.get('state')!r}",
        )
    if bom.get("item") != work_order.get("item"):
        raise Refused(
            "bom-item-mismatch",
            f"work order {wo_id} produces {work_order.get('item')!r} but bom "
            f"{bom.get('id')} produces {bom.get('item')!r}",
        )

    quantity = float(work_order["quantity"])
    consumed = components_of(bom, quantity=quantity)
    planned = explode(space, bom, quantity=quantity)
    space.rail = space.rail.append(
        at=at,
        actor=actor,
        action=ACTION_EXPLODE,
        document=wo_id,
        detail=(
            f"bom {bom['id']} for {quantity:g} {work_order['item']}: consumes "
            + ", ".join(f"{line['item_code']} x {line['qty']:g}" for line in consumed)
            + " | explodes to "
            + ", ".join(f"{code} x {qty:g}" for code, qty in sorted(planned.items()))
        ),
    )

    space.post(
        "material-issue",
        work_order,
        at=at,
        actor=actor,
        action=ACTION_ISSUE,
        currency=space.catalog.currency,
        lines=[
            {
                **line,
                "valuation_rate": _valuation(space, line["item_code"]),
            }
            for line in consumed
        ],
        where=f"work order {wo_id} issue",
    )

    produced = space.replace(
        dict(space.get(wo_id), produced_quantity=quantity),
        where=wo_id,
    )
    space.post(
        "manufacture",
        produced,
        at=at,
        actor=actor,
        action=ACTION_PRODUCE,
        currency=space.catalog.currency,
        lines=[
            {
                "item_code": work_order["item"],
                "qty": quantity,
                "warehouse": work_order["to_warehouse"],
                "valuation_rate": _valuation(space, work_order["item"]),
            }
        ],
        where=f"work order {wo_id} produce",
    )
    return space.advance(produced, "complete", at=at, actor=actor)


def plan_production(
    space: Workspace,
    *,
    plan_id: str,
    entries: Sequence[Mapping[str, Any]],
    at: str,
    actor: str = PLANNER,
    from_warehouse: Optional[str] = None,
    to_warehouse: Optional[str] = None,
) -> Dict[str, Any]:
    """Decide a production plan. Intent only: nothing moves, no order is raised."""
    for entry in entries:
        space.item(entry.get("item"))
    document: Dict[str, Any] = {
        "doctype": KIND_PRODUCTION_PLAN,
        "id": plan_id,
        "state": "draft",
        "docstatus": 0,
        "company": space.company,
        "transaction_date": _date(at),
        "entries": [dict(entry) for entry in entries],
    }
    if from_warehouse is not None:
        document["from_warehouse"] = from_warehouse
    if to_warehouse is not None:
        document["to_warehouse"] = to_warehouse
    created = space.create(document, at=at, actor=actor, where=plan_id)
    submitted = space.advance(created, "submit", at=at, actor=actor)
    space.rail = space.rail.append(
        at=at,
        actor=actor,
        action=ACTION_PLAN,
        document=plan_id,
        detail=(
            f"production plan {plan_id} decided: "
            + ", ".join(f"{float(e['quantity']):g} x {e['item']}" for e in entries)
        ),
    )
    return submitted


def run_plan(
    space: Workspace,
    *,
    plan_id: str,
    at: str,
    actor: str = PLANNER,
    prefix: str = "WO",
) -> Tuple[Dict[str, Any], ...]:
    """Raise a decided plan into work orders, one per entry, and close it."""
    plan = space.get(plan_id)
    if plan.get("doctype") != KIND_PRODUCTION_PLAN:
        raise Refused(
            "unknown-document",
            f"{plan_id!r} is a {plan.get('doctype')!r}, not a production plan",
        )
    if plan.get("state") != "submitted":
        raise Refused(
            "invalid-value",
            f"plan {plan_id} is {plan.get('state')!r}; only a decided plan is raised",
        )
    orders: List[Dict[str, Any]] = []
    for position, entry in enumerate(plan["entries"]):
        bom = bom_for(space, entry["item"])
        if bom is None:
            raise Refused(
                "unknown-bom",
                f"plan {plan_id}: no active bill of materials produces "
                f"{entry['item']!r}, so its entry cannot be raised",
            )
        orders.append(
            raise_work_order(
                space,
                wo_id=f"{prefix}-{plan_id}-{position + 1:02d}",
                bom_id=bom["id"],
                quantity=entry["quantity"],
                at=at,
                actor=actor,
                from_warehouse=entry.get("from_warehouse") or plan.get("from_warehouse"),
                to_warehouse=entry.get("to_warehouse") or plan.get("to_warehouse"),
            )
        )
    space.advance(space.get(plan_id), "complete", at=at, actor=actor)
    return tuple(orders)
