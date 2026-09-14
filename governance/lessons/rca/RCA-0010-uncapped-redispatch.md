# RCA-0010 — an uncapped retry stacked 137 gates and starved the box

| Field | Value |
|---|---|
| RCA id | `RCA-0010` |
| Incident | `INC-0010` |
| Origin | `event: uncapped-redispatch-2026-09-14` — the committed board snapshot does not carry #723 (see [`RCA-0012`](RCA-0012-stale-snapshot-no-trigger.md)) |
| Severity | `high` |
| Owner | fleet lane — issue #723 (EPIC #708) |
| Reviewed | `2026-09-14` |

## Impact

A retry loop turned one interrupted gate into a machine-wide outage. Measured at
the time: **137+ `verify.sh` processes** on the box, from **two** orphaned
worktrees holding **68 and 69** copies each, with every lane's gates stalled at
~17 minutes elapsed. The gate of record was not slow — it was *multiplied*, and
the multiplication was caused by a previous session's watcher that re-invoked
`make verify` after each termination, stacking one new `verify.sh` per attempt.

The diagnostic trap is the lasting damage: **no driver process remained**. The
loop self-perpetuated through the `make` → `verify.sh` fork chain, so `ps` showed
no runaway parent — only a busy box. A lane reading its own output concludes "the
machine is slow", and a lane reading its own gate concludes "my change is
broken".

## Detection

Late, and by process census rather than by any gate:

```text
pgrep -f verify.sh | wc -l                      -> 137+
readlink /proc/<pid>/cwd | sort | uniq -c       -> the storm is ONE worktree
```

The per-worktree count is the tell: `verify.sh` firing 68 times from a single
checkout is not load, it is a loop. Nothing counted redispatch attempts, so
nothing noticed the loop until a human counted processes.

## Root cause

The dispatch path **retries without a cap**. Measured on this tree:

```text
grep -rn "redispatch\|max_attempts\|retry_cap" fleet/*.py governance/dispatch/*.py
  -> no matches
ls fleet/runaway.py   -> No such file or directory
```

Three properties of that design produced the outage:

- **No bound.** A failed or interrupted dispatch is retried unconditionally: not
  capped by count, not by elapsed time, and not by the identity of the
  dispatcher.
- **The effect is the state.** The loop survived the death of its driver because
  each retry *created a new process*; the loop was stored in the process table,
  not in a variable, so killing the parent freed nothing.
- **No attempt ledger.** With no record of attempts, a storm is
  indistinguishable from concurrency — and it is attributed to "the box is busy"
  instead of to a specific lane's retry.

## Corrective actions

- `CA-0012` — a cap on redispatch in the runaway guard (`fleet/runaway.py`,
  issue **#723**): a bounded attempt count per dispatch with an attempt ledger,
  a terminal refusal when the cap is reached, and a refusal that names the loop's
  owner so an orphaned storm is attributable without a `ps` census.

## Lessons

`SUGGEST-0008` — **a retry loop is a resource claim that must be bounded, and its
bound must be visible.** Retries need a cap (count and time), an attempt ledger,
and a terminal refusal; and because a loop can outlive its driver, the refusal
must name the dispatcher rather than the process. A box that is "just busy" is
unfalsifiable until someone counts processes per worktree — which is the
measurement this incident says must be a gate, not a diagnosis. Recorded as an
open suggestion until #723 lands.

## Evidence

- Measured during the episode: `pgrep -f verify.sh | wc -l` → 137+;
  per-worktree copies 68 (`ao-366-d9af928c`) and 69 (`ao-287-d9caf9d1`);
  `readlink /proc/<pid>/cwd | sort | uniq -c` attributing the storm to one
  checkout.
- `fleet/runaway.py` absent, and no `redispatch` / `max_attempts` / `retry_cap`
  vocabulary in `fleet/*.py` or `governance/dispatch/*.py` (measured
  2026-09-14).
- Ledger: `INC-0010`, `RCA-0010`, `CA-0012`, `SUGGEST-0008`.

## Follow-up

The incident closes with `CA-0012` (#723). The retry cap bounds the storm; it does
not serialize concurrent gates (`RCA-0009`) nor stop a single lane from filling
the shared tmpfs (`RCA-0007`) — each is its own control with its own issue.
