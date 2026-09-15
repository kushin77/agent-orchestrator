# `integrations/erp/tx/` — the transactional spine

The ERP module's **transactional-spine lane** (issue
[#648](https://github.com/kushin77/agent-orchestrator/issues/648), EPIC
[#645](https://github.com/kushin77/agent-orchestrator/issues/645)): **selling →
stock → accounting** as one closed loop. A quotation becomes a sales order, the
order is fulfilled by a delivery note, the delivery moves stock, the invoice
derived from that delivery commits the general ledger — and every step can be
cancelled, with the cancellation reversing exactly what the step applied.

The feature-by-feature contract this lane builds against is
[`docs/ERP-MODULE-GAP-ANALYSIS.md`](../../../docs/ERP-MODULE-GAP-ANALYSIS.md); the
document model and the workflow data it consumes are ERP-02's
([`integrations/erp/core/`](../core/)), and the declarations that own this lane's
documents are ERP-01's ([`integrations/erp/catalog/`](../catalog/)).

## What resolves, and from where

This lane declares **no ERP fact**. The acceptance criterion is that "every
definition resolves through the indexer query surface or ERP-02 schemas", and each
of these is a *derivation* rather than a list:

| The spine needs | Where it comes from |
|---|---|
| which documents this lane owns | the indexer: catalogue declarations whose `owning_issue` is this lane's, read through `governance/knowledge/catalog.json` |
| the cycle's order and links | the ERP-02 schemas: a family is *raised against* the family its link field names (`sales-order.quotation`, `delivery-note.against_sales_order`, `sales-invoice.against_delivery_note`) |
| states, `docstatus`, transitions | the ERP-02 workflows, through `DocumentModel` |
| which family commits stock | the cycle families whose schema declares a warehouse-bearing field |
| which family commits the ledger | the one lifecycle family whose schema declares a `voucher_type` |
| the submit / complete / cancel **actions** | the workflow: the action is found by *the state it reaches*, never by its name |
| document field shapes | the ERP-02 schemas, which validate every document the spine creates |
| posting roles, refusals, audit actions | this lane's own closed vocabularies — the one thing no upstream has an opinion about |

The derived cycle for this lane is therefore
`quotation → sales-order → delivery-note → sales-invoice`, plus the ledger family
`gl-posting` derived from the invoice. `python3 -m integrations.erp.tx.cli
definitions` prints the whole resolution.

## The flows

| Flow | What it does | What it refuses, by name |
|---|---|---|
| `draft` | creates a document in its workflow's initial state, validated by ERP-02 | `duplicate-id`, `invalid-value` (a scenario may not set `state`/`docstatus`), `schema-violation` |
| `submit` | moves a draft to its submitted state and applies the effects: stock for the delivery, the ledger for the invoice | `already-submitted` (a document submits once), `cancelled-document`, `illegal-transition` |
| `raise_document` | creates a document raised against the family its schema links it to | `wrong-document`, `not-submitted`, `cancelled-document`, `currency-mismatch` |
| `deliver` | fulfils an order and moves stock out of the line's warehouse | `over-delivery` (names the item and both figures), `unknown-item`, `not-a-stock-item`, `unknown-warehouse` |
| `invoice` | bills a delivery and posts the double entry | `over-invoice`, `unbalanced-posting`, `unknown-account-role` |
| `complete` | settles a submitted document (accepted, paid) | `illegal-transition` |
| `cancel` | reverses the document's exact effects, then cancels it | `live-dependant`, `already-reversed` |

## The two properties the lane is built on

1. **The loop is a function of its inputs.** The clock is *injected*
   (`spine.TIMELINE`), the audit rail digests only supplied values, every id comes
   from the scenario (nothing is keyed by time, counter or random source), and the
   rail is append-only by construction (`Rail.append` returns a *new* rail) and
   tamper-evident (`Rail.verify` re-derives every digest and back-link). Two runs
   of the golden path therefore produce the same audit head and the same balances,
   which `cli check` asserts rather than assumes.
2. **Cancellation reverses, it does not roll back.** A cancellation posts the
   exact inverse of the document's stock movements and ledger entries and then
   cancels any posting derived from it. The measurement that matters is *not* "the
   net is zero" — under a total inversion that is true by construction, so
   asserting it would be a check that cannot fail. It is instead that both ledgers
   return to their **pre-cycle** balances, on the very items, warehouses and
   accounts the reversal had to name, **while still carrying the entries**: a
   reversal that rolled the ledgers back would pass a balance check and fail this
   one.

## Boundary (what this lane does and does not drive)

Three of the documents the indexer declares for this lane are **not driven by this
spine**, and the reason is reported rather than hidden (it is printed by `cli
check` and returned by `definitions.to_dict()["undriven"]`):

* `journal-entry` and `payment-entry` — ERP-02 ships no lifecycle for them, so
  there is no declared way to move them. Inventing a schema or a workflow would
  mean writing into ERP-02's lane (`integrations/erp/core/**`), which this lane
  does not own.
* `stock-entry` — ERP-02 *does* declare its lifecycle, but it stands outside the
  derived selling cycle (no schema link connects it), and its purpose-driven
  warehouse set does not declare a direction of effect. The spine will not guess
  one.

Two further hand-offs are named rather than assumed: the **REST surface** is
ERP-06 ([#651](https://github.com/kushin77/agent-orchestrator/issues/651)), the
**portal mount** is ERP-07 ([#652](https://github.com/kushin77/agent-orchestrator/issues/652)),
and the feature flag that gates the module is `erp-module`, declared `off` in
[`integrations/erp/module.yaml`](../module.yaml) (GR-5). This lane ships no
runtime surface of its own.

## Harvest provenance (GR-10)

Upstream ERPNext is **GPL-3.0** and is a **pattern source only**: no upstream
code, schema or text is copied or vendored. The harvested shapes — the document
cycle, the posting derivation and the docstatus discipline — and what was built
instead are recorded in [`PROVENANCE.md`](PROVENANCE.md).

## Running it

```bash
python3 -m pytest integrations/erp/tx/tests -q          # the suite
bash scripts/check-erp-tx.sh                            # the gate (suite + check + mutant)
python3 -m integrations.erp.tx.cli check                # resolution, golden path, controls
python3 -m integrations.erp.tx.cli demo                 # both transcripts, as JSON
python3 -m integrations.erp.tx.cli definitions          # the validated resolution
python3 integrations/erp/tx/negative_control.py         # provoke every refusal
```

The CLI follows the repository's tri-state contract: `0` OK, `1` NOT-OK,
`2` CANNOT-ASSESS (an indexer or ERP-02 asset that will not load is never a pass).

## Layout

| Path | Role |
|---|---|
| [`model.py`](model.py) | the document handle, the closed action and refusal vocabularies, the posting roles |
| [`indexer.py`](indexer.py) | the indexer seam: which documents this lane owns |
| [`definitions.py`](definitions.py) | the derivations: cycle, links, stock family, ledger family, actions |
| [`audit.py`](audit.py) | the append-only, hash-chained rail |
| [`stock.py`](stock.py) | the stock ledger, its movements and their reversal |
| [`ledger.py`](ledger.py) | the general ledger, the posting policy and the reversal |
| [`spine.py`](spine.py) | the workspace, the flows, and the two golden paths |
| [`cli.py`](cli.py) | `check` / `demo` / `definitions`, tri-state |
| [`negative_control.py`](negative_control.py) | one provocation per refusal, coverage computed |
| [`tests/`](tests) | the suite, organised one module per acceptance criterion |
