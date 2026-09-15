# `governance/spawn` — one spawn envelope, one producer (issue #793)

`governance/**` is a large, well-tested surface — claim ledger, lane isolation,
lifecycle close-out, reconcile, runaway guard, capacity, gate admission — and
**none of it was applied by the act of spawning**. The remote path inlined
governance as *prompt prose* inside `fleet/terminal.py::build_prompt`, where
nothing could check that a spawn had carried it; the local path shared none of
it. The measured consequence: nine worktrees running a gate, eight of them
running exactly one, and **one running twenty-six**. AO-GR-22 ("at most one
composite gate per worktree") held for eight lanes and failed silently for the
ninth, because a rule that lives only in a document cannot refuse anything.

This package makes the envelope the thing a spawn carries.

## The shape

`governance/spawn/model.py` defines one versioned document
(`spawn-envelope/v1`) with eleven required fields:

| field | what it is | read from |
|-------|------------|-----------|
| `issue` | the unit of work | the directive |
| `lane` | one issue = one lane = one branch | the directive |
| `worktree` | the isolated lane worktree | `governance/isolation` |
| `session` | the minted identity: id, branch, agent, repo slug, author | `governance/isolation` |
| `trailer` | `Refs <owner>/<repo>#<issue>`, on every commit | derived from the session |
| `claim` | who holds the issue, and on which lane | the claim ledger (`governance/dispatch/claims.py`) |
| `focus` | the issue's epic, plus the pinned focus it runs under | the board snapshot + `.board/focus.json` |
| `capacity` | the fan-out admission **and the gate permit** it will take | `fleet/capacity.py` + `fleet/gatelock.py` |
| `budget` | the attempt budget that bounds the spawn | `fleet/runaway.py` (issue #723) |
| `gate` | the gate of record and the one-gate bound (AO-GR-22) | `fleet/gatelock.py` |
| `verify` | the issue's own `Verify:`, and which source produced it | the issue body, else the gate of record |

`capacity.assessed` is part of the document on purpose: an *unmeasurable* ceiling
is refused, never rounded up to "probably fine" — the same posture
`fleet/capacity.py::admit` takes when it holds every lane.

## The two spawn paths, one document

```
fleet/terminal.py::build_prompt ─┐
                                 ├─► governance/spawn/produce() ─► document ─► render.prompt()
governance/spawn/cli.py open ────┘
```

* **Remote.** `fleet/terminal.py::build_prompt` supplies the data it already
  holds (the directive, the minted `AO_*` environment, the worktree, the context
  pack's issue body) and renders the envelope. It holds **no** governance prose:
  `scripts/check-spawn-envelope.sh` fails by name if a copy reappears.
* **Local.** `governance/spawn/cli.py open` claims the issue through
  `governance/dispatch` (against its own root's board), mints the lane through
  `governance/isolation`, assembles the envelope from the same producers, writes
  it beside the run markers, and prints the block the subagent is spawned with.
  `--root` scopes the whole spawn, so a caller can drive it against a scratch
  tree; `--json` prints the document instead of the block.

## Refusal is a precondition, not a warning

`model.assemble` refuses a document it cannot validate, and it reports **every**
field at fault at once, each by name (`capacity.permit.store: missing`), so one
pass tells a caller everything to fix:

```
$ python3 governance/spawn/cli.py open --issue 793 --agent me --lane my-lane
spawn: REFUSED — claim.owner: empty
spawn: the spawn is refused (rc 78); nothing was spawned. Fix every field above and re-run.
```

Exit codes: `0` OK / `1` NOT-OK / `2` CANNOT-ASSESS / **`78` REFUSED**. The
refusal is deliberately outside the tri-state, so nothing can read a refused
spawn as a pass, a failure, or a skip. It matches `fleet/terminal.py::RC_REFUSED`,
so the loop and the CLI refuse alike — and the loop refuses **before any child
exists**, counting the attempt against the directive's budget (#723) so a
permanently malformed order is retired instead of re-dispatched forever.

`check --without <field>` is the documented provocation of that contract.

## A run in flight is the marker's own evidence

`fleet/watchdog.py::run_in_flight()` used to ask whether a run marker's `pid` was
alive. That `pid` is the **loop's**, and a loop outlives every run it dispatches,
so a crashed run's leftover marker read as "in flight" for as long as the loop
lived. Measured: four markers ~5.6 hours old, every one with `child_pid: null`,
each naming the live sister loop — the drift lock was held open on every tick,
the sister's own heartbeat (`state: idle`) was ignored for that decision, and the
rung stayed on pre-#723 code **with no attempt budget**, re-dispatching without
bound.

`governance/spawn/liveness.py` decides on the marker's own evidence instead: a
live `child_pid`, or a beat no older than `AO_RUN_STALE_SECONDS` (default 120s)
that the run's own beater advanced. A marker with neither is a crashed run and
does not hold the lock. A heartbeat that says `idle` beside a live child is
**reported** on the pass — the marker still wins, because a live child is real
work, but the disagreement is named rather than resolved in silence.

## Proof

`scripts/check-spawn-envelope.sh` (in `make verify`) drives the real entrypoints
and provokes every refusal; `governance/spawn/tests` carries the unit-level
proofs, including the exact-equality check that the fleet prompt **is** the
envelope's rendering.
