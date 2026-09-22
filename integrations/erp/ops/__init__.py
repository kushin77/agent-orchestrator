"""ERP-04 of the ERP module: procurement and manufacturing (issue #649).

The ERP module's **supplier-side and production** lane, under
``integrations/erp/ops/``: the buying cycle (RFQ, purchase order, receipt,
purchase invoice, subcontracting note) and the production cycle (bill of
materials, work order, production plan), over the core document model ERP-02
landed.

Read ``README.md`` for the surface, the flows and the lane boundary. In short:

* this lane **consumes** ``integrations/erp/core`` — its buying and stock
  families (``purchase-order``, ``purchase-receipt``, ``stock-entry``,
  ``gl-posting``) and its two masters are validated by ERP-02's own model, and
  the shared field definitions this lane's schemas compose from are ``$ref``'d
  out of ERP-02's ``document.schema.json`` rather than restated;
* this lane **declares** what ERP-02 does not ship: ``rfq``,
  ``purchase-invoice``, ``subcontracting-note``, ``bom``, ``work-order`` and
  ``production-plan``, each as a JSON-Schema family with its lifecycle as data;
* every refusal is provoked by ``negative_control.py``, which fails when the
  provoked set and the closed vocabulary diverge.

The flag is ``erp-module``, declared OFF (GR-5): this lane ships documents,
flows and postings, and no tenant-visible surface of its own.

---knowledge---
module_id: integrations.erp.ops.__init__
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from . import audit, catalog, documents, flows, ledger, manufacturing, model, procurement, provenance, workspace, workflow  # noqa: F401
from .audit import Entry, Rail  # noqa: F401
from .catalog import Catalog, Kind, Posting  # noqa: F401
from .model import (  # noqa: F401
    ACTIONS,
    CORE_KINDS,
    OPS_KINDS,
    PURCHASE_CYCLE,
    REFUSALS,
    Model,
    Refused,
    load_model,
)
from .workspace import Workspace  # noqa: F401

__all__ = [
    "ACTIONS",
    "CORE_KINDS",
    "Catalog",
    "Entry",
    "Kind",
    "Model",
    "OPS_KINDS",
    "PURCHASE_CYCLE",
    "Posting",
    "REFUSALS",
    "Rail",
    "Refused",
    "Workspace",
    "audit",
    "catalog",
    "documents",
    "flows",
    "ledger",
    "load_model",
    "manufacturing",
    "model",
    "procurement",
    "provenance",
    "workspace",
    "workflow",
]
