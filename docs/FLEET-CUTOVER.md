# FLEET-CUTOVER.md — fleet-cron D7: cutover, rollback, decommission

**Status:** normative runbook · Issue [#715](https://github.com/kushin77/agent-orchestrator/issues/715)
(D7 of EPIC [#706](https://github.com/kushin77/agent-orchestrator/issues/706)),
refined by [#902](https://github.com/kushin77/agent-orchestrator/issues/902).
Modelled on `leaderboard/docs/runbooks/MIGRATION_RUNBOOK.md`'s reversible-phase
shape.

**Owner's cutover policy (verbatim intent, #706 / #902):** once the remote
container pair holds **lease capability** (D5, [#713](https://github.com/kushin77/agent-orchestrator/issues/713))
**and** **dual-run parity evidence** (D6, [#714](https://github.com/kushin77/agent-orchestrator/issues/714))
is green, any job already running **locally is allowed to complete**; **no new
local dispatch starts**. Only then is the host crontab **frozen** — commented
via `fleet/cron.py disable`, **never deleted**. `fleet/cron.py enable` is the
rollback. **Decommission** (removing host-side state entirely) happens only
**after a soak** on the frozen state.

## 0. Preconditions — the exact evidence files

Do not proceed past §2 (freeze) until **both** of the following are true. This
tool checks both for you: `python3 fleet/freeze.py status`.

| # | Precondition | Evidence file | How it is verified |
|---|---|---|---|
| 1 | Remote container pair holds single-writer lease capability | Per D5 ([#713](https://github.com/kushin77/agent-orchestrator/issues/713)): a per-job lease under `.board/locks/` (or the `cronrunner` KeyDB `SetNX scheduler:lock:<id>` mechanism it may reuse) — a job that loses the lease skips with a logged reason, distinguishable from a failure | `fleet/freeze.py freeze` refuses to proceed while any local rung (see §3) is still alive; the lease itself is D5's own acceptance evidence, read by the operator before running `freeze` |
| 2 | Dual-run parity evidence is green | `.fleet/parity.json` = `{"ticks": N, "one_writer": true, "diffs": []}` (exact shape committed in D6, [#714](https://github.com/kushin77/agent-orchestrator/issues/714)) | `fleet/freeze.py freeze` reads this file itself and refuses when it is absent, malformed, `one_writer` is not `true`, or `diffs` is non-empty |

`fleet/freeze.py status` prints both checks (live local rung pids, and whether
`.fleet/parity.json` is green) without mutating anything.

## 1. Drain — let in-flight work finish, stop new local dispatch

```bash
python3 fleet/freeze.py drain
```

This writes `.fleet/freeze.flag`. Its existence means exactly one thing: **no
new local dispatch starts.** It does **not** kill, signal, or otherwise touch a
run already in progress — a rung mid-directive is allowed to finish naturally.

`fleet.freeze.refuse_if_frozen(rung)` is the check function any dispatch path
is meant to call before starting new local work for `rung`; it returns a
human-readable refusal string while the flag is set, `None` otherwise.

**Enforced as of #978:** `fleet/watchdog.py` now calls `refuse_if_frozen(rung)`
immediately before every spawn site (`respawn()` and `start_monitor()`) — so
this runbook's claimed behavior ("no new local dispatch starts") is not just
recorded intent, it is checked in code before each respawn/spawn call, with a
negative control (`scripts/check-fleet-freeze.sh`) proving a copy of
`fleet/watchdog.py` with that call removed spawns anyway and is caught. A
refused spawn is logged and does not count against the watchdog's own
bounded-remedy attempt budget; an in-flight rung already running is left
untouched, exactly as before.

**Drain is complete** when `python3 fleet/freeze.py status` reports zero live
local rungs (it checks `brain`, `sister`, `monitor` — the same set
`fleet/control.py` calls `LIVE_RUNGS` — against their recorded heartbeat pids).

## 2. Freeze — commit the cutover

```bash
python3 fleet/freeze.py freeze
```

`freeze` is a **gated** action. It refuses (exit 1, nothing touched) unless
**all** of:

1. `.fleet/freeze.flag` exists (drain was run), **and**
2. no local rung (`brain`/`sister`/`monitor`) is still alive, **and**
3. `.fleet/parity.json` is present and green (`one_writer: true`, `diffs: []`).

Only when all three hold does it shell out to `python3 fleet/cron.py disable`,
which **comments out** (never deletes) the three marked host crontab lines:

```
*/2  * * * *  cd <repo> && python3 fleet/watchdog.py run     >> .fleet/watchdog.log 2>&1 # ao-fleet-watchdog
23   4 * * *  cd <repo> && python3 fleet/prune.py run --apply >> .fleet/prune.log 2>&1 # ao-fleet-prune
*/2  * * * *  cd <repo> && python3 governance/reconcile/cli.py watch --once --apply >> .fleet/reconcile.log 2>&1 # ao-fleet-reconcile
```

After `freeze`, the remote container pair (D5's lease holder) is the sole
writer to shared fleet state (`.fleet/`, `.board/`).

## 3. Every laptop crontab line and its container rung

| Marker | Host line (commented after freeze) | Container rung (owns it post-cutover) | Source of truth |
|---|---|---|---|
| `ao-fleet-watchdog` | `*/N * * * * ... python3 fleet/watchdog.py run` | `fleet/watchdog.py run` inside `agent-fleet-cron` (respawns `brain.py`, `terminal.py`/sister, `monitor.py`) | `fleet/cron.py:line()`, `infra/fleet/inventory.yaml` §1/§2 |
| `ao-fleet-prune` | `23 4 * * * ... python3 fleet/prune.py run --apply` | `fleet/prune.py run --apply` inside `agent-fleet-cron` | `fleet/cron.py:prune_line()` |
| `ao-fleet-reconcile` | `*/N * * * * ... python3 governance/reconcile/cli.py watch --once --apply` | `governance/reconcile/cli.py watch --once --apply` inside `agent-fleet-cron` | `fleet/cron.py:reconcile_line()` |

`fleet/cron.py` is the single owner of all three lines and their markers
(`MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER)`); this table is measured
against that module by `infra/fleet/inventory.yaml`, not restated as a second
copy.

## 4. Rollback

Rollback is symmetric and available at any point after freeze, through the end
of the soak window:

```bash
python3 fleet/freeze.py thaw
```

`thaw` runs `python3 fleet/cron.py enable` (uncomments the three lines,
restoring them **byte-identical**, since `disable`/`enable` only toggle a
leading `# `) and then removes `.fleet/freeze.flag`. Verify:

```bash
python3 fleet/cron.py status   # 3 active line(s)
```

Rollback does **not** require redoing drain — the host lines resume ticking
exactly where `fleet/watchdog.py`'s own idempotency (a missing/stale rung is
respawned; a healthy one is a no-op) leaves them.

## 5. Soak

Decommission never follows freeze directly. Hold the frozen state — remote
sole writer, host lines commented — for a soak window (operator-set; mirrors
the dual-run parity ticks measured in D6) before touching anything further.
During the soak:

- `python3 fleet/freeze.py status` is the read-only health check (flag set,
  zero local rungs, crontab still commented).
- A regression discovered during the soak is `fleet/freeze.py thaw` — the same
  rollback as step 4 — not a partial fix.

## 6. Decommission — only after the soak

Decommission removes host-side state entirely, once the soak has passed with
no rollback:

1. Confirm the soak window has elapsed and no `thaw` occurred.
2. Remove the (still-commented) host crontab lines outright:
   `python3 fleet/cron.py uninstall` (this removes lines carrying the three
   markers, foreign lines untouched — the same selector `disable`/`enable`
   use).
3. Remove the host's fleet worktrees the session-isolation model created
   (`governance/isolation/`) and any lane-local `.fleet/` state that is not
   the shared runtime the container now owns.
4. Remove `.fleet/freeze.flag` (`fleet/freeze.py thaw` also does this, but by
   this point there is nothing left to re-enable — decommission is a one-way
   step, unlike freeze).

Decommission is the only irreversible step in this runbook. Every step before
it (drain, freeze, soak) has a rollback; this one does not, which is why it is
gated on the soak window rather than on the freeze alone.

## 7. Gate: `scripts/check-fleet-freeze.sh`

Wired into `make verify`, refuses when `.fleet/freeze.flag` exists but the host
crontab still has an **active** (uncommented) fleet-cron line — the one state
this runbook says must never occur (drained/frozen intent recorded, but the
host never actually stopped dispatching). See `scripts/check-fleet-freeze.sh`
for the exact check and its negative control.
