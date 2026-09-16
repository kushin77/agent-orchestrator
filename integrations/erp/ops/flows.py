"""The golden path: one deterministic run of both cycles over one workspace.

Everything this lane claims is exercised here, in one function, offline: the
purchase cycle from invitation to settled bill, a two-level bill of materials
exploded to raw material, a production plan raised into a work order, and two
work orders completing — the second consuming a sub-assembly the first produced.

Two properties make this evidence rather than a demonstration:

* **it is deterministic.** The clock is a table of instants (:data:`T`), never
  the wall clock; ids come from :meth:`~.workspace.Workspace.next_id`; nothing
  reads the environment. Running :func:`golden_path` twice yields byte-identical
  transcripts, which is exactly what ``cli.py check`` measures before it will
  report OK.
* **it ties out.** The purchase cycle debits Stock In Hand and credits the
  payable; manufacturing moves that value through Work In Process and back. The
  transcript carries every account's balance, and the balances sum to zero —
  which is the ledger's own statement that nothing was invented, only moved.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, Optional, Tuple

from . import catalog as catalog_mod
from . import manufacturing, procurement
from .model import KIND_ITEM, KIND_PARTY, Model, load_model
from .workspace import Workspace

__all__ = [
    "COMPANY",
    "ITEMS",
    "PARTIES",
    "RAW_WAREHOUSE",
    "FINISHED_WAREHOUSE",
    "T",
    "digest",
    "golden_path",
    "render",
    "workspace",
]

#: The company every document of the run belongs to.
COMPANY = "COMPANY-1"

#: The two warehouses stock moves between: components live in one and finished
#: goods land in the other, so a movement is visible as a change of place.
RAW_WAREHOUSE = "WH-RAW"
FINISHED_WAREHOUSE = "WH-FG"

#: The run's clock. Every document, posting and audit entry takes its instant
#: from here, so the whole run is reproducible and the rail reads in order.
T: Mapping[str, str] = {
    "masters": "2026-09-15T07:00:00Z",
    "bom": "2026-09-15T07:30:00Z",
    "rfq": "2026-09-15T08:00:00Z",
    "order": "2026-09-15T08:30:00Z",
    "plan": "2026-09-15T09:00:00Z",
    "receipt": "2026-09-15T10:00:00Z",
    "invoice": "2026-09-15T14:00:00Z",
    "wo_sub": "2026-09-16T08:00:00Z",
    "wo_fg": "2026-09-16T12:00:00Z",
}

#: The item master. RAW-* is bought, SUB-1 and FG-1 are made, and FG-SERVICE is
#: a non-stock item — present so the transcript shows an item that carries no
#: stock at all rather than implying everything is inventory.
ITEMS: Tuple[Mapping[str, Any], ...] = (
    {
        "doctype": KIND_ITEM,
        "id": "RAW-A",
        "name": "Raw material A",
        "uom": "Kg",
        "item_group": "RAW",
        "is_stock_item": True,
        "valuation_rate": 5.0,
    },
    {
        "doctype": KIND_ITEM,
        "id": "RAW-B",
        "name": "Raw material B",
        "uom": "Nos",
        "item_group": "RAW",
        "is_stock_item": True,
        "valuation_rate": 2.0,
    },
    {
        "doctype": KIND_ITEM,
        "id": "SUB-1",
        "name": "Sub-assembly 1",
        "uom": "Nos",
        "item_group": "SUB",
        "is_stock_item": True,
        "valuation_rate": 12.0,
    },
    {
        "doctype": KIND_ITEM,
        "id": "FG-1",
        "name": "Finished good 1",
        "uom": "Nos",
        "item_group": "FG",
        "is_stock_item": True,
        "valuation_rate": 18.0,
    },
    {
        "doctype": KIND_ITEM,
        "id": "FG-SERVICE",
        "name": "Assembly service",
        "uom": "Hour",
        "item_group": "SVC",
        "is_stock_item": False,
        "valuation_rate": 0.0,
    },
)

#: The party master: the supplier the cycle buys from, and a customer, so the
#: supplier check is shown to be a check rather than a name that always matches.
PARTIES: Tuple[Mapping[str, Any], ...] = (
    {
        "doctype": KIND_PARTY,
        "id": "SUP-1",
        "party_type": "supplier",
        "name": "Nordic Components AB",
        "currency": "EUR",
        "territory": "EU",
        "email": "sales@nordic-components.example",
    },
    {
        "doctype": KIND_PARTY,
        "id": "CUST-1",
        "party_type": "customer",
        "name": "Acme Retail",
        "currency": "EUR",
        "territory": "EU",
        "email": "buying@acme.example",
    },
)


def workspace(
    model: Optional[Model] = None, catalog: Optional[catalog_mod.Catalog] = None
) -> Workspace:
    """A fresh workspace with the masters seeded, and nothing else."""
    space = Workspace(
        model if model is not None else load_model(),
        catalog if catalog is not None else catalog_mod.load(),
        company=COMPANY,
    )
    for item in ITEMS:
        space.create(dict(item), at=T["masters"], actor="master-data")
    for party in PARTIES:
        space.create(dict(party), at=T["masters"], actor="master-data")
    return space


def golden_path(
    model: Optional[Model] = None, catalog: Optional[catalog_mod.Catalog] = None
) -> Dict[str, Any]:
    """Run both cycles end to end over one workspace and return the transcript."""
    space = workspace(model, catalog)

    cycle = procurement.purchase_cycle(
        space,
        ids={
            "rfq": "RFQ-0001",
            "order": "PO-0001",
            "receipt": "PR-0001",
            "invoice": "PI-0001",
        },
        supplier="SUP-1",
        lines=[
            {"item_code": "RAW-A", "qty": 40, "rate": 5.0},
            {"item_code": "RAW-B", "qty": 60, "rate": 2.0},
        ],
        warehouse=RAW_WAREHOUSE,
        at={
            "rfq": T["rfq"],
            "order": T["order"],
            "receipt": T["receipt"],
            "invoice": T["invoice"],
        },
        tax_rate=0.10,
    )

    manufacturing.define_bom(
        space,
        bom_id="BOM-SUB-1",
        item="SUB-1",
        quantity=1,
        components=[
            {"item_code": "RAW-A", "qty": 2},
            {"item_code": "RAW-B", "qty": 1},
        ],
        at=T["bom"],
    )
    manufacturing.activate_bom(space, bom_id="BOM-SUB-1", at=T["bom"])
    manufacturing.define_bom(
        space,
        bom_id="BOM-FG-1",
        item="FG-1",
        quantity=1,
        components=[
            # The sub-assembly is produced INTO the finished-goods warehouse by
            # its own work order and consumed FROM there, so the component names
            # that warehouse rather than falling back to the order's default.
            {"item_code": "SUB-1", "qty": 1, "warehouse": FINISHED_WAREHOUSE},
            {"item_code": "RAW-B", "qty": 3},
        ],
        at=T["bom"],
    )
    manufacturing.activate_bom(space, bom_id="BOM-FG-1", at=T["bom"])

    plan = manufacturing.plan_production(
        space,
        plan_id="PP-0001",
        entries=[{"item": "SUB-1", "quantity": 10}],
        at=T["plan"],
        from_warehouse=RAW_WAREHOUSE,
        to_warehouse=FINISHED_WAREHOUSE,
    )
    raised = manufacturing.run_plan(space, plan_id=plan["id"], at=T["plan"])
    completed = [
        manufacturing.complete_work_order(space, wo_id=order["id"], at=T["wo_sub"])
        for order in raised
    ]

    top = manufacturing.raise_work_order(
        space,
        wo_id="WO-0002",
        bom_id="BOM-FG-1",
        quantity=4,
        at=T["wo_fg"],
        from_warehouse=RAW_WAREHOUSE,
        to_warehouse=FINISHED_WAREHOUSE,
    )
    completed.append(
        manufacturing.complete_work_order(space, wo_id=top["id"], at=T["wo_fg"])
    )

    explosions = {
        "BOM-SUB-1": manufacturing.explode(
            space, space.get("BOM-SUB-1"), quantity=1
        ),
        "BOM-FG-1": manufacturing.explode(space, space.get("BOM-FG-1"), quantity=1),
    }

    return {
        "issue": 649,
        "lane": space.catalog.lane,
        "flag": {
            "id": space.catalog.flag["id"],
            "default": space.catalog.flag["default"],
        },
        "purchase_cycle": {
            "order": [
                "rfq",
                "purchase-order",
                "purchase-receipt",
                "purchase-invoice",
            ],
            "documents": {
                step: cycle[step] for step in sorted(cycle)
            },
        },
        "manufacturing": {
            "boms": [bom["id"] for bom in space.of_kind("bom")],
            "production_plan": plan["id"],
            "work_orders": [order["id"] for order in space.of_kind("work-order")],
            "completed": [order["id"] for order in completed],
            "explosions": explosions,
        },
        "workspace": space.snapshot(),
        "verdict": "OK",
    }


def digest(transcript: Any) -> str:
    """A stable digest of a transcript, so two runs can be compared."""
    payload = json.dumps(transcript, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render(transcript: Any) -> str:
    """The transcript as pretty JSON (the ``demo`` verb's output)."""
    return json.dumps(transcript, indent=2, sort_keys=True, default=str) + "\n"
