# Capability drift — restarting a rung that does not implement what the repository declares

Issue #319. The fleet's rungs are long-lived processes: they load their code once
and keep running it. When a control merges — lane isolation (#263), lifecycle
close-out (#269), orphan reconciliation (#304) — the running loop keeps executing
the pre-merge build, so the control *ships* and is still *not live*. A capability
that ships but is not live is a silently absent control, and a commit comparison
is not enough: `watchdog decide() == "drifted"` says "old build" per rung and
never says **which** capability is missing, so an operator cannot tell "the loop
is old" from "the loop is old and therefore lanes are not being beat, so orphans
will never be flagged".

This is the runbook for that signal. It is gated: `scripts/check-fleet-runbook.sh`
requires this document to keep declaring the three cases and the restart step, and
it provokes a missing capability to prove the report can fail (GR-12 — a check
that cannot fail is a formality).

## The two declarations

**The repository declares the capability set its code provides.** One tuple,
`CAPABILITIES`, in `fleet/channel.py`: each entry names the capability
(`name@version`), the rung(s) that must implement it, the commit that first
provided it (`since`) and the path that implements it (`evidence`). The gate
proves both anchors — `since` is an ancestor of HEAD and it touches `evidence` —
so a capability declared ahead of its code fails the runbook check rather than
promising a control the code cannot have.

**A rung declares the capability set it implements, in its own beat.** The field
is additive and versioned:

```json
{
  "pid": 1234, "state": "working", "commit": "abc1234", "ts": "…",
  "capabilities_version": 1,
  "capabilities": ["steering-channel@1", "lane-isolation@1", "…"]
}
```

Every beat written before this shipped carries no `capabilities` list, and is
still read correctly: such a rung declares itself through the `commit` it beats
with — the one declaration a running build makes about itself — and the watchdog
resolves that commit's capability set from the repository declaration
(`governance/policy/lease.py` keeps the fleet's other declared numbers; this
vocabulary lives in `fleet/channel.py`). Nothing in the heartbeat shape changed
for existing readers: `pid`, `state`, `started_at`, `commit`, `ts` keep their
meaning, and a reader that does not know the new key ignores it.

## The signal — three cases, three remediations

`python3 fleet/watchdog.py capabilities` prints one line per rung; the same
verdict appears in `fleet/watchdog.py run` (the cron pass) and in
`python3 fleet/channel.py status`. Both a drifted rung and a current rung that
misses a declared capability carry the `CAPABILITY STALE` token — with every
missing capability named after it, and with *different* remediations, because
they have different fixes:

| case | what it means | remediation |
| --- | --- | --- |
| `rung DOWN` | no heartbeat from a live loop | start it: `bash fleet/run-fleet.sh`, or respawn just this rung with `python3 fleet/watchdog.py run` |
| `rung on DRIFTED CODE` | live, but running a different commit from HEAD — the report names each capability that commit predates | **restart the rung between runs** so it loads HEAD (see the restart step below) |
| `rung on CURRENT code MISSING a declared capability` | on HEAD and still not implementing a capability the repository declares — the case a commit comparison calls healthy | a restart will **NOT** fix this: the owning lane must wire the capability (or the declaration must be corrected), and the restart then loads the fix |

A rung that is current and missing a capability is reported, never respawned: no
restart adds a capability the build does not have. An unreadable declaration (a
vocabulary version this build does not speak, or a commit that cannot be
resolved) is reported as `capability declaration UNKNOWN` and is **not** counted
as a missing capability — "cannot assess" is never dressed up as "stale".

## The restart step (drifted code)

A restart is safe *between* runs and wrong *during* one: the watchdog deliberately
never restarts a run in flight, since that discards the lane's work. So:

```bash
# 1. what is stale, and which capability each rung is missing?
python3 fleet/watchdog.py capabilities

# 2. restart the rung(s) that are on drifted code — an idle rung is enough:
python3 fleet/watchdog.py run          # respawns missing/stale/drifted rungs, leaves runs alone
#    or restart one by hand, from the shared checkout:
bash fleet/terminal.sh                 # the sister
bash fleet/brain.sh                    # the brain

# 3. re-measure: the report must show `capabilities current` for both rungs
python3 fleet/watchdog.py capabilities
```

`python3 fleet/watchdog.py run` is the same pass cron runs, so a drifted idle
rung is repaired on the next tick without an operator. If a lane has a run in
flight, wait for it to finish (the pass says `left alone`) instead of killing
it — the orphan-reconciliation sweep is the safety net for a lane that dies.

## How this is verified

```bash
bash scripts/check-fleet-runbook.sh     # markers + provokes all three cases (must be able to fail)
make verify                             # the gate of record
```

The runbook check writes synthetic beats into a temporary directory and requires
each case to be reported with its own label and remediation; it reads no live
fleet state and writes none. `scripts/check-fleet-runbook.sh` also proves it can
fail: delete the capability comparison in `fleet/watchdog.py` and the provoked
"missing capability" case stops being reported, which turns the check red naming
the capability that went un-named.
