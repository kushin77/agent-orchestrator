# Provenance — `integrations/erp/tx/` (ERP-03, issue #648)

GR-10 requires every harvested asset to record its source. This lane harvests
**shapes and semantics only** and copies **nothing**.

## The upstream

| Field | Value |
|---|---|
| Repository | [`frappe/erpnext`](https://github.com/frappe/erpnext) |
| Licence | GPL-3.0 |
| Mode | **pattern-only** — no upstream code, schema, text or file is copied, translated or vendored |
| Retrieved | 2026-09-14 (public documentation; no upstream clone was made) |
| Verification | ERP-02's own schema set carries the same declaration (`integrations/erp/core/provenance.json`), and the ERP catalogue documents carry `code_copied: false`, which `indexer.read_lane_document` refuses to accept otherwise |

## What was harvested, and what was built instead

| Harvested shape | Upstream origin | What this lane built instead |
|---|---|---|
| The selling cycle as one document chain — quotation → order → delivery → invoice | ERPNext's selling module and its `against_sales_order` / `against_delivery_note` links | A **derivation over ERP-02's schemas**: the chain is recovered by walking the link fields the schemas already declare, so this lane holds no copy of the cycle and follows an upstream change to it automatically |
| Stock movement applied on delivery | ERPNext's stock ledger entries written on Delivery Note submit | A stock ledger of signed movements, valued at the item's `valuation_rate` (the field ERP-02's `item.schema.json` names as "what a stock ledger entry debits"), and gated on `is_stock_item` |
| GL posting raised from an invoice, with the voucher back-reference | ERPNext's GL Entry `voucher_type` / `voucher_id` | A derivation that checks the voucher against ERP-02's closed `voucher_type` enum before posting, so a family the model cannot resolve is refused rather than posted |
| Double entry — debits equal credits | ERPNext's GL Entry validation | Rows proven to balance at derivation **and** re-checked by ERP-02's own `gl-posting` family rule, which refuses an unbalanced posting by name |
| Cancellation as a reversal rather than a deletion | ERPNext's cancel-and-repost behaviour | A reversal that posts the exact inverse and cancels the derived posting, measured by both ledgers returning to their pre-cycle balances while *still carrying* the entries |

## The cannibalization this lane was asked to make

Issue #648 names three assets to cannibalize. All three were consumed as patterns,
none was edited:

* **`engine/core` durable-workflow patterns** — "consume, don't edit". The spine
  consumes workflow *data*; it imports nothing from `engine/` and edits nothing
  there. The state machine it walks is ERP-02's.
* **`integrations/paperclip/` adapter shapes** — the module-assembly precedent
  (a package with its own README, tests, CLI and a gate) is followed; no Paperclip
  code is imported.
* **ERPNext posting logic as semantics** — the double-entry semantics above.

## What is *not* copied, and how that is enforced

* `integrations/erp/core/**` (ERP-02's schemas and workflows) is **read** through
  its public validators and never edited — it is another lane's files.
* A catalogue declaration claiming `code_copied: true` is refused by
  `indexer.read_lane_document` with `catalogue-invalid`, naming GR-10.
* No upstream repository is vendored and no upstream file is committed; any
  reading clone lives under `.research/`, which is gitignored.
