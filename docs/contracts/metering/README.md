# The metering/budget boundary contract (frozen)

The frozen contract for the one metering/budget record shape that crosses the
repo boundary. It exists because the same shape is being built independently on
both sides — `kushin77/deepseek` (`#55`, `#71`, `#29`, `#30`, `#115`, `#106`) and
`kushin77/ollama#199` are a third producer — and our budget adapter
(`integrations/paperclip/budget.py`, issue #415) is about to ship a receipt shape
of its own. Freezing the shape **before** the adapter's receipt hardens is the
only way to avoid a third variant. The boundary rule this contract obeys is
[`CROSS-REPO-EXECUTION-BOUNDARY.md`](../../CROSS-REPO-EXECUTION-BOUNDARY.md)
(NG4): a peer's work is a **direction issue on the peer's board**, never an edit
here.

## The record shape

One record, four groups, one kind:

| Group | What it fixes | Real field names from |
|---|---|---|
| `identity` | which tenant / agent / **run** the row belongs to | `telemetry/metering/model.py` (`UsageRecord`) |
| `quantity` | the **measured quantity** — tokens, cost, currency — and the honesty of a null cost | `telemetry/metering/model.py`, `intake.py`, `ratecards.py` |
| `receipt` | the **receipt** that ties the row to a ticket, and the evidence that backs it | `telemetry/budgets/`, `integrations/paperclip/budget.py` (#415) |
| `attribution` | the provider/model/tier/outcome and the **budget scope** charged | `telemetry/metering/`, `gateway/finops/`, `telemetry/budgets/` |

Every field name here is the live vocabulary of `telemetry/metering/` and the
budget rail — never invented. The canonical shape is
[`metering-record.schema.json`](metering-record.schema.json) (closed:
`additionalProperties: false`), and two committed instances validate against it:
[`metering-record.example.json`](metering-record.example.json) (the fleet side)
and [`peer-export.example.json`](peer-export.example.json) (the peer export
mapped onto the same shape).

## Files

| File | What it freezes |
|---|---|
| [`metering-record.schema.json`](metering-record.schema.json) | the **record shape** — the four groups and `claims[]` |
| [`field-map.json`](field-map.json) | the **fleet ↔ peer** map: the fleet column and the peer column for every field, one-sided fields **named** |
| [`ownership.json`](ownership.json) | the **one-writer map** — the single producer of every field (the producer seam) |
| [`*.example.json`](metering-record.example.json) | instances the gate validates against the schema |

## The two rules the gate enforces

> **A field that exists on one side only is named, not silently dropped.**

`field-map.json` carries a row for every canonical field. A row names the fleet
column and the peer column it maps to; where a field is on one side only, the row
sets `one_sided` to the side that has it — the field is **named**, never dropped.
A canonical field with **no row** is an *unmapped field* and fails the gate.

> **Exactly one producer for each field.**

`ownership.json` names the single side that **emits** each field, and a `where`
for that side. A field with **no producer** is unowned (`kushin77/deepseek#115`
is precisely the "record shape exists but nothing produces it" seam); a field
with **two producers** is a second authority (half-coupling). Both are refused
**by name**.

## The producer seam — who emits each field

The recorded failure this closes is `kushin77/deepseek#115`: a shape that exists
but has no producer. Every canonical field therefore names the side that emits
it, so "nothing produces it" cannot recur silently on either side.

| Producer (side) | Where | Emits |
|---|---|---|
| `fleet-metering` | `telemetry/metering/model.py` (`UsageRecord`), `intake.py`, `ratecards.py` | the identity, measured-quantity and call-attribution fields |
| `fleet-run-identity` | `governance/isolation/identity.py` (minted session identity) | `identity.runId` |
| `fleet-budget` | `telemetry/budgets/`, `gateway/finops/` | the budget scope/period/cap/spend/hard-stop/burn-rate fields and `claims` |
| `fleet-paperclip-adapter` | `integrations/paperclip/budget.py` (#415) | the `receipt` fields that tie spend to a ticket |
| `peer-deepseek-export` | `kushin77/deepseek` (`#55`, `#71`, `#30`) | the peer export's own records, mapped onto this same shape |
| `peer-ollama-budget` | `kushin77/ollama` (`#199`) | the third producer's budget records, mapped onto this same shape |

The full per-field map:

| Field | Producer | Meaning |
|---|---|---|
| `schemaVersion` | `fleet-metering` | the record-shape version |
| `kind` | `fleet-metering` | the record kind (metering) |
| `identity.tenantId` | `fleet-metering` | the tenant the spend is attributed to |
| `identity.agentId` | `fleet-metering` | the agent whose call this is |
| `identity.runId` | `fleet-run-identity` | the run/session that emitted the call |
| `identity.recordId` | `fleet-metering` | the row's own id |
| `identity.sourceType` | `fleet-metering` | which sibling record shape the row was absorbed from |
| `identity.sourceKey` | `fleet-metering` | the source record's key within its own shape |
| `quantity.inputTokens` | `fleet-metering` | prompt/input tokens |
| `quantity.outputTokens` | `fleet-metering` | completion/output tokens |
| `quantity.totalTokens` | `fleet-metering` | input + output tokens |
| `quantity.costUsd` | `fleet-metering` | the resolved cost, or null when unmetered |
| `quantity.costSource` | `fleet-metering` | why the cost figure is trustworthy |
| `quantity.currency` | `fleet-metering` | the ISO-4217 currency of costUsd |
| `quantity.metered` | `fleet-metering` | whether a cost was resolved |
| `quantity.billable` | `fleet-metering` | whether the call is billable |
| `quantity.cacheHit` | `fleet-metering` | the explicit zero-cost cache-hit path |
| `quantity.unmeteredReason` | `fleet-metering` | why the row is unmetered |
| `receipt.ticket` | `fleet-paperclip-adapter` | the ticket the spend is tied to |
| `receipt.receipt` | `fleet-paperclip-adapter` | the per-task receipt id |
| `receipt.evidence` | `fleet-paperclip-adapter` | the evidence reference backing the receipt |
| `attribution.provider` | `fleet-metering` | the provider id |
| `attribution.model` | `fleet-metering` | the concrete model id |
| `attribution.route` | `fleet-metering` | the route taken |
| `attribution.tier` | `fleet-metering` | the effective model tier |
| `attribution.taskClass` | `fleet-metering` | the task class the chooser attributed |
| `attribution.outcome` | `fleet-metering` | the call outcome |
| `attribution.scope.level` | `fleet-budget` | the scoping level of the cap |
| `attribution.scope.id` | `fleet-budget` | the agent/team/project id the cap applies to |
| `attribution.period` | `fleet-budget` | the budget window |
| `attribution.cap` | `fleet-budget` | the spend limit for the period |
| `attribution.spent` | `fleet-budget` | spend consumed in the period |
| `attribution.hardStop` | `fleet-budget` | whether the cap is absolute |
| `attribution.burnRateAlertPct` | `fleet-budget` | the burn-rate alert threshold |
| `claims` | `fleet-budget` | the asserted cap/saving claims, each with its evidence |

## Truthfulness — a claim that cannot be measured is not claimed

`kushin77/deepseek#106`'s lesson, made mechanical: a record may carry
`claims[]`, each an asserted `cap` or `saving`. **Every claim must name the
evidence that measures it**, and the `receipt.evidence` reference must be
non-empty. The gate **refuses a metering claim whose evidence field is empty** —
a saving or a cap that cannot be measured is not claimed, and a receipt that
cannot be evidenced is not a receipt.

## The gate

`scripts/check-metering-parity.sh` is offline and deterministic, so it can be
wired into `make verify`. It derives the field vocabulary from the schema,
requires `field-map.json` to map every field (naming one-sided ones), requires
`ownership.json` to name exactly one producer per field, validates the example
instances with `jsonschema`, and refuses any empty-evidence claim. It is
tri-state: `0` OK, `1` NOT-OK, `2` CANNOT-ASSESS (an absent or unreadable
contract is never a pass). It then proves it can fail, refusing by name an
unmapped field (`rc 1`), a field with no producer (`rc 1`), a claim with empty
evidence (`rc 1`) and an absent input (`rc 2`).

```bash
bash scripts/check-metering-parity.sh
```

## The direction issue

The 22 peer-mapped fields, plus the 13 fields the fleet emits that the peer
export must add, were filed as a **direction issue on the peer's board** (NG4 —
the only cross-repo write this lane makes):

- **`kushin77/deepseek` issue [#119](https://github.com/kushin77/deepseek/issues/119)**
  — "Direction: emit the fleet's frozen metering/budget record shape (field list)
  — from `kushin77/agent-orchestrator#425`". It carries the field list, cites
  this issue and the boundary contract, references `kushin77/deepseek#55`,
  `#71`, `#115` and `#106`, and closes nothing on the peer's behalf.

## Related

- `kushin77/agent-orchestrator` **#415** — the local producer (`integrations/paperclip/budget.py`) this contract constrains; its receipt shape must not be frozen first.
- **#401** — the ticket projection: carries `facets.budget` and the receipt id.
- **#403** — the PMO rollup: reports spend per goal/agent off this shape.
- `kushin77/deepseek#55`, `kushin77/deepseek#71`, `kushin77/deepseek#29`, `kushin77/deepseek#30`, `kushin77/deepseek#115`, `kushin77/deepseek#106`; `kushin77/ollama#199`.
- [`CROSS-REPO-EXECUTION-BOUNDARY.md`](../../CROSS-REPO-EXECUTION-BOUNDARY.md) — NG4: a peer's work is a direction issue on their board, never an edit here.
