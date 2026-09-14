# `governance/pmo` — the PMO layer as derived views (issue #403)

CMR's doctrine (`vendor/CMR/docs/PROGRAM-MANAGEMENT.md` — the pinned submodule,
unpopulated in a fresh worktree, so quoted rather than linked) names five PMO
abilities and a surface `fleet/pmo.sh {deps,lanes,report,raid,aging}`.
Measured 2026-09-14, `git ls-tree -r --name-only origin/master | grep -i pmo` was
**empty**: the layer existed as doctrine with no producer here. This package is
that producer — and it is deliberately **not a store**.

Every subcommand is a **query over the ticket graph** built by
[`governance/ticket`](../ticket/README.md) (ADR-0014: the paperclip ticket is the
single join node, a projection and never authority). The PMO adds **no ledger**,
**no cache that survives a rebuild** and **no second source of `status`**: the
graph is rebuilt in memory from the same committed ledgers, and the only thing a
view writes is its own stdout.

## Commands

```bash
python3 governance/pmo/cli.py deps    # blocked_by + goal edges
python3 governance/pmo/cli.py lanes   # owner × lane occupancy
python3 governance/pmo/cli.py report  # tickets by goal / status / owner
python3 governance/pmo/cli.py raid    # R / A / I / D + dependency edges
python3 governance/pmo/cli.py aging   # what has been waiting, tiered
python3 governance/pmo/cli.py gates   # review-gate state + escalation rung (#635)
bash scripts/check-pmo-rollup.sh      # the gate (in `make verify`)
make pmo                              # the gate, as a make target
```

Options: `--root` (repository root), `--json` (the whole of stdout is the
document), `--check` (gate mode — report findings as NOT-OK, re-derive to prove
determinism, and reconcile `--against FILE`), `--now ISO` (override the clock).

Exit-code contract: **0 OK / 1 NOT-OK / 2 CANNOT-ASSESS**. A graph that cannot be
built (an unreadable board or ledger, a projection that refuses to build, no
timestamp to anchor an age) is `2` — **never reported as a pass**.

## The views

| Subcommand | Derived from | Replaces |
|---|---|---|
| `deps` | ticket `blocked_by` + `goal` edges | hand-joined dependency chains |
| `lanes` | ticket `owner` × the claim's lane, occupancy by status | lane occupancy by eye |
| `report` | tickets by `goal` / `status` / `owner` | the status rollup over several ledgers |
| `raid` | **R** = live risks, **A** = assumptions, **I** = incident-kind, **D** = decision-kind + dependency edges | a RAID register assembled from four sources |
| `aging` | ticket timestamps and status age, tiered | nothing (this did not exist) |
| `gates` | per-task review-gate state + escalation rung (issue #635) | gate state read out of the lifecycle by hand |

### `raid` — the derived register

* **R — live risks.** Blocked or at-risk *open* work the claim ledger actually
  holds (the ticket has a `status`). The doctrine is explicit — *"every risk
  carries an owner and a named remediation"* — so an R item that names no owner
  is a **finding**, naming the ticket.
* **A — assumptions.** The risks the fleet is *assuming* will not bite: open
  tickets that carry a risk but are dormant (no live claim) and name no
  remediation. They are reported, not failed — surfacing an assumption is the
  point.
* **I — incidents.** The incident register and its RCA / corrective-action /
  lesson family — the ticket kinds the lessons register projects.
* **D — decisions + dependencies.** The decision nodes (the improvement
  register, `SUGGEST-*`) plus every `blocked_by` / `goal` edge.

### `aging` — what is rotting, and who owns it

Tiers are explicit, from CMR doctrine ("older than 14 days, tiered
watch/attention/red/postmortem"): **watch ≥ 14d, attention ≥ 30d, red ≥ 60d,
postmortem ≥ 90d**. An aging item that names no owner is a **finding** — nobody
is watching what is rotting.

An aging item's age is measured from the timestamp at which the ticket entered
its current state, read from the graph's own committed inputs (the claim
ledger's `at`, the ledger record's `date`, the board's `closed_at`). The clock is
the **latest timestamp those inputs themselves carry**, so the view is offline
and rebuildable byte-identically — no wall clock.

The view is honest about what it cannot date: an aging *candidate* the committed
inputs give no timestamp for is reported under `unauditable`, with the reason
(the board snapshot carries no filed-at timestamp, so a dormant ticket cannot be
honestly aged), and `candidates` is the sum of aged and unauditable. That split
is rendered, not hidden — but it is not a finding, because it is a property of
the board's shape rather than a governance failure.

### `gates` — the review gate, per task (issue #635)

The workbook-4 review gate ([`governance/merge/gates.py`](../merge/README.md))
is a step on the tenant task lifecycle. This view surfaces its state per task as
a **derivation** over facts the graph already carries — never a store:

* the ticket's `status` (the claim ledger's verdict) says whether the gate is
  **open** (`in-review` / `done`), **closed** (a red/rejected verdict), or
  **pending** (in flight, not yet reviewed);
* the board issue's `review-gate:<verdict>` label carries the **verdict** the
  review produced, when one was recorded (`open` / `red` / `rejected` / …);
* the `review-escalation:<rung>` label carries the C-suite rung a closed gate
  reached. The declared chain is `rungs: ["COO", "CEO"]` — COO (pacing) then CEO
  (board escalation), and it terminates.

The **verdict label is authoritative** when present (it is the review's own
finding), and `status` is the fallback for a task that has not recorded one.
The findings are the conditions the workbook forbids:

| Finding | Meaning |
|---|---|
| `gate-closed-but-done` | the task is done but its gate reads closed — **a red gate yielded a closed task** |
| `gate-verdict-missing` | a gate-scoped task is done and records no verdict (a close nobody can evidence) |
| `gate-escalation-missing` | the gate is closed but no escalation rung is named |
| `gate-escalation-unrunged` | the named rung is not in the declared chain |

Adoption is **explicit**: the gate contract applies to a task that carries a
`review-gate:*` or `review-escalation:*` label. A legacy close that predates the
gate carries neither and is reported as gate state (`open` for a done task)
rather than failed — a view that fails on all history is not a view. (Measured
2026-09-14: over the committed 166-ticket graph the view exits **0 OK**; planting
a gate-scoped close with no verdict flips it to **1 NOT-OK**, naming the ticket.)

## What makes the views falsifiable (GR-12)

A view that can never be wrong is a formality. The gate
([`scripts/check-pmo-rollup.sh`](../../scripts/check-pmo-rollup.sh)) therefore
**provokes** each failure for real:

| Control | What it drives | Must |
|---|---|---|
| derived RAID set = expected set | a fixture with a known ticket set | match **exactly** |
| owner-less live risk | a claimed, at-risk ticket with no agent | exit 1, naming the ticket |
| RAID disagrees with the graph | remove a ticket, keep the saved view | exit 1, naming the removed ticket |
| rollup from a stale cache | render, change the board, re-check | exit 1, naming the moved count |
| aging item with no owner | a 105-day-old unowned item | exit 1, naming the ticket |
| unbuildable graph | a root with no board snapshot | exit 2, never a pass |
| no store of its own | derive all five views over a fixture | the tree is byte-for-byte unchanged |

`--check --against FILE` is how a view proves it is a *projection*: a ticket the
saved view carries but the freshly derived graph does not is caught **by name**.
The PMO writes no cache — it must merely be able to prove that a view it is
handed is not one.

## Boundary

The PMO **coordinates; it never does lane work**. It never merges, never
reassigns an issue, never writes to another lane's files. Every subcommand is
read-only over the projection; `report` output is a *view*, never an authority.
