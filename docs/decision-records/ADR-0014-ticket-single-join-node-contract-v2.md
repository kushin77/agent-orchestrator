---
id: ADR-0014
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0014: The paperclip ticket is the single join node — freeze ticket contract v2 (facets, authority)

## Status

`accepted` — ratified on the PR for issue #400 (EPIC #399, M26). This record is
the **ADR-0013 pattern** (a decision and the frozen seam it implies, in one lane)
applied to the **join**: ADR-0013 decided *whether* to integrate upstream and
ADR-0012 fixed the *ownership boundary*; this record decides **what the fleet's
tasks, agents, the lessons register and the PMO all join on** — the ticket — and
freezes the machine-readable contract that six parallel lanes (#414–#419) code
against.

The decision reproduced here is exactly what the seam doc freezes as a JSON
Schema, so the record and the schema cannot drift apart: the normative shape is
[`../contracts/paperclip/ticket.schema.json`](../contracts/paperclip/ticket.schema.json)
(v2) and the prose is
[`../PAPERCLIP-ING-INTEGRATION.md`](../PAPERCLIP-ING-INTEGRATION.md) §3.1, both
cross-checked by `scripts/check-paperclip-integration.sh` in `make verify`. It
supersedes no earlier decision; the boundary it operates within is
[`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) (unamended) and the
integration mode it assumes is
[`ADR-0013`](ADR-0013-paperclip-ing-integration.md) (unamended).

## Context

The fleet has five surfaces that describe the same work, and today they are
joined **pairwise, by convention**:

- an **issue** (the unit of work, the canonical roadmap — GR-2);
- an **agent session** (a minted identity bound to exactly one issue, ADR-0011 /
  issue #263);
- a **lesson** (`LESSON-*` / `SUGGEST-*` in the lessons register);
- a **budget charge** (a spend against an agent, `telemetry/budgets/`);
- a **PMO row** (a roll-up over issues).

Each pair is joined by whatever marker was at hand when someone needed the join:
a `cmr-refs:` target, a `Refs owner/repo#N` commit trailer, a PR body, a ledger
id, a field in `.board/snapshot.json`. With five surfaces that is up to
$\binom{5}{2} = 10$ independent joins, each invented locally, none of them
validated. Two symptoms are already real:

1. **A ticket's evidence is a convention, not a field.** "Evidence" today is the
   PR plus the gate output pasted onto the issue (GR-12) — there is no structured
   record, so the same receipt that proves a delivery cannot also back a budget
   charge (mismatches #6 and #10 in the seam doc).
2. **The improvement register has no addressable node.** A `SUGGEST-*` is a valid
   ledger id but not a valid `cmr-refs:` target: there is no *kind* of ticket it
   can be, so an open suggestion cannot be dispatched, blocked-by, or closed.

**Six lanes are blocked on this.** #414–#419 each build a consumer of the join
(projection, lessons, PMO). If each invents its own shape, the fleet pays the
contract sprawl it is trying to escape — the whole point of freezing a seam is
that the consumers and the producers agree *before* they are written.

**Why the ticket.** It is the one artifact every other surface already names: a
dispatch claim names an issue, a lesson names an incident discovered by an issue,
a budget charge names the task that spent, and the PMO rolls up issues. Choosing
the ticket as the join is choosing the node the edges already point at.

**The decision space.** Three options were live:

1. **Keep the mesh.** Let each pair be joined ad hoc. Cheap today, unbounded
   tomorrow: the joins multiply with every new surface, none can be validated,
   and `SUGGEST-*` stays unaddressable. The cost is *superlinear in the number of
   surfaces*, which is exactly the wrong shape for a fleet that keeps adding them.
2. **Make the ticket authoritative.** Let the ticket *own* status, ownership,
   goals and budgets. This is the seductive option and it is wrong for one
   reason: it creates a **second authoritative engine** for dispatch, claims,
   budgets and audit — precisely what ADR-0012's *map the policy, do not couple
   the runtime* rule forbids. Two engines means two truths to reconcile forever.
3. **Store RAID (risk/assumption/issue/dependency) separately.** Model risk and
   remediation in their own store. This only *moves* the join: the separate store
   must still be correlated back to the task that carries the risk, so it
   recreates the same mesh one level down.

The chosen option is none of these taken whole: the ticket is the **hub**, and it
is a **projection, never authority**.

## Decision

**The paperclip ticket is the single join node. The topology moves from mesh to
hub.** Tasks, agents (sessions), the lessons register and the PMO all join on the
ticket, and the ticket is a **rebuildable projection** over the fleet's
authoritative surfaces — never itself an authority. Dispatch and claims
(`governance/dispatch/`), the canonical board and its snapshot
(`.board/snapshot.json`), the lessons register (`governance/lessons/`), the budget
rail (`telemetry/budgets/`, `gateway/finops/`) and the audit stream
(`.fleet/slog.jsonl`, `telemetry/ledger/`) stay authoritative exactly where they
already are. This is ADR-0012's rule applied to the join: **map the policy, do not
couple the runtime** — the ticket maps them, it does not own them.

**Contract v2 is additive.** The v1 keys — `id`, `owner`, `status`, `blocked_by`,
`goal`, `evidence` — keep their meaning and a v1 document still validates. v2
adds three things and changes no v1 field's meaning:

1. **`kind` ∈ `task | incident | rca | corrective-action | lesson | suggestion`.**
   The **generic improvement register is a ticket kind**. This is how an open
   `SUGGEST-*` becomes addressable: it is a ticket of kind `suggestion`, so it can
   be dispatched, blocked-by and closed like any other node, instead of being a
   ledger id with no shape.
2. **`facets` — a closed set.** The facet groups a ticket joins on are
   `lessons` (`incident`, `rca`, `corrective_actions`, `class`), `raid` (`risk`,
   `remediation`) and `budget` (`scope{level,id}`, `spent`, `cap`, `receipt`). The
   set is **closed**: an unknown facet fails, so a new facet is a contract change,
   not a silent extension.
3. **`authority{}` — one writer per field.** Each populated field names the
   **single** lane that produces it. A field with two writers fails; a populated
   field with no declared writer fails. The frozen map is:

   | Ticket field | Single writer (authority) |
   |---|---|
   | `owner` | `governance/isolation` |
   | `status` | `governance/dispatch` |
   | `goal` | `.board/snapshot.json` |
   | `blocked_by` | `.board/snapshot.json` |
   | `facets.lessons` | `governance/lessons` |
   | `facets.raid` | `derived` |
   | `facets.budget` | `telemetry/budgets` |

   (`facets.raid` is `derived` — it is computed from the ticket's risk signals,
   not written by a lane. The identity keys `id`/`kind` and the `evidence` array
   are not authority-tracked: `id`/`kind` are the node's own identity and
   `evidence` is appended by whichever lane ran the proof.)

**`evidence[]` becomes structured, additively.** v1 accepted a bare string; v2
also admits a receipt object `{kind, ref, result, checks}` so the *same* receipt
that proves delivery also backs the budget charge — the one change that closes
mismatches #6 and #10 at once. Both forms validate, so this stays additive.

**Why one-writer-per-field is the load-bearing rule.** A projection is only
rebuildable if, for every field, there is exactly one place to read the truth
from. Two writers make the projection ambiguous (which one wins?), and no writer
makes it a fiction (where did this come from?). One writer per field is what turns
"the ticket joins the surfaces" from a slogan into a property a gate can check: a
ticket is rebuildable iff every populated field names exactly one producer, and
the rebuild is deterministic iff that producer is a real authoritative surface.
This is why the rule is enforced by `scripts/check-paperclip-integration.sh`, not
merely documented.

**What stays OURS regardless** (out of scope by construction, not aspiration):

- the **claim ledger** `governance/dispatch/` — claims and dependency-ordered
  eligibility remain authoritative here; the ticket *projects* them;
- the **canonical board** and `.board/snapshot.json` — the ticket does not become
  the board;
- the **lessons register** `governance/lessons/` and the **budget rail**
  `telemetry/budgets/` — the ticket joins them, it does not replace them.

**Explicitly out of scope.** This record decides the topology and freezes the
contract; it **edits no code, stands up no process, and changes no runtime
behaviour.** The heartbeat and budget schemas are untouched by this decision (v2
is scoped to the ticket). It does not rename anything and does not migrate data.

**Rejected alternatives (recorded, as ADR-0012's method requires):**

- **Keep the mesh** — rejected: superlinear join count, unvalidatable joins, and
  `SUGGEST-*` remains unaddressable.
- **Make the ticket authoritative** — rejected: a second authoritative engine for
  dispatch/claims/budgets/audit, violating ADR-0012's no-two-engines rule.
- **Store RAID separately** — rejected: relocates the join instead of resolving
  it; the ticket facet `raid` keeps risk attached to the node that carries it.

## Consequences

- **Positive:** the join collapses from up to ten ad-hoc pairwise links to one
  hub with one shape per field, and the shape is machine-readable (a JSON Schema)
  and gate-checked, so six parallel lanes can be built against it without
  inventing competing shapes. The improvement register becomes addressable: a
  `SUGGEST-*` is now a ticket of kind `suggestion`, so an open elite suggestion can
  be dispatched and closed like any other node. Structured evidence makes one
  receipt serve both delivery proof and budget backing, closing mismatches #6 and
  #10. ADR-0012's rule is honoured, not bent: the fleet's claim, board, lessons
  and budget surfaces remain the single authoritative engines, and the mapping is
  recorded field-by-field in `authority{}`.
- **Negative:** a **projection has a rebuild cost and a staleness window** — the
  ticket can lag its authoritative surfaces, and something must rebuild it (the
  projection lane, #414). The `authority{}` map is now a **coupling surface**: add
  a produced field without a writer and the gate fails, which is the point but
  also the discipline. The fleet-side producers for some fields (notably
  `facets.budget.scope.level = agent`) still do not exist — v2 freezes the shape,
  the adoption builds the producers (mismatch #7).
- **Neutral:** nothing is renamed, no transport changes (ADR-0011 stands), the
  heartbeat and budget schemas are untouched, and no data migrates. A v1 ticket
  document still validates, so the contract change breaks no existing consumer.

**Follow-ups (named):** the six consumers #414–#419 code against this frozen
shape. The projection lane must rebuild the ticket from the authoritative
surfaces named in `authority{}`. The lesson facet's `class` vocabulary follows
`docs/SOLUTION-CLASSES.md` (`template → class → pattern → enterprise → faang →
elite`). The mismatch list in the seam doc is annotated with which items v2 closes
(#4, #5, #6, #10) and which remain open (the adoption's work-order).

**Reversibility.** The decision is **reversible at bounded cost**. The contract is
one schema plus a projection; reversing to the mesh, or to a different join node,
replaces one projection implementation and one schema rather than rewriting the
fleet, and because no authoritative surface depends on the ticket existing,
reversal never stops the fleet. That is the same reversibility shape ADR-0011
used for transport and ADR-0013 used for the operator surface.
