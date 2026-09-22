"""ERP FinOps: metering and usage telemetry for ERP operations (issue #654).

ERP-09 of the ERP module epic (#645). The lane makes the module **billable, not
free**: every document creation and every state transition is metered, the usage
is rolled up per tenant with a cost, and a tenant at its budget is stopped by
name before anything is written.

The three acceptance criteria of the issue, and where each one lives:

1. *every document create/transition emits a ledger record* —
   :mod:`.meter` is the only path that writes, and it writes the record through
   the platform ledger's public API (:mod:`.ledger`).
2. *a roll-up produces per-tenant usage + cost, and a tenant at budget gets a
   deterministic hard stop, proven by a negative control* — :mod:`.rollup` for
   the figures, :mod:`.budget` for the stop, :mod:`.negative_control` for the
   proof.
3. *no telemetry file is edited* — this lane consumes ``telemetry/ledger``,
   ``telemetry/metering`` and ``telemetry/budgets`` through their public APIs
   only, and the lane's check measures the content digest of every tracked file
   under ``telemetry/`` before and after a full metered run.

Public surface
--------------

- :mod:`.model` — the closed vocabulary: operations, event names, refusal codes.
- :mod:`.schema` — the stdlib JSON-Schema subset validator and its keyword freeze.
- :mod:`.rates` — the price list, and the coverage rule that keeps it complete.
- :mod:`.ledger` — the audit sink over ``telemetry/ledger``.
- :mod:`.usage` — the usage sink over ``telemetry/metering``.
- :mod:`.budget` — the pre-operation budget hook over ``telemetry/budgets``.
- :mod:`.rollup` — per-tenant ERP usage and cost, with the NO-DATA rule.
- :mod:`.provenance` — the GR-10 harvest record and its enforcement.
- :mod:`.meter` — the one path by which an ERP operation is metered.
- :mod:`.harness` — the deterministic workspace and the surface enumeration.

``python3 -m integrations.erp.finops.cli check`` is the lane's own tri-state
check; ``bash scripts/check-erp-finops.sh`` is the gate that names it.

---knowledge---
module_id: integrations.erp.finops.__init__
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

from integrations.erp.finops import (  # noqa: F401
    budget,
    harness,
    ledger,
    meter,
    model,
    provenance,
    rates,
    rollup,
    schema,
    usage,
)

__all__ = [
    "budget",
    "harness",
    "ledger",
    "meter",
    "model",
    "provenance",
    "rates",
    "rollup",
    "schema",
    "usage",
]
