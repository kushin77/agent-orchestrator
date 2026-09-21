# PMO priority + dispatch engine

Issue #403 follow-on (elite-enterprise PMO, `pmo-sme` profile). This document
is the doctrine-to-implementation map for `governance/pmo`'s `priority` and
`dispatch` subcommands, which upgrade the read-only rollups
(`deps`/`lanes`/`report`/`raid`/`aging`/`gates`) into a mechanical, deterministic
engine that ranks open work and attaches it to agents.

## 1. What this is not

`governance/pmo` stays a **derived view** (`governance/pmo/README.md`): no new
ledger, no cache that survives a rebuild, no second source of `status`.
`priority` and `dispatch` are two more queries over the same ticket graph
(`governance/pmo/graph.py`, built from `governance/ticket`, ADR-0014) plus one
new, committed, declared-data input: `governance/pmo/policy.yaml`.

## 2. `priority` — a single explainable order

`python3 governance/pmo/cli.py priority [--json] [--live]`

Every open, non-closed ticket that names a board issue gets one `score`, the
**sum** of five terms — additive, not multiplicative, on purpose: a
multiplicative formula lets any single zero term (including the declared-zero
`sla_breach` below) collapse every score to zero, which would make the whole
order a formality (GR-12). Each term's weight lives in `policy.yaml` and cites
the ledger it is read from:

| Term | Weight source | Read from |
|---|---|---|
| `priority` | `policy.yaml: priority_weights` (P0=100, P1=50, P2=20, unset=5) | the board's `priority:P0/P1/P2` label |
| `aging` | `policy.yaml: aging_weights` (postmortem=50 … watch=5, none=0) | `views.aging`'s own tiering |
| `fanout` | `policy.yaml: fanout_weight_per_blocker` (10) × count | `deps`: how many *other* open tickets name this one in their own `blocked_by` |
| `readiness` | `policy.yaml: readiness_bonus` (25) | `deps`: true when every `blocked_by` target is closed (or there is none) |
| `capacity` | **negative** `policy.yaml: capacity_penalty_per_inflight` (8) × count | `lanes`: the owner's current `in-progress` count — depriotises, never excludes |

A term with **no committed ledger in this repo** is never fabricated. The goal
brief named `sla_breach` (SLA breach); this repo has no `sla-enforcement`
package and the frozen ticket contract (`docs/contracts/paperclip/ticket.schema.json`,
ADR-0014 v2) carries no due-date field. It is declared in `policy.yaml`'s
`unsourced_terms` block with weight `0` and a stated reason, and every
`priority` document carries that block verbatim under `"unsourced"` — reported,
not hidden, exactly like `aging`'s own `unauditable` split.

Sorting is by `(-score, issue number)` — the issue number is an **unconditional**
tiebreak, so two items that tie on every weighted term still land in one
deterministic order. This is what `--check` proves: it re-derives the view
in-process and requires the two renders to be byte-identical, which catches a
formula whose ties depend on dict/set iteration order, not just "the code ran
without an exception."

### Sample output (top of the committed board, 2026-09-19 clock)

```
$ python3 governance/pmo/cli.py priority --json | python3 -c '
import json,sys
d=json.load(sys.stdin)
for item in d["items"][:5]:
    print(item["rank"], item["ticket"], item["score"], item["terms"]["priority"]["level"])'
1 kushin77/agent-orchestrator#706 125.0 P0
2 kushin77/agent-orchestrator#878 125.0 P0
3 kushin77/agent-orchestrator#803 75.0 P1
...
```

## 3. `dispatch` — the priority order, attached to agents

`python3 governance/pmo/cli.py dispatch [--json] [--wave N] [--wave-cap N] [--dry-run] [--apply] [--live]`

`dispatch` walks `priority`'s order and builds one **wave**: a batch of tasks
that can run in parallel because no two share a lane. `docs/EXECUTION-PLAN.md`
§3 ("no two lanes share a file in the same wave") is not re-derived — it is
**declared data**, copied into `policy.yaml`'s `lanes` table, keyed by the
board's `pillar:*` label (`pillar:guardrails-security` → lane `guardrails`,
`owns: ["guardrails/**"]`, tier `L1`, SME `security`, …). A ticket whose pillar
label the table does not name falls to `default_lane` — a declared fall-through,
never a silent guess, mirroring `docs/SME-ROUTING.md`'s own `sme_default`
pattern.

The plan builder is **lane-collision-free by construction**: it walks the
priority-sorted candidates, skips a ticket whose lane a wave has already used
(reported under `deferred`, reason `lane-occupied-this-wave:<lane>`), and
caps the wave at `wave_cap` (`--wave-cap`, default `policy.yaml`'s
`wave_cap_default`, epic #1510's own cap of 12). A candidate is also deferred
when it is not `ready` (an open blocker) or already `owned` (claimed).

Every assignment carries:

* `lane` / `owns` (the globs it may touch) / `tier` (`L0`/`L1`/`L2`) / `model`
  (`policy.yaml: model_tiers`, per `docs/SME-ROUTING.md`'s declared ladder
  flash → pro → auditor) / `sme_profile`;
* `agent_brief` — the exact `Agent` tool skeleton, **goal-first**
  (`~/.claude/CLAUDE.md`'s own `/focus` rule: goal in the first line, then
  constraints, then context, then steps):

```
GOAL: Resolve kushin77/agent-orchestrator#706 (autonomous-ops lane, sniper-generic SME, tier L0/flash) — read the issue, make the change, run the lane's gate, open a PR.
CONSTRAINTS: owns only docs/**, guardrails/**, infra/**; one issue = one lane = one branch (docs/EXECUTION-PLAN.md); never edit vendor/, .board/, .fleet/; escalate tier only on observed difficulty, never pre-emptively.
CONTEXT: priority rank 1, score 125.0 (priority=+100, aging=+0, fanout=+0, readiness=+25, capacity=-0); currently unowned.
STEPS: 1) read the issue and AGENTS.md/CLAUDE.md for autonomous-ops; 2) implement + test; 3) run the lane's gate (make verify or the lane-specific target); 4) open a PR closing kushin77/agent-orchestrator#706.
```

### The falsifiable half (GR-12): `validate_plan`

`dispatch()` calls `dispatch.validate_plan(document)` on every plan it builds.
Because the builder is collision-free *by construction*, the production path
alone would never exercise the refusal — so the gate (`scripts/check-pmo-rollup.sh`,
controls 10–11) calls `validate_plan` directly against a **crafted** plan
document to prove the check itself refuses for real:

* `dispatch-lane-collision` — two assignments in one wave share a lane, naming
  both tickets;
* `dispatch-unowned-risk` — a live risk with no owner (`raid`'s own R
  condition) that a plan neither assigns nor lists under `unowned_risks` with a
  reason — silently dropped. (`_assert_unowned_risks` is the test/gate seam
  that lets a caller assert this without recomputing the whole graph; the
  production `dispatch()` path always populates `unowned_risks` correctly, so
  this seam exists purely so the refusal itself is provable rather than merely
  "true by construction and untested," the same reasoning
  `scripts/check-pmo-rollup.sh` already applies to every other control.)

### `--apply` (off by default; never run by the gate)

`--apply` posts one comment per assignment, marked `<!-- pmo-dispatch:v1 -->`
and updated in place on a second run (idempotent — the marker is looked up via
`gh issue view --json comments` before deciding create vs. PATCH), plus the
`pmo:dispatched` label, via the `gh` CLI. It lives entirely in `cli.py`
(`_apply_dispatch`), never in `dispatch.py`: the plan derivation itself has no
GitHub-writing code path, so the gate can exercise `dispatch --check` (and
`validate_plan`) without ever touching the network. `dispatch` coordinates; it
never executes lane work, and `--apply` is the narrowest possible exception to
"writes nothing" — one comment, one label, both idempotent, never a merge,
reassignment, or write into another lane's files.

### `--live`

Optional, read-only: `gh issue list --json number,labels` overlaid on top of
the committed `.board/snapshot.json` labels already loaded, so a priority/
dispatch run can reflect labels changed since the board was last synced.
**Any** failure (no `gh` binary, no network, an auth error, a rate limit) is
caught and reported as a note; it never raises, never turns a run into
CANNOT-ASSESS. This is exactly the property the offline default is for: `make
pmo` (the gate) never passes `--live`, so it is bit-for-bit reproducible from
the committed checkout alone.

## 4. Adding a new scoring term

1. Confirm the term has a **real, committed** source in the graph or a sibling
   ledger — if it doesn't, it goes in `unsourced_terms` with weight 0 and a
   reason, not into the weighted sum (§2 above explains why).
2. Add the weight to `governance/pmo/policy.yaml`, with a comment naming the
   view/field it reads from, and (if it introduces a new key shape) extend
   `governance/pmo/policy.schema.json`.
3. Compute the term in `governance/pmo/priority.py::priority()`, add it to the
   `terms` dict on each item (so it is visible and cited, per the existing
   five), and fold it into `total`.
4. Add a test to `governance/pmo/tests/test_priority.py` that provokes the term
   moving the rank (mirror the existing "P0 outranks P1" / "fanout rewards …"
   tests) — a term that never changes an order in a test is unproven.
5. Run `python3 -m pytest governance/pmo/tests -q` and `bash
   scripts/check-pmo-rollup.sh` (or `make pmo`) before committing.

## 5. Commands

```bash
python3 governance/pmo/cli.py priority [--json] [--live]
python3 governance/pmo/cli.py dispatch [--json] [--wave N] [--wave-cap N] [--dry-run] [--apply] [--live]
make pmo-dispatch          # priority + dispatch wave 1, human-readable
make pmo                   # the gate (make verify's entry point)
python3 -m pytest governance/pmo/tests -q
```

Exit-code contract is unchanged from the rest of `governance/pmo`: `0` OK /
`1` NOT-OK (a finding — a lane collision, a dropped unowned risk, a
non-deterministic render) / `2` CANNOT-ASSESS (the graph or `policy.yaml`
itself could not be built — never reported as a pass).

## 6. Persona routing (ADR-0012), and what stayed scoped out

An initial mid-implementation message asked this engine to make Hermes an
**authoritative runtime sink** the paperclip ticket feeds into — a live
service dependency. That reading was declined: it is the opposite of this
repo's own ratified decision,
[ADR-0012](decision-records/ADR-0012-hermes-paperclip-boundary.md), whose §(e)
is explicit that the accepted path is *"map the policy, do not couple the
runtime"* — no service call, no availability dependency on Hermes.

A follow-up correctly pointed at ADR-0012's own **Decision (b)**, which this
package had not yet implemented: *"Work is routed by capability from the
registry personas… code / test / PR authoring routes to the `hermes` persona
at MED… research / docs / reporting routes to the `paperclip` persona at
LOW."* That is **persona routing** — a declared registry vocabulary lookup
(`registry/profiles/seeds/hermes.1.0.0.yaml`'s `capabilitySet: [code-author,
test-author, test-run, …]` vs. `registry/profiles/seeds/paperclip.1.1.0.yaml`'s
`capabilitySet: [research, docs-authoring, …]`), not a runtime call — and is
exactly what (e)'s boundary permits. `dispatch` now implements it:

* **`executor`** on every per-issue assignment is `"hermes"` or `"paperclip"`,
  chosen from the ticket's `kind` facet (a real, committed ADR-0014 contract
  field, never an invented text-keyword heuristic): the lessons/RCA/decision
  family (`incident`/`rca`/`corrective-action`/`lesson`/`suggestion`) is the
  research/reporting half → `paperclip`; the default `task` kind is the
  code-author/test/PR half → `hermes`. A lane's declared `executor:` override
  in `policy.yaml` (SME-ROUTING-style) wins outright when present — none of
  the shipped lanes declare one, so today every assignment follows the
  kind-based default. The tier/model assignment (`L0`/`L1`/`L2`, the
  high-floor rule for security/identity lanes) is a completely orthogonal
  field and is untouched by this addition.
* **`paperclip_ticket`** on every per-issue assignment is the reporting half:
  a record shaped to `docs/contracts/paperclip/ticket.schema.json` (ADR-0014's
  join node), containing only the contract's own keys
  (`id`/`owner`/`status`/`blocked_by`/`goal`/`kind`/`facets`) and only the ones
  the graph actually populates — a dispatch-ready, unclaimed ticket
  legitimately has no `owner`/`status`/`goal` yet, and inventing one to
  satisfy the contract's `required` list would be exactly the fabrication
  GR-12 forbids (§2's rule about `unsourced` terms, applied to a whole
  record instead of one score term). This is a **projection written to
  stdout only** — `dispatch` still adds no ledger.

### `dispatch --by-cluster`

The same follow-up asked for a mode that dispatches one agent per group of
similar ready issues, so a wave can batch N similar RCA-family backfills in
tandem instead of one agent per issue. Because no committed source for that
grouping exists in this repo (`board-triage` is a proposed sibling lane, not a
landed one), the grouping itself is **out of scope for `governance/pmo` to
invent** — the same "no term without a committed source" rule from §2 applies
to a whole clustering, not just a score term. What *is* in scope, and shipped:
a contract the PMO validates and consumes, never authors —
`governance/pmo/clusters.schema.json` — plus `governance/pmo/clusters.py`
(load + validate) and `dispatch --by-cluster`, which:

* with **no** `governance/pmo/clusters.json` committed, falls back to the
  per-issue plan, byte-for-byte unchanged (`by_cluster: false` in the
  document) — this is the state today, and what the gate exercises;
* with a **schema-invalid** `clusters.json`, is CANNOT-ASSESS (rc 2) — an
  input that claims to be a cluster proposal but is not shaped like one is
  never silently accepted or silently dropped (`scripts/check-pmo-rollup.sh`
  control 12 provokes this for real);
* with a **valid** `clusters.json`, dispatches one assignment per
  `batchable: true` cluster whose `wave` matches `--wave`, sorted by
  `priority_rank`, each with one `agent_brief` naming every member issue and
  the cluster's own `recipe`, `sme` and `tier` (control 13 provokes the
  positive path over a fixture).

`governance/pmo/clusters.schema.json` is the contract a `board-triage` lane
(or any other producer) must conform to; this package does not depend on that
lane landing, does not read anything from it beyond the committed file, and
never writes `clusters.json` itself.
