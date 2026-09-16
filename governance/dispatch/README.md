# governance/dispatch — claim-time issue-order enforcement (issue #157)

The chronological-dispatch rule lives in `AGENTS.md` (golden rule 14),
`docs/GOVERNANCE.md` (section 8) and `docs/EXECUTION-PLAN.md` (section 5), and
`scripts/check-chronological-dispatch.sh` gates that those documents keep
declaring it — and (since #726) that the dispatch arbitration actually refuses a
unit whose issue → epic → lane proof does not hold.

This package is the **behavioural** half. It refuses a claim on an issue that is
not the next step in the active dependency chain, so the board behaves like an
execution plan instead of a backlog to browse.

## The rule, mechanically

An issue is claimable only when one of these holds:

| Reason | Meaning |
|---|---|
| `next-in-milestone` | It is the frontier of the active milestone: the lowest-numbered open, unblocked, unclaimed **non-epic** issue in that milestone. |
| `child-of-claim` | It declares `Parent: #n` and the agent already holds #n. |
| `successor-of-claim` | It declares `Blocked-by: #n` and the agent already advanced #n. |
| `active-epic-child` | It declares `Parent: #n` and #n is the **active epic** (`.board/focus.json`, epic #707). Stricter than the frontier, never a relaxation. |
| `brain-directed` | A brain directive recorded in `.fleet/sent` names exactly this issue (`claim --directive <id>`). The brain is the chain. |

Everything else is refused with a reason: `unknown-issue`, `issue-closed`,
`epic-closed`, `blocked`, `already-claimed`, `epic-not-workable`,
`provenance-mismatch`, `unowned`, `out-of-epic-pooled` (outside the active epic —
parked, see below), or `no-chain-edge` — the last being kanban scavenging. Epics
are never claim targets: an epic closes with its children.

## The out-of-epic pool (#707 lane F6 / #721)

Epic focus concentrates the fleet on one epic. That is only honest if the work it
defers is **parked**. When an active focus makes an issue out-of-epic, the claim is
refused `out-of-epic-pooled` **and** a record is appended to
`.board/pool.jsonl`:

```json
{"issue": 902, "reason": "out-of-epic", "at": "2026-09-14T00:00:00Z"}
```

* The check sits **before** the milestone-frontier branch in `order.eligible`, so an
  out-of-epic issue is never dispatched on the frontier — a frontier that
  interleaves epics is the incoherence focus exists to remove.
* A `Blocked-by:` blocker of an active-epic child is promoted **just-in-time**
  through the *existing* `claim --directive <id>` path (reason `brain-directed`).
  No new authorisation mechanism is invented.
* When the resolver returns `None` (no workable epic) the pool **drains** and the
  drained numbers are returned/reported — a pooled issue is never silently
  dropped.
* `governance/dispatch/cli.py pool` prints the rail; `pool --self-control` proves
  the reader rejects a malformed line and that a drain reports what it drained, so
  the behaviour is gate-enforced by `scripts/check-epic-focus.sh`.

The rail is runtime state and is gitignored (`.board/pool.jsonl`), like
`.board/locks/` — the decision log is the branch/PR history.

## A2A dispatch arbitration (#726)

A directive names an issue, but a name is not ownership: nothing bound the issue
to the epic that owns the work or to the lane that holds it, so a directive could
be dispatched for a closed issue or under a closed epic, and two lanes could drive
one issue. A message between agents is only safe if the receiver can prove who
owns the work, so dispatch **proves issue → epic → lane** and refuses a unit whose
proof does not hold — naming the evidence it checked.

```bash
python3 governance/dispatch/cli.py dispatch --issue 726 --agent me --lane governance
```

`dispatch` is read-only (it takes no claim and writes no ledger entry), so it is
safe to run before provisioning a lane:

```
$ python3 governance/dispatch/cli.py dispatch --issue 724 --agent me --lane governance
{
  "verdict": "granted",
  "issue": 724,
  "agent": "me",
  "lane": "governance",
  "epic": 708,
  "directive_id": "",
  "provenance": {
    "issue": 724,
    "epic": 708,
    "lane": "governance",
    "evidence": [
      ".board/snapshot.json (source kushin77/agent-orchestrator, generated 2026-09-14T18:00:00Z, sha256 1a2b3c4d5e6f) issue #724 state=open parent=#708 milestone=M26",
      "epic issue #708 state=open milestone=M26",
      "lane 'governance' named by the dispatch",
      "directive: none (claimed as part of the active chain)",
      "ledger .board/claims holds no live claim on #724"
    ]
  }
}
dispatch granted: #724 -> epic #708 -> lane governance
```

`claim` runs the same arbitration before it writes, so the refusal cannot be
sidestepped by calling the mutation directly. Each refusal names what it read:

| Refusal | Refused when | Evidence named |
|---|---|---|
| `issue-closed` | the issue is closed in the board | the snapshot (source, generation, digest) and the issue's `state` / `closed_at` |
| `epic-closed` | the issue declares a `Parent:` edge to a closed epic | the same, for both the issue and the epic |
| `already-claimed` | a live claim holds the unit (another agent, or another lane) | the holder's agent **and lane**, since when, plus the ledger and lock path |
| `unowned` | the dispatch and the directive both name no lane | the empty lane and the directive that would have declared one |
| `provenance-mismatch` | the directive declares an epic or lane the board does not corroborate | the directive file, the declared `task.epic` / `task.lane`, and the board's own edge |
| `blocked` / `unknown-issue` / `epic-not-workable` | the issue is blocked, absent, or is itself an epic | the snapshot and the issue fields read |
| `snapshot-stale` | the board is older than `--stale-minutes` (fail closed, exit 2) | the snapshot's age and how to refresh it |

Exit codes stay tri-state: `0` granted, `1` refused, `2` CANNOT-ASSESS (missing or
stale board). `unowned` applies at the dispatch seam, where a unit no lane owns
must be refused; the legacy `claim` path keeps its documented empty `--lane`
default (`fleet/terminal.py` always passes one), so the same arbitration serves
both without changing the claim contract.

**Provenance is recorded.** A granted arbitration writes an `issue -> epic -> lane`
record onto the claim — linked to the directive that authorized it — so the
owner of a unit is readable from the ledger afterwards:

```json
{"event": "claim", "issue": 726, "agent": "me", "lane": "governance", "reason": "brain-directed",
 "directive_id": "d-1", "provenance": {"issue": 726, "epic": 708, "lane": "governance",
 "evidence": ["...the board, the epic, the lane, the directive, the ledger..."]}}
```

The audit re-derives the provenance invariants from the ledger it reads: a
recorded provenance must agree with the board's `Parent:` edge and with the claim's
own lane, and a claim still **live** that was taken by a directive
(`brain-directed`) must record one. Absence is not judged on settled history:
records written before #726 carry no provenance, and an audit that retro-blames
them cannot be green on its own repository.

## Claim protocol

```bash
python3 governance/dispatch/cli.py status                        # active milestone + frontier
python3 governance/dispatch/cli.py eligible --issue 139 --agent me
python3 governance/dispatch/cli.py dispatch --issue 139 --agent me --lane knowledge-index
python3 governance/dispatch/cli.py claim --issue 139 --agent me --lane knowledge-index
# ...do the work, open the PR...
python3 governance/dispatch/cli.py release --issue 139 --agent me
```

Exit codes follow the repo tri-state convention: `0` OK, `1` refused/NOT-OK,
`2` CANNOT-ASSESS (for example the snapshot is missing **or stale** — past
`--stale-minutes`, default 15 minutes).

* **Claim record** — one JSON object per event. New events are written **one file
  per event** into `.board/claims/` (atomic, collision-proof), so two concurrent
  lanes never touch the same path and a git conflict on the ledger is impossible
  by construction. The pre-#170 single-file ledger `.board/claims.jsonl` is
  frozen history, read first so replay stays time-ordered. The record is the
  evidence that the claim was order-checked.
* **Single-claim lock** — `.board/locks/<issue>.lock` is created
  `O_CREAT|O_EXCL`, so a second claim on an in-flight issue fails loudly instead
  of two agents duplicating work. A claim whose TTL elapsed may be taken over,
  so a dead agent cannot wedge the chain. Lock files are runtime state and are
  ignored by git.
* **Board snapshot** — `.board/snapshot.json` is the committed board state the
  gate audits against. Refresh it with
  `python3 governance/dispatch/cli.py snapshot --from-github` (the only
  network-touching path in this package); the gate itself is offline.

## Snapshot staleness (#170)

The snapshot is refreshed only by an explicit command, so it can go stale while
the live board moves on — a stale frontier sends an agent at finished work, or
refuses a legitimate claim as out of order. `status`, `eligible` and `claim`
print the snapshot's age on every run and **fail closed** past the threshold:

```
$ python3 governance/dispatch/cli.py eligible --issue 139 --agent me
eligible: snapshot age 19.0m (threshold 15m)
eligible: CANNOT-ASSESS — snapshot-stale (19.0m > 15m) — refresh first: python3 governance/dispatch/cli.py snapshot --from-github
# exit code 2
```

A stale snapshot yields `CANNOT-ASSESS` (exit 2), never a verdict; a stale
`claim` is refused with reason `snapshot-stale`. The threshold is
`--stale-minutes` (default 15). The offline `audit` is deliberately **not**
staleness-gated: it judges committed history against the committed snapshot, and
`make verify` runs without the network.

## Chain markers

Dependency edges are declared in the issue body, so a chain is explicit rather
than guessed:

```text
Parent: #138        Part-of: #138        (same edge, alternate spelling)
Blocked-by: #139, #140
```

An issue with no markers is still reachable as its milestone frontier; it is
never reachable merely because it is visible.

## The gate

```bash
bash scripts/check-issue-claims.sh      # 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
```

`make issue-claims` runs the same check; it is part of `make verify` and
`make lint`.

The audit re-derives the **time-stable** invariants — record schema, declared
chain edges, milestone membership, blockers, the single-claim lock, release
pairing, epics, expired-but-unreleased claims, and the recorded provenance. Strict
frontier *ordering* is enforced live at claim time by `order.eligible`, because a
historical frontier cannot be recomputed from today's snapshot.

The audit always runs its own mutants first: scavenged claim, blocked claim,
closed claim, epic claim, duplicate claim, double release, unsupported reason,
provenance the board contradicts, a routed claim that recorded none, and a
malformed record must all be **rejected**, while a valid frontier claim and a valid
child claim must be **accepted**. If the audit cannot fail, the gate fails
(GR-12 / AO-GR-19: a check that cannot fail is a formality).

The **dispatch** refusals are controlled the same way, and by the same `audit`
run: `arbitration_self_control()` provokes every reason in
`ARBITRATION_REFUSALS` — each with the evidence it must name — and refuses to pass
if a reason has no provoked refusal, if a refusal stops quoting its evidence, or if
a valid unit is no longer granted. The controls run in a temporary directory and
rebind the sent mailbox only for their own run, so they touch no repository state.

`scripts/check-chronological-dispatch.sh` (the gate named by #726's `Verify:`) is
this rule's other half: it keeps the *declaration* checks on the contract documents
and adds a behavioural section that drives the `dispatch` CLI against a fixture
board in a temp dir — a closed issue, a directive aimed at a closed issue, an open
issue under a closed epic, a unit another lane holds, an unowned unit and a stale
board must all be **refused** (naming their evidence), and a dispatchable unit must
still be **granted with its provenance**.

## The owner's committed queue (#928)

`order.eligible`/`claims.arbitrate` refuse an out-of-order claim only when
`Issue.blocked_by` is populated — but nothing populated that edge from the
owner's actual intended order (posted as an advisory comment on #878,
"Owner queue 2026-09-16"). `governance/dispatch/owner_queue.py` closes that gap: it
is a new EDGE SOURCE, not a new refusal reason.

`governance/dispatch/queue.yaml` is the single committed source of truth —
waves of issue numbers, in forced order:

```yaml
waves:
  - name: wave-1-epic-878-lanes
    issues: [880, 881, 882, 883, 884, 885, 886, 887, 888, 889, 890]
  - name: wave-2-epic-706-residual-and-golive
    issues: [902, 619, 731, 732]
blocked_by: {}   # optional explicit overrides, unioned on top of wave edges
```

At claim/eligibility time (`cli._load_snapshot`, which every verb goes
through), the queue is loaded and its IMPLIED order is unioned into
`Issue.blocked_by` **before** the existing refusal path runs:

* every issue is blocked by every EARLIER issue in the same wave that is
  still open;
* every issue is blocked by every issue of an EARLIER wave that is still
  open;
* only OPEN blockers are added — a closed issue drops out of the chain
  automatically, so the queue self-shortens as the owner's real work lands.

A `claim` on an out-of-order issue is refused with the existing `blocked`
reason, plus a `queue:` detail naming the blocking issue(s):

```
$ python3 governance/dispatch/cli.py claim --issue 889 --agent me --lane hermes
claim REFUSED: blocked — #889 is blocked by #880, #881, ..., #888 — evidence: ...
  (queue: #880, #881, ..., #888 precede(s) #889 in governance/dispatch/queue.yaml)
```

### `queue` subcommand

```bash
python3 governance/dispatch/cli.py queue --next    # the next claimable issue(s) per the queue
python3 governance/dispatch/cli.py queue --check   # validate the file (see below)
```

`--check` validates `governance/dispatch/queue.yaml` structurally (no
duplicate issue numbers, no cycle in the `blocked_by` graph — including
overrides) and, against a FRESH `.board/snapshot.json`, that every queued
number exists and is open. Like every other verb here, `--check` is
tri-state and fails CLOSED on a stale board: past `--stale-minutes` it
reports `CANNOT-ASSESS` (exit 2) for the exists/open half rather than a false
verdict, while the structural half (duplicates, cycles) still runs — that
half needs no board at all.

### The gate

```bash
bash scripts/check-dispatch-queue.sh   # part of `make verify` (dispatch-queue)
```

Runs `governance/dispatch/tests/test_queue.py` (out-of-order claim refused,
in-order accepted, closed issues drop out of the chain, `--check` catches a
cycle), validates the committed queue against the committed snapshot, and
PROVOKES the cycle detector with a fixture queue whose `blocked_by` overrides
form a 2-cycle — it must be refused by name (`cycle`), while a clean fixture
of the same shape must still pass (GR-12 / AO-GR-19: a check that cannot fail
is a formality).

## Layout

| File | Role |
|---|---|
| `model.py` | Issue / Snapshot / Eligibility / ClaimEvent / Provenance / Arbitration dataclasses and reason codes |
| `snapshot.py` | Snapshot build, load, hash, chain-marker parsing, `gh` fetch |
| `order.py` | Eligibility rules (including a closed epic) and frontier/milestone resolution |
| `claims.py` | Ledger (directory + frozen legacy file), single-claim lock, TTL take-over, snapshot-staleness refusal, A2A arbitration, audit, both self-controls |
| `cli.py` | `status` / `eligible` / `dispatch` / `claim` / `release` / `held` / `reap` / `snapshot` / `audit` / `queue` |
| `owner_queue.py` | Owner queue (#928): loads `queue.yaml`, overlays its implied `blocked_by` edges, validates, reports next-claimable |
| `queue.yaml` | The owner's committed, ordered wave queue (#928) |
| `tests/` | Eligibility, lock, TTL, audit, arbitration, the CLI refusal seam, the owner queue (`test_queue.py`) and the anti-formality controls |
