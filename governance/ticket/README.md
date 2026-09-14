# `governance/ticket` — the ticket projection (issue #401)

The fleet describes the same work on five surfaces — a board **issue**, an agent
**session**, a **lesson**, a **budget charge** and a **PMO row** — and joins them
pairwise, by convention. [ADR-0014](../../docs/decision-records/ADR-0014-ticket-single-join-node-contract-v2.md)
moves that topology from mesh to hub: the **paperclip ticket is the single join
node**, and it is a **projection, never authority**. This package is that
projection.

The frozen shape is the contract v2 JSON Schema at
[`docs/contracts/paperclip/ticket.schema.json`](../../docs/contracts/paperclip/ticket.schema.json);
the seam it sits in is [`docs/PAPERCLIP-ING-INTEGRATION.md`](../../docs/PAPERCLIP-ING-INTEGRATION.md)
§3.1. The vocabulary is **read from the schema**, never restated here, so the
projection cannot drift from the contract it enforces.

## Commands

```bash
python3 governance/ticket/cli.py project   # write the projected store
python3 governance/ticket/cli.py verify    # re-derive, prove rebuildability, compare
bash scripts/check-ticket-projection.sh    # the gate (in `make verify`)
make ticket                                # the gate, as a make target
```

The projected store is `.verify/ticket/tickets.json` — runtime state under
`.verify/` (gitignored). A projection that were committed would become the second
source of truth ADR-0014 exists to prevent.

## The join, producer by producer

Each reader is a **producer** whose name is exactly the `authority` value the
contract pins for the fields it supplies. That is what makes the one-writer rule
checkable rather than merely documented.

| Producer (`authority` value) | Fields it writes | Read from |
|---|---|---|
| `.board/snapshot.json` | `goal`, `blocked_by` | the committed board snapshot |
| `governance/dispatch` | `status` | `.board/claims/**` + `.board/claims.jsonl` |
| `governance/isolation` | `owner` | the claiming session identity in the claim ledger |
| `governance/lessons` | `facets.lessons` | `governance/lessons/ledger.jsonl` |
| `telemetry/budgets` | `facets.budget` | `telemetry/budgets/ledger.jsonl` (when present) |
| `derived` | `facets.raid` | the ticket's own priority label and open corrective actions |
| `.verify/attestation.json` | `evidence` | the gate attestation for the lane's branch |

`id` and `kind` are the node's own identity and `evidence` is appended by
whichever lane ran the proof; those three are deliberately **not**
authority-tracked (ADR-0014). Every other populated field must name exactly one
writer.

### What becomes a node

* **Issues** from the board snapshot, `kind` = `task`.
* **Ledger records** from the lessons register — `incident`, `rca`,
  `corrective-action`, `lesson`, and `suggestion` for an open `SUGGEST-*` (that
  is how the generic improvement register becomes an addressable node).
* An issue's `facets.lessons` join comes from the incidents whose `origin` names
  it; a `pr` origin is deliberately **not** a ticket edge, because the fleet's
  issue and PR numbering share one sequence and coercing one into the other would
  invent an edge.

## The rules the projection refuses on

* **One writer per field.** A populated authority-tracked field written by two
  producers fails, naming the ticket, the field and both producers.
* **The declared writer.** A populated authority-tracked field written by any
  producer other than its `authority` value fails, naming both.
* **A known field only.** A populated field the frozen contract does not carry
  (for example an undeclared facet) fails — the facet set is closed.
* **No silent skips.** An unresolvable reference fails naming the file and line:
  a lesson whose *issue* origin is absent from the board, a claim for an unknown
  issue, a budget charge for an unknown issue, or an attestation naming a branch
  for an issue the board lacks.
* **No unbacked node.** A ticket no ledger supplies a field for fails, naming it.
* **One receipt.** A `facets.budget.receipt` that no `evidence[]` receipt on the
  same ticket carries fails — the contract's mismatch #10, closed.

## Determinism and rebuildability

`build()` is a pure function of the committed ledgers: no timestamps, no
dictionary-iteration order, and every list sorted by a stable key. Two builds
over one revision are byte-identical. `verify` proves the store is not a source
of truth: it re-derives, compares against the store, **deletes the store**,
rebuilds from the ledgers, and compares hashes. A rebuild that differs is a
failure, because a hub that cannot be rebuilt is a second source of truth.

Run `project` before `verify` when you have edited a ledger: `verify` compares the
store to the rebuild, so a stale store is (correctly) reported as stale.

## The negative-control seam

Two CLI options exist **only** so the gate can show that the rules can fail:

* `--stamp <value>` embeds a `generated_at` field, which makes two builds differ.
  A real build never passes it; its presence is what proves the determinism check
  is not vacuous.
* `--negative-control <file>` loads extra `Contribution` records that provoke a
  two-writer field, a producer mismatch, an unknown field or an unbacked ticket.
  The records only ever influence the projected output — no caller in the fleet
  passes either option.

[`scripts/check-ticket-projection.sh`](../../scripts/check-ticket-projection.sh)
provokes each refusal for real and fails if any control passes: a check that
cannot fail is a formality (GR-12).

## Inputs are read-only

This package reads `.board/`, `governance/lessons/ledger.jsonl`, the budget rail
and `.verify/attestation.json`. It writes exactly one path —
`.verify/ticket/tickets.json` — and never a ledger, the board, or another lane's
files. The claim ledger is replayed **without** the wall clock: lease expiry is
the live claim gate's concern, and a projection that read the clock could not be
rebuilt byte-identically.
