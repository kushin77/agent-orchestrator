# RCA-0009 — gates stacked on one box, with no lock and no queue

| Field | Value |
|---|---|
| RCA id | `RCA-0009` |
| Incident | `INC-0009` |
| Origin | `event: gate-stacking-2026-09-14` — the committed board snapshot does not carry #724 (see [`RCA-0012`](RCA-0012-stale-snapshot-no-trigger.md)) |
| Severity | `medium` |
| Owner | fleet lane — issue #724 (EPIC #708) |
| Reviewed | `2026-09-14` |

## Impact

The gate of record assumes it is **alone on the machine**, and it is not. Four
sibling lanes on one box means three or four `make verify` runs plus eight gate
processes at once — measured load of ~85–107 — and the gate takes roughly ten
minutes while its siblings run. The damage is not only slowness:

- a long gate is **SIGTERM'd mid-run by a neighbour lane's interrupt**, so a run
  dies at the same step every time (`== python-syntax ==`, which spawns one
  `python3` per tracked `.py` file and prints nothing until it ends). It reads
  exactly like a defect in the change under test, and it is not;
- a terminated run is **CANNOT-ASSESS, not a pass**: the evidence has to be
  re-earned, so the wall-clock cost is paid twice;
- the pressure compounds with the shared tmpfs: one lane's 14.8 GB scratch log
  (`INC-0007`) starved the very gates that were stacking.

## Detection

Late, and only by looking outside one's own lane: counting *other* lanes'
processes (`ps -eo pid,etime,cmd | grep -E "make verify|verify.sh"`) is what
explains a gate that dies at the same step on a pristine change. A single lane
cannot see this from its own output, and nothing in the gate reports that it was
one of four.

## Root cause

`scripts/verify.sh` takes **no lock**. Measured on this tree:

```text
grep -c "flock\|gatelock" scripts/verify.sh Makefile   -> 0, 0
ls fleet/gatelock.py                                   -> No such file or directory
```

The composite gate is a shared, resource-hungry activity on a shared machine,
and it has no mutual exclusion and no queue. Concurrency is therefore decided by
timing rather than by policy: whoever starts first gets the box, and the losers
interfere with each other. The missing control is a single one — one gate per
worktree, with a second invocation either waiting or refusing **by name** — and
its absence is invisible until two lanes happen to overlap.

## Corrective actions

- `CA-0011` — a gate lock: one gate of record per worktree, a second invocation
  serialized or refused by name, so a stacked run is a declared outcome instead
  of an accident, and an interrupted run can say *why* it was interrupted.
  Tracked by **#724**.

## Lessons

`SUGGEST-0007` — **a gate is a resource claim, not just a script.** Anything that
consumes the whole box — the composite gate, a full-corpus lint, a long suite —
must take a lock and name the holder when it refuses; without one, the failure
presents as someone else's flaky test. Recorded as an open suggestion until #724
lands.

## Evidence

- `grep -c "flock\|gatelock" scripts/verify.sh Makefile` → `0`, `0`;
  `fleet/gatelock.py` absent (measured 2026-09-14).
- Measured on the box during the episode: 3–4 concurrent `make verify` runs,
  eight gate processes, load ~85–107, gate wall-clock ~10 minutes.
- Measured failure shape: a run terminated at the same step
  (`make: *** [Makefile:112: verify] Terminated`), repeatedly and independently
  of the change under test.
- Ledger: `INC-0009`, `RCA-0009`, `CA-0011`, `SUGGEST-0007`.

## Follow-up

The incident closes with `CA-0011` (#724). Two related controls are separate
incidents and not merged into this one: the retry storm that multiplies the
stacked runs (`RCA-0010`) and the shared tmpfs that starves them (`RCA-0007`).
