---
id: ADR-0017
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0017: The diagrams blueprint on the paperclip operator surface — a read-only `evidence[]` signal, never a new facet

## Status

`accepted` — ratified on the PR for issue #463 (EPIC #461, "the diagrams chain",
milestone M27). It fixes **where a diagram signal lives on a ticket**, so the
projection lane (#465) implements a frozen contract instead of inventing one. It
supersedes no earlier decision. The boundary it operates within is
[`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) (*map the policy, do not
couple the runtime*) and
[`ADR-0013`](ADR-0013-paperclip-ing-integration.md) (adopt the upstream CLI over
HTTP); the ticket contract it extends is
[`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md) (the ticket is the
single join node — closed `facets`, one-writer-per-field `authority{}`, the ticket
a projection, never authority); and the authority split it restates for this
surface is CMR `ADR-0018` (the Architecture SSOT transition).

## Context

EPIC #461 makes `kushin77/diagrams` (the GR-18 mandatory SSOT architecture-blueprint
module) a **first-class, read-only input** to the paperclip operator surface: the
live-state picture — extracted topology, rendered blueprint, drift Findings — is
what decisions are made against and what closes the tasks those decisions create.
The obvious first implementation is to give the diagram signal its own home on the
ticket. `docs/PAPERCLIP-ING-INTEGRATION.md` §3.1 froze two constraints that collide
with that instinct.

1. **`facets` is a closed set** — `lessons`, `raid`, `budget`. "An unknown facet
   fails; a new facet is a contract change, not a silent extension" (ADR-0014,
   rule 2; enforced by `scripts/check-paperclip-integration.sh`, which pins the
   literal set `["budget", "lessons", "raid"]` in
   `docs/contracts/paperclip/ticket.schema.json`).
2. **`authority{}` is the one-writer-per-field map**, and the ticket is a
   **projection, never authority** (ADR-0014, rule 3). Every populated field names
   **exactly one** producing lane; a field with two writers fails and a populated
   field with no declared writer fails.

### Which question the blueprint owns on this surface

This is **not a new question** and is not re-litigated here. CMR `ADR-0018`
(`vendor/CMR/docs/decision-records/ADR-0018-architecture-ssot-transition.md`, a
vendor-ratified decision, cited by path and never restated as our own) split
architecture authority **by question**: *"What is live, today?"* belongs to the
diagrams blueprint (`ssot-extract` + `blueprint-gen` + `drift-detect`);
*"What did we decide, and why?"* belongs to the human-authored target doc and the
ADRs; and a drift the blueprint detects between live and declared state is a
**signal to open an ADR or a ticket — never a silent absorption**.

Applied to this repo's operator surface, the blueprint owns **exactly one**
question: *what is live, today?* It owns neither *what did we decide* (that is
`docs/ARCHITECTURE.md` + `docs/decision-records/`) nor *who acts* (that is the
ticket — the single join node, ADR-0014). The projection is therefore **read-only
and one-way**: diagram → decision → ticket; **never** diagram → write. Nothing in
this decision lets a diagram mutate fleet state, and the fleet stays authoritative
for dispatch, claims, budgets and audit (ADR-0012).

## Decision

**A diagram signal lives in a ticket's structured `evidence[]`, as an appended
receipt — not in a new `facets.diagrams`, and not as a new ticket `kind`.**

Two commitments:

1. **Shape — evidence, not a facet.** A diagram signal (a drift Finding, a
   rendered-blueprint reference, a `content_hash`) rides a ticket's `evidence[]`
   as an additive structured receipt `{kind, ref, result, checks}` — the v2 receipt
   §3.1 already froze (ADR-0014, rule 4). `evidence` is explicitly **not
   authority-tracked** ("appended by whichever lane ran the proof", ADR-0014),
   which is exactly the semantics a read-only projection needs: the adapter lane
   appends a proof artifact; it does not become a writer of an authority-tracked
   field. The adapter lane (#465) therefore needs **no contract move** — no schema
   change, no gate change, no new closed-set member.

2. **Kind — a drift finding is a `task`.** The blueprint's drift→decide loop does
   **not** need a new ticket `kind`. `kind` names **what the work item is**
   (`task | incident | rca | corrective-action | lesson | suggestion` — the closed
   vocabulary ADR-0014 rule 1 froze). A drift Finding is **why a task exists**,
   which is evidence, not a kind. So the blueprint opens a `task` whose
   `evidence[]` is the Finding; the Finding is the task's proof, not its type.

### The rejected shape — `facets.diagrams`, and why

The rejected option is a new closed-set member `facets.diagrams` (with
`authority.facets.diagrams` naming its writer). It is rejected on three grounds.

- **It would name an external runtime as a fleet authority.** A facet is
  authority-tracked: §3.1's `authority{}` maps every populated facet to **exactly
  one** producing lane. The only producer of a diagram facet is `kushin77/diagrams`
  — a **vendor process outside this repo**. Declaring it the single writer of a
  fleet ticket field makes an external runtime an authority over a fleet
  projection, which is precisely the *couple the runtime* outcome ADR-0012 forbids
  and the *projection, never authority* rule (ADR-0014 rule 3) excludes.
- **It cannot be enforced from here, so it would rot silently.** The producer is
  not in this repo: if the vendor renames or drops the signal, **no fleet gate can
  fail** and the field simply goes stale. That is the silent rot the closed-set
  rule exists to prevent — and it is the exact reason the closed set must not gain
  a member whose writer lives across the process boundary. Evidence's appender is
  the fleet's **own** adapter lane (#465), which the fleet's gate surface covers,
  so the signal is enforceable and cannot rot unnoticed.
- **The contract change buys nothing.** Moving the closed set would mean editing
  `docs/contracts/paperclip/ticket.schema.json` (`facets.properties` +
  `authority.properties`) **and** `scripts/check-paperclip-integration.sh`
  (`expected_facets` / `expected_auth` literals) together — the gate move the
  closed-set rule demands (wiring is #466's lane). That change adds a field whose
  value is a read-only projection of external state, which additivity already
  carries. The cost is real and the benefit is nil.

## Consequences

- **Positive:** the projection lane implements a frozen contract (#465) with no
  schema or gate churn; the closed `facets` set stays closed and the join node
  (ADR-0014) is untouched; the anti-rot enforcement lives where the producer lives
  (the fleet's own adapter gate), not at an unenforceable process boundary; the
  blueprint can never be read as an authority over a fleet field.
- **Negative:** a diagram signal is not first-class on the ticket the way a facet
  would be — it is one receipt kind among many in `evidence[]`, so a consumer that
  wants *only* diagram signals filters by receipt `kind` rather than reading a
  dedicated field. This is accepted: the signal is a proof, and proofs are
  appended, not owned.
- **Neutral:** §7 of `docs/PAPERCLIP-ING-INTEGRATION.md` records the seam shape
  additively; §1–§6 are unchanged. No ADR is superseded. Should the fleet ever
  become the diagram signal's producer in-process, revisiting `facets.diagrams`
  would be a **new** ADR with its gate move, not an edit to this one.

## Anchors (by path)

- `docs/PAPERCLIP-ING-INTEGRATION.md` — §3.1 (the frozen closed facets + authority
  rules) and the new §7 (the diagrams seam shape this decision freezes).
- `docs/contracts/paperclip/ticket.schema.json` — v2 `evidence.items`
  (`anyOf` string | `evidence_receipt`), the closed `facets` set, and the
  `authority{}` constants.
- `docs/decision-records/ADR-0014-ticket-single-join-node-contract-v2.md` — the
  single join node and the four frozen rules.
- `docs/decision-records/ADR-0012-hermes-paperclip-boundary.md` and
  `docs/decision-records/ADR-0013-paperclip-ing-integration.md` — the boundary.
- CMR `ADR-0018` —
  `vendor/CMR/docs/decision-records/ADR-0018-architecture-ssot-transition.md` —
  the authority-split-by-question this decision restates for the operator surface
  (a vendor-ratified decision, cited by path).
- `integrations/paperclip/` — the canonical boundary adapter (ADR-0016) the
  projection extends; `integrations/paperclip/diagrams.py` is #465's deliverable.
- `scripts/check-paperclip-integration.sh` — the gate that would move **iff** a
  facet were chosen (`expected_facets = ["budget", "lessons", "raid"]`). It is
  **not** moved here; the evidence choice leaves it untouched, and wiring is #466's
  lane.
