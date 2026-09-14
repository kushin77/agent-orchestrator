# `routines` — the fleet's schedule as projected routine objects (issue #418)

Upstream paperclip.ing models **routines** — scheduled recurring work — as a
first-class object: a **trigger**, its **params**, and an **owner**. The fleet
already owns the mechanism: `fleet/cron.py` is the code-native scheduler (GR-15:
there are no GitHub Actions) and its marked crontab lines *are* the schedule.
What the fleet had no object for is the routine an operator can name, see and
reason about. This adapter publishes it.

The EPIC's rule is decisive and this adapter holds it: a routine is a
**projection of the code, never a second copy of it**. There is no schedule in
this package — no cadence literal, no command, no log target. Every one of those
is read from `fleet/cron.py` at projection time.

```bash
python3 integrations/paperclip/adapters/routines/cli.py project   # the routine view (JSON)
python3 integrations/paperclip/adapters/routines/cli.py verify    # determinism + drift re-check
python3 integrations/paperclip/adapters/routines/cli.py registry  # identity/owner only
bash scripts/check-paperclip-routines.sh                          # the gate
```

Exit-code contract: **0 OK / 1 NOT-OK / 2 CANNOT-ASSESS**. CANNOT-ASSESS (an
input that cannot be read at all) is never reported as a pass, and neither state
is ever a silent success.

## What is projected

| Source (`fleet/cron.py`) | Routine | Trigger | Owner |
|---|---|---|---|
| `line(interval)` — `# ao-fleet-watchdog` | `fleet-watchdog` | interval, every 2 min | `fleet/watchdog` |
| `prune_line()` — `# ao-fleet-prune` | `fleet-prune` | daily at 04:23 | `fleet/prune` |
| `reconcile_line(interval)` — `# ao-fleet-reconcile` | `fleet-reconcile` | interval, every 2 min | `governance/reconcile` |

The **trigger** and the **params** (`argv`, `log`, `cwd`) come from the parsed
cron line. The **owner**, the **lane** and the **anchor** ticket come from the
routine registry in `model.py` — the one thing the code has no field for. The
registry deliberately carries no schedule.

The install interval is **read from the code too**: `fleet/cron.py` declares it
once as the `--interval` default on its `install` subparser, so the adapter asks
the parser rather than restating `2`.

## The refusal and drift cases

| Case | Outcome |
|---|---|
| a marked line in `fleet/cron.py` with no routine in the registry | `schedule-unprojected` — **reported** (the schedule drifted without a routine change) |
| a routine whose schedule the code does not declare | `routine-orphan-schedule` — **refused** (the code is the schedule, never a duplicate of it) |
| the same marker scheduled more than once | `duplicate-marker` — **refused** (a routine has exactly one schedule) |
| a routine with no owner | `routine-unowned` — **fails closed** |
| a cron expression that is not `*/N * * * *` or `M H * * *` | `inexpressible-trigger` — **refused by name** |
| a routine whose lane/owner disagrees with the PMO graph | `pmo-lane-disagreement` / `pmo-owner-disagreement` — **refused by ticket** |
| `fleet/cron.py` missing or unimportable | **CANNOT-ASSESS** (exit 2) |

## Agreeing with the PMO view, not becoming a second answer

"What is scheduled and who owns it" is the same program-management truth the
rollup reports, so the adapter does not derive an owner of its own where the PMO
already has one. `pmo.py` reads the **PMO's own graph**
(`governance/pmo/graph.py` over `governance/ticket`, ADR-0014) and, for the
ticket each routine is anchored to:

* **refuses** the routine when the graph derives a different lane or names a
  different owner (`pmo-lane-disagreement`, `pmo-owner-disagreement`);
* **renders a note** — not a failure — when the committed graph does not carry
  that ticket (`anchor-unauditable`), the same honesty the PMO's own `aging`
  view uses for a ticket it cannot date.

The gate asserts the agreement actually **ran** on the real root (the committed
graph was read), so it can never silently degrade to `pmo-unavailable`.

## Determinism

Same revision → same bytes, from any checkout: absolute paths are normalised to
`<ROOT>` / `<FLEET_DIR>`, routines are emitted sorted by marker, findings and
notes are sorted, and the document is `json.dumps(..., indent=2, sort_keys=True)`.
The gate proves it by deriving twice and comparing the bytes.

## No store

The projection writes only its own stdout. It adds **no ledger, no cache and no
second source of `status`**: the schedule is read from the code on every run and
the PMO graph is rebuilt in memory. The gate proves it by hashing the tree before
and after deriving and verifying every routine — the tree is unchanged.

## Falsifiability

`scripts/check-paperclip-routines.sh` does not merely assert these properties, it
**provokes** them (GR-12): an entry deleted from a scratch copy of the schedule,
an entry added with no routine change, an inexpressible trigger, an owner-less
routine, a lane that disagrees with the graph, an owner that disagrees with the
graph, a non-deterministic render, and a written store. Each control must be
refused **by name**; if any is accepted the gate reports FAIL. The mutation proof
(`mutate → gate must go red naming the offender → restore → byte-identical`)
confirms each control is load-bearing rather than decorative.

## Boundary

This package is an `adapters/<family>/` of the canonical module
`integrations/paperclip/`; it lives **over** the seam (`client`, `mapping`,
`model`) and the seam never imports it. It never writes `fleet/**` — `fleet/cron.py`
stays the schedule's single owner — and never adopts an upstream-style scheduler
as its authority (ADR-0012).
