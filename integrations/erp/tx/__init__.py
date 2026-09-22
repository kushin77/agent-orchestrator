"""The ERP transactional spine — selling → stock → accounting (ERP-03, #648).

ERP-03 of the ERP module epic (#645): the closed loop the epic calls its spine.
A quotation becomes a sales order, the order is fulfilled by a delivery note,
the delivery commits stock, the invoice derived from that delivery commits the
general ledger — and every one of those steps can be cancelled, with the
cancellation reversing exactly what the step applied.

The package is a thin front door over six modules:

* :mod:`.indexer` — the indexer query seam: which documents this lane owns;
* :mod:`.definitions` — the resolved definition set. The cycle, the stock
  families and the ledger family are *derived* from the ERP-02 schemas and the
  indexer's declarations, never restated (acceptance criterion 3);
* :mod:`.model` — the document envelope, the closed action and refusal
  vocabularies, and the refusal type;
* :mod:`.audit` — the append-only, hash-chained rail every spine step lands on;
* :mod:`.stock` — the stock ledger, and its reversal;
* :mod:`.ledger` — the general ledger derived from an invoice, and its reversal;
* :mod:`.spine` — the workspace and the end-to-end flows, including
  :func:`~.spine.golden_path`.

Nothing here is tenant-visible and nothing here runs on its own: the module ships
the loop, the REST surface is ERP-06 (#651), the portal mount is ERP-07 (#652)
and the feature flag that gates the whole module is ``erp-module``, declared
``off`` in ``integrations/erp/module.yaml`` (GR-5).

---knowledge---
module_id: integrations.erp.tx.__init__
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

from . import audit, definitions, indexer, ledger, model, spine, stock  # noqa: F401

__all__ = ["audit", "definitions", "indexer", "ledger", "model", "spine", "stock"]
