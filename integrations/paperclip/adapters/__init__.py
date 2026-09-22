"""The thin upstream-family adapters (EPIC #410), under the canonical module.

Each ``adapters/<family>/`` package derives an upstream resource shape from the
fleet's own authoritative surfaces. It never writes a ledger, never persists a
projection, and never grants a privilege the fleet did not grant. Every adapter
obeys the EPIC's one rule: **projection, not authority** — a fleet ledger stays
the writer and the adapter derives and maps onto the frozen ticket contract
(#400, ADR-0014). No adapter introduces a second store.

One module per seam contract (fleet -> upstream). Families: ``approvals``,
``heartbeat``, ``budget``, ``secrets``, ``routines``, ``skills``.

These were ``paperclip/adapters/**`` until issue #457 (ADR-0016) consolidated
the parity tree under the canonical module ``integrations/paperclip/``. The
**budget** family's rail lives at ``integrations/paperclip/budget.py``.
This subpackage is layered **over** the seam (``client``, ``mapping``,
``model``): ``adapters/**`` may import the seam, and the seam must never import
``adapters/**``.

---knowledge---
module_id: integrations.paperclip.adapters.__init__
system: integrations
app: paperclip
solution_class: pattern
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""
