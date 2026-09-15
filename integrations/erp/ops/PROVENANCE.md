# Harvest provenance — ERP-04 procurement and manufacturing (GR-10)

Upstream `frappe/erpnext` is **GPL-3.0**. It is a **pattern source only**: no
upstream code, schema, text or file is copied into this repository, no upstream
repository is vendored, and `.research/` — where a reading clone would live — is
gitignored. What was harvested is the *shape* of each document family, recorded
per schema in [`provenance.json`](provenance.json) and enforced by
[`provenance.py`](provenance.py).

## What was harvested, and what was built instead

| Declared family | Upstream doctype | Harvested as a pattern | Built instead |
|---|---|---|---|
| `rfq` | Request for Quotation | the field surface (invited suppliers, item lines, validity) and the idea that price discovery precedes commitment | our own schema and lifecycle data, with `rate` deliberately **optional** on a line and required at conversion |
| `purchase-invoice` | Purchase Invoice | the field surface (supplier, order and receipt back-references, posting date, taxes, totals) and invoice-after-goods | our own schema and lifecycle data, with both back-references **required** |
| `subcontracting-note` | Subcontracting Order | material issued to a subcontractor against a purchase order | our own schema, with the source warehouse required and the material still ours while out |
| `bom` | BOM | the field surface (produced item, output quantity, component lines) and a bill as data an explosion walks | our own schema and lifecycle data, with `quantity` strictly positive so an explosion always scales |
| `work-order` | Work Order | the field surface (bom link, produced item, quantity, source and target warehouse) and completion as the point where components are consumed | our own schema, **citing** the bill rather than copying its lines, and `complete` terminal |
| `production-plan` | Production Plan | the field surface (planned entries of item and quantity) and a plan as intent | our own schema, naming an item rather than a BOM id so a plan pins no revision it did not choose |

Nothing else in this lane is harvested. The stock and ledger families are
**consumed** from `integrations/erp/core` (ERP-02, issue #647), which records its
own harvest; the posting rules in [`catalog/ops-catalog.json`](catalog/ops-catalog.json)
are this lane's own double-entry decisions, not an upstream table.

## What the enforcement refuses

`provenance.enforce_harvest` refuses a per-schema record that is empty, that
names no upstream doctype, or whose harvest list is not a list of sentences —
so "no harvest record" can never be the state that passes.

`provenance.enforce_catalogue` refuses a lane-level record that claims copied
code (`code-copied`), that omits the upstream or its licence, or whose licence
does not record the GPL the harvest is bounded by. Both are provoked in
[`negative_control.py`](negative_control.py), and both are measured by
`scripts/check-erp-ops.sh` through the lane's own `cli.py check`.
