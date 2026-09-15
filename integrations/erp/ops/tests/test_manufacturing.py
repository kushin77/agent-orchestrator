"""Acceptance criteria 2 and 3: BOM explosion, stock consumption, and the refusals.

Criterion 2 is that an explosion and a work order consuming stock are correct
and offline. Criterion 3 names two refusals: a receipt without a purchase order
(asserted in ``test_procurement.py``) and completing the same work order twice,
which is asserted here — along with the property that makes it matter, that the
second completion moves no stock.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

import pytest

from integrations.erp.ops import flows, manufacturing, procurement
from integrations.erp.ops.model import Model, Refused
from integrations.erp.ops.workspace import Workspace
from .helpers import assert_refused

SUPPLY: Mapping[str, str] = {
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


def stocked(space: Workspace) -> Workspace:
    """A workspace with the raw material a sub-assembly run needs."""
    procurement.purchase_cycle(
        space,
        ids=SUPPLY,
        supplier="SUP-1",
        lines=[
            {"item_code": "RAW-A", "qty": 40, "rate": 5.0},
            {"item_code": "RAW-B", "qty": 60, "rate": 2.0},
        ],
        warehouse=flows.RAW_WAREHOUSE,
        at=AT,
        tax_rate=0.10,
    )
    for bom_id, item, components in (
        ("BOM-SUB-1", "SUB-1", [{"item_code": "RAW-A", "qty": 2}, {"item_code": "RAW-B", "qty": 1}]),
        ("BOM-FG-1", "FG-1", [{"item_code": "SUB-1", "qty": 1}, {"item_code": "RAW-B", "qty": 3}]),
    ):
        manufacturing.define_bom(
            space,
            bom_id=bom_id,
            item=item,
            quantity=1,
            components=components,
            at=flows.T["bom"],
        )
        manufacturing.activate_bom(space, bom_id=bom_id, at=flows.T["bom"])
    return space


def work_order(**overrides: Any) -> Dict[str, Any]:
    """A work order that validates, with any field overridden."""
    document: Dict[str, Any] = {
        "doctype": "work-order",
        "id": "WO-T1",
        "state": "submitted",
        "docstatus": 1,
        "company": "COMPANY-1",
        "bom": "BOM-SUB-1",
        "item": "SUB-1",
        "quantity": 1,
        "from_warehouse": flows.RAW_WAREHOUSE,
        "to_warehouse": flows.FINISHED_WAREHOUSE,
    }
    document.update(overrides)
    return document


def test_a_bill_produces_the_components_it_declares(model: Model, space: Workspace) -> None:
    stocked(space)
    assert manufacturing.explode(space, space.get("BOM-SUB-1"), quantity=1) == {
        "RAW-A": 2.0,
        "RAW-B": 1.0,
    }


def test_an_explosion_reaches_raw_material_through_a_sub_assembly(
    model: Model, space: Workspace
) -> None:
    """A two-level bill is expanded, not listed: the sub-assembly is not a leaf."""
    stocked(space)
    assert manufacturing.explode(space, space.get("BOM-FG-1"), quantity=1) == {
        "RAW-A": 2.0,
        "RAW-B": 4.0,
    }


def test_an_explosion_scales_with_the_quantity(model: Model, space: Workspace) -> None:
    stocked(space)
    assert manufacturing.explode(space, space.get("BOM-FG-1"), quantity=4) == {
        "RAW-A": 8.0,
        "RAW-B": 16.0,
    }


def test_an_explosion_moves_nothing(model: Model, space: Workspace) -> None:
    """It is a walk: reading a bill must not touch a warehouse."""
    stocked(space)
    before = {warehouse: dict(bucket) for warehouse, bucket in space.stock.items()}
    manufacturing.explode(space, space.get("BOM-FG-1"), quantity=4)
    assert space.stock == before


def test_a_work_order_consumes_its_bill_and_receives_the_output(
    model: Model, space: Workspace
) -> None:
    stocked(space)
    order = manufacturing.raise_work_order(
        space,
        wo_id="WO-0001",
        bom_id="BOM-SUB-1",
        quantity=10,
        at=flows.T["wo_sub"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    manufacturing.complete_work_order(space, wo_id=order["id"], at=flows.T["wo_sub"])

    assert space.stock_qty(flows.RAW_WAREHOUSE, "RAW-A") == 20.0
    assert space.stock_qty(flows.RAW_WAREHOUSE, "RAW-B") == 50.0
    assert space.stock_qty(flows.FINISHED_WAREHOUSE, "SUB-1") == 10.0


def test_completion_posts_two_stock_entries_and_two_ledger_rows(
    model: Model, space: Workspace
) -> None:
    stocked(space)
    order = manufacturing.raise_work_order(
        space,
        wo_id="WO-0001",
        bom_id="BOM-SUB-1",
        quantity=10,
        at=flows.T["wo_sub"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    before = len(space.of_kind("gl-posting"))
    manufacturing.complete_work_order(space, wo_id=order["id"], at=flows.T["wo_sub"])

    purposes = sorted(entry["purpose"] for entry in space.of_kind("stock-entry"))
    assert purposes == ["manufacture", "material_issue", "material_receipt"]
    assert len(space.of_kind("gl-posting")) == before + 2
    assert space.gl_imbalance() == 0.0
    # Work In Process is a way-station: the issue fills it and the manufacture
    # empties it, so a completed order leaves nothing parked there.
    assert "WORK-IN-PROCESS" not in space.gl_balance()


def test_the_produced_quantity_is_written_on_completion(
    model: Model, space: Workspace
) -> None:
    stocked(space)
    order = manufacturing.raise_work_order(
        space,
        wo_id="WO-0001",
        bom_id="BOM-SUB-1",
        quantity=10,
        at=flows.T["wo_sub"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    completed = manufacturing.complete_work_order(
        space, wo_id=order["id"], at=flows.T["wo_sub"]
    )
    assert completed["state"] == "completed"
    assert completed["produced_quantity"] == 10


def test_completing_a_work_order_twice_is_refused(model: Model, space: Workspace) -> None:
    """Acceptance criterion 3, and the reason it matters: the second try moves nothing."""
    stocked(space)
    order = manufacturing.raise_work_order(
        space,
        wo_id="WO-0001",
        bom_id="BOM-SUB-1",
        quantity=10,
        at=flows.T["wo_sub"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    manufacturing.complete_work_order(space, wo_id=order["id"], at=flows.T["wo_sub"])
    stock = {warehouse: dict(bucket) for warehouse, bucket in space.stock.items()}
    entries = len(space.of_kind("stock-entry"))

    assert_refused(
        lambda: manufacturing.complete_work_order(
            space, wo_id=order["id"], at=flows.T["wo_sub"]
        ),
        "work-order-already-completed",
        needle="WO-0001",
    )
    assert space.stock == stock
    assert len(space.of_kind("stock-entry")) == entries


def test_an_unstarted_work_order_does_not_complete(model: Model, space: Workspace) -> None:
    stocked(space)
    space.create(work_order(id="WO-DRAFT", state="draft", docstatus=0), at=flows.T["wo_sub"], actor="test")
    assert_refused(
        lambda: manufacturing.complete_work_order(
            space, wo_id="WO-DRAFT", at=flows.T["wo_sub"]
        ),
        "work-order-not-submitted",
        needle="WO-DRAFT",
    )


def test_an_unknown_work_order_is_refused(model: Model, space: Workspace) -> None:
    stocked(space)
    assert_refused(
        lambda: manufacturing.complete_work_order(
            space, wo_id="WO-NOPE", at=flows.T["wo_sub"]
        ),
        "unknown-work-order",
        needle="WO-NOPE",
    )


def test_a_work_order_cannot_consume_stock_that_is_not_there(
    model: Model, space: Workspace
) -> None:
    stocked(space)
    order = manufacturing.raise_work_order(
        space,
        wo_id="WO-0001",
        bom_id="BOM-SUB-1",
        quantity=1000,
        at=flows.T["wo_sub"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    stock = {warehouse: dict(bucket) for warehouse, bucket in space.stock.items()}
    assert_refused(
        lambda: manufacturing.complete_work_order(
            space, wo_id=order["id"], at=flows.T["wo_sub"]
        ),
        "insufficient-stock",
        needle="RAW-A",
    )
    assert space.stock == stock


def test_an_order_that_produces_another_item_is_refused(
    model: Model, space: Workspace
) -> None:
    stocked(space)
    space.create(work_order(id="WO-MISMATCH", item="FG-1"), at=flows.T["wo_sub"], actor="test")
    assert_refused(
        lambda: manufacturing.complete_work_order(
            space, wo_id="WO-MISMATCH", at=flows.T["wo_sub"]
        ),
        "bom-item-mismatch",
        needle="FG-1",
    )


def test_a_bill_that_contains_its_own_item_is_refused(
    model: Model, space: Workspace
) -> None:
    stocked(space)
    manufacturing.define_bom(
        space,
        bom_id="BOM-CYCLE",
        item="SUB-1",
        quantity=1,
        components=[{"item_code": "SUB-1", "qty": 1}],
        at=flows.T["bom"],
    )
    manufacturing.activate_bom(space, bom_id="BOM-CYCLE", at=flows.T["bom"])
    assert_refused(
        lambda: manufacturing.explode(space, space.get("BOM-CYCLE"), quantity=1),
        "bom-cycle",
        needle="SUB-1",
    )


def test_a_draft_bill_is_not_exploded(model: Model, space: Workspace) -> None:
    stocked(space)
    manufacturing.define_bom(
        space,
        bom_id="BOM-DRAFT",
        item="SUB-1",
        quantity=1,
        components=[{"item_code": "RAW-A", "qty": 1}],
        at=flows.T["bom"],
    )
    assert_refused(
        lambda: manufacturing.explode(space, space.get("BOM-DRAFT"), quantity=1),
        "bom-not-submitted",
        needle="BOM-DRAFT",
    )


def test_a_work_order_cannot_cite_a_missing_bill(model: Model, space: Workspace) -> None:
    stocked(space)
    assert_refused(
        lambda: manufacturing.raise_work_order(
            space,
            wo_id="WO-0001",
            bom_id="BOM-NOPE",
            quantity=1,
            at=flows.T["wo_sub"],
            from_warehouse=flows.RAW_WAREHOUSE,
            to_warehouse=flows.FINISHED_WAREHOUSE,
        ),
        "unknown-bom",
        needle="BOM-NOPE",
    )


def test_a_plan_raises_work_orders_and_closes(model: Model, space: Workspace) -> None:
    stocked(space)
    plan = manufacturing.plan_production(
        space,
        plan_id="PP-0001",
        entries=[{"item": "SUB-1", "quantity": 10}],
        at=flows.T["plan"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    assert space.stock_qty(flows.FINISHED_WAREHOUSE, "SUB-1") == 0.0
    orders = manufacturing.run_plan(space, plan_id=plan["id"], at=flows.T["plan"])
    assert len(orders) == 1
    assert orders[0]["bom"] == "BOM-SUB-1"
    assert orders[0]["state"] == "submitted"
    assert space.get(plan["id"])["state"] == "completed"


def test_a_plan_with_no_active_bill_is_refused(model: Model, space: Workspace) -> None:
    stocked(space)
    plan = manufacturing.plan_production(
        space,
        plan_id="PP-0001",
        entries=[{"item": "FG-SERVICE", "quantity": 1}],
        at=flows.T["plan"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    assert_refused(
        lambda: manufacturing.run_plan(space, plan_id=plan["id"], at=flows.T["plan"]),
        "unknown-bom",
        needle="FG-SERVICE",
    )


def test_an_unknown_plan_is_refused(model: Model, space: Workspace) -> None:
    stocked(space)
    assert_refused(
        lambda: manufacturing.run_plan(space, plan_id="PP-NOPE", at=flows.T["plan"]),
        "unknown-document",
        needle="PP-NOPE",
    )


def test_a_refused_completion_leaves_the_rail_shorter_than_a_real_one(
    model: Model, space: Workspace
) -> None:
    """A refusal writes nothing, so the rail's head does not move."""
    stocked(space)
    order = manufacturing.raise_work_order(
        space,
        wo_id="WO-0001",
        bom_id="BOM-SUB-1",
        quantity=10,
        at=flows.T["wo_sub"],
        from_warehouse=flows.RAW_WAREHOUSE,
        to_warehouse=flows.FINISHED_WAREHOUSE,
    )
    before = space.rail.head()
    with pytest.raises(Refused):
        manufacturing.complete_work_order(space, wo_id="WO-NOPE", at=flows.T["wo_sub"])
    assert space.rail.head() == before

    manufacturing.complete_work_order(space, wo_id=order["id"], at=flows.T["wo_sub"])
    assert space.rail.head() != before
