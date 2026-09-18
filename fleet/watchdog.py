#!/usr/bin/env python3
"""Fleet watchdog — the cron-owned keeper of the brain/sister loops.

Run by cron (installed with ``python3 fleet/cron.py install``) or by hand
(``python3 fleet/watchdog.py run`` / ``--force``). It respawns a missing, stale or
drifted loop rung, ensures the monitor rung (``fleet/monitor.py``) is running,
and does nothing when the fleet is healthy, so a cron tick is cheap and
idempotent.

Safety, the one rule a watchdog must never break: **a run in flight is never
restarted just to update code.** A *missing* loop is respawned regardless — its
run is already orphaned and the fresh loop self-heals the claim. A *drifted*
(healthy but old-code) dispatcher is respawned only when idle.

Every rung this module spawns is started detached with its stdout+stderr
appended to a per-rung capture log, `.fleet/<rung>.log`. Spawning used to send
both streams to `DEVNULL`, so the director — the middle rung of the hierarchy — was
unobservable from anywhere: no window, no log, no way in. The watchdog is the
spawn authority, so the capture path is defined here and read by
`fleet/console.py` (the dashboard) and `fleet/run-fleet.sh` (the tail windows).

Respawn is a *measurement*, not a claim (issue #276). "`spawn()` returned" is not
"the rung came up": the watchdog now waits, bounded, for the rung's process to
appear and survive a short settle window, so a no-op spawn or a rung that dies at
startup (a singleton refusal, a crash) reports `RESPAWN FAILED` and the pass exits
non-zero instead of fabricating a success.

The pass measures a second thing (issue #319): whether the running rung implements
the capabilities `fleet/channel.py` declares for it. A rung on an old commit
reports `CAPABILITY STALE` naming each missing capability — "the loop is old" and
"the loop is old, so lanes are not being beat and orphans will never be flagged"
are different facts, and only the second tells a principal what is absent. A rung
that is *current* and still missing a declared capability is reported and never
respawned: no restart adds a capability the build does not have. `python3
fleet/watchdog.py capabilities` prints that report on its own.

**The remedy is bounded (issue #773, AO-GR-21).** Drift detection is only half a
control; the other half is an action that can actually change what was compared.
Measured 2026-09-15: `#739` made the watchdog compare the running commit against
`origin/master`, and on a mismatch it respawned — but a respawn re-executes the
*same checkout*. When the drift was the checkout being behind, the watchdog took
an action that could not change the compared value and repeated it without bound:
**132 `drifted … — respawned` decisions, 45 clean stops, a director process never
older than 60s, and no work done at all.**

So this module now separates the two cases by name and bounds every remedy:

* **`drifted`** — the running commit is *not* the local HEAD: respawn loads HEAD.
* **`checkout-behind`** — the running commit *is* the local HEAD while
  `origin/master` is ahead: the rung is current relative to the checkout and the
  **checkout** is stale, so the remedy is a **fast-forward** (`git fetch` +
  `git merge --ff-only`), never a blind respawn.

Every remedy is recorded per rung under `.fleet/watchdog/` and is bounded by an
attempt cap with exponential backoff (the same harvested contract as
`fleet/runaway.py`). A remedy that does not change the observed state is **not
retried forever**: after the cap the watchdog **escalates once**, naming both
commits and the checkout, and **parks** the rung — it never retries it again until
a principal rearms it (`python3 fleet/watchdog.py rearm --rung <name>`) or the
rung's state changes on its own. A drifted rung that is busy is *recorded* as
pending drift and acted on when the run completes, instead of being dropped every
tick.

**And the remedy has to be reachable (issue #780).** #773 gave the
`checkout-behind` case its correct remedy, and that remedy could not run: cron
executes `python3 fleet/watchdog.py run` **from the shared checkout**, so the
watchdog in flight is whatever copy the checkout holds — and when the checkout is
the stale side, the running watchdog *is* the pre-fix watchdog, whose remedy is
the code it cannot see. Measured 2026-09-15: the shared checkout sat 5 commits
behind (`e9cfc10` merged, `HEAD` `b95a8b7`), the dispatcher ran `592b132` for ~5.5
hours, and #773's own evidence records a **human** doing the fast-forward.

Two circularities, and this module now closes both:

1. **Availability** — the remedy must not be read out of the checkout it repairs.
   `self_freshness` answers "am I current?" by comparing THIS FILE's blob at
   `origin/master` (`git rev-parse origin/master:fleet/watchdog.py`) against the
   working tree's (`git hash-object`), and `bootstrap_checkout` performs the move
   with `git fetch` + `git merge --ff-only` as *git subprocesses*. Neither the read
   nor the move depends on the content of the loaded module — they depend on
   `git`, which lives outside the checkout. That is the small stable kernel #780
   asks for, and its body is kept tiny on purpose: a stale copy that has it can
   always run it.
2. **Durability** — a process that fast-forwards the checkout has already IMPORTED
   the old module, so the rest of its pass would still be the code it just
   replaced, and the repair would never become the running code.
   `bootstrap_preflight` therefore `execv`s a fresh interpreter onto the
   now-current file and re-runs the same verb, bounded by `ENV_BOOTSTRAPPED` to
   one re-exec per invocation — never a loop.

The residual is stated rather than hidden: a checkout whose copy of this module
predates this kernel cannot bootstrap itself, because the kernel has to be
installed once. That is why the kernel is tiny and why the pass reports its
freshness VERDICT — not its age — naming both commits and both blobs.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet"))

import channel  # noqa: E402
import freeze  # noqa: E402  (#978 — refuse_if_frozen before every spawn)
import gatelock  # noqa: E402  (RCA-0007 — the proactive gate-lock health sweep)
import lease  # noqa: E402  (#977 — single-writer lease around the tick)
import runtime  # noqa: E402

from governance.spawn import liveness as spawn_liveness  # noqa: E402  (#793)

#: The watchdog tick's own job lease (#977, issue #706 D5). Deliberately a
#: distinct path from `singleton.py`'s `.fleet/<rung>.lock` — those guard the
#: long-lived brain/sister loops for their whole lifetime; this guards one
#: `watchdog_once()` pass so two replicas of the fleet-cron pair never spawn
#: or fast-forward the same rung at once. `AO_FLEET_LOCK_BACKEND` (default
#: `fcntl`, GR-28) picks the backend, same as everywhere else `lease.py` is
#: used.
WATCHDOG_LEASE_JOB = "watchdog"
#: 2x the bounded settle/verify window a tick can take (issue #977's "ttl =
#: 2x the job's own timeout" convention) — generous enough that a slow but
#: healthy tick never loses its own lease mid-pass.
WATCHDOG_LEASE_TTL_SECONDS = 600.0


class RespawnRefused(Exception):
    """`respawn()`/`start_monitor()` refused because the fleet is frozen (#978).

    Raised — not folded into the plain `False` "spawn failed" result — so a
    caller can tell "no new dispatch started because the fleet is draining"
    (not a failure; the tick should stay quiet) from "the spawn itself broke"
    (a real `RESPAWN FAILED`, which trips the pass to NOT-OK)."""

RUNGS = (
    ("brain", "fleet/brain.py", "fleet/brain.sh", channel.BRAIN_HEARTBEAT),
    ("sister", "fleet/terminal.py", "fleet/terminal.sh", channel.HEARTBEAT),
)
MONITOR_PATTERN = "fleet/monitor.py"
MONITOR_NAME = "monitor"
FLEET_DIR = runtime.FLEET_DIR
RUNS_DIR = FLEET_DIR / "runs"

# Respawn verification window (issue #276). After spawning, wait up to
# VERIFY seconds for a new loop process to appear, and only report success once
# it has stayed up past SETTLE — a rung that starts and immediately exits is a
# failure, not a spawn the principal can trust.
RESPAWN_VERIFY_SECONDS = 10.0
RESPAWN_SETTLE_SECONDS = 1.0
RESPAWN_POLL_SECONDS = 0.25

#: The closed rung-state vocabulary `decide` returns. Declared here, as named
#: constants, so a consumer (the fleet-health publisher, issue #498) IMPORTS the
#: rung states instead of re-typing their literals at its own boundary — the
#: same no-vocabulary-copy rule ADR-0022 D4 fixes for the SLO family.
HEALTHY = "healthy"
MISSING = "missing"
STALE = "stale"
DRIFTED = "drifted"
#: The third drift case (#773, AO-GR-21). Distinct from `DRIFTED` because the
#: remedy differs: the rung runs the *local* HEAD, which is itself behind
#: `origin/master`, so a respawn cannot change what was compared — the checkout
#: must be fast-forwarded first. Reporting this as `drifted` is what made the
#: watchdog a runaway: 132 respawn decisions in one night, zero work done.
CHECKOUT_BEHIND = "checkout-behind"
#: The fail-closed state (#739, AO-GR-25): the drift comparison could not be
#: made — an unreadable `origin/master` baseline, or a loop that reports no
#: commit. Distinct from `HEALTHY` on purpose: "I cannot see the baseline" is not
#: "there is no drift". Distinct from `DRIFTED` too, because the remedy differs —
#: a drifted rung needs a respawn, an unassessable one needs the remote ref
#: looked at first. It maps to the repo's exit-code 2 (CANNOT-ASSESS).
CANNOT_ASSESS = "cannot-assess"
RUNG_STATES = (MISSING, STALE, DRIFTED, CHECKOUT_BEHIND, CANNOT_ASSESS, HEALTHY)
#: Rung states that mean the rung is NOT healthy. `CANNOT_ASSESS` belongs here:
#: the watchdog still acts (it cannot certify the rung), it just says so.
UNHEALTHY_STATES = (MISSING, STALE, DRIFTED, CHECKOUT_BEHIND, CANNOT_ASSESS)

# ── the bounded remedy (issue #773, AO-GR-21) ───────────────────────────────
# A remedy that cannot change the value it compares must not be repeated without
# bound. The budget and the backoff are HARVESTED, never invented (GR-10): the
# CMR hub's `vendor/CMR/ops/retry.sh` declares `delay = BACKOFF * 2^(attempt-1)`
# seconds capped at 300 and `--attempts N` as the budget after which the command
# gives up — the same contract `fleet/runaway.py` reimplements for a directive.
#
# The knobs are configurable, and their defaults are documented here and nowhere
# else:
#
# ============ ============================== ======= ========================
# knob         env                            default meaning
# ============ ============================== ======= ========================
# K (cap)      ``AO_WATCHDOG_RESPAWN_ATTEMPTS``  ``3`` attempts on one unchanged
#                                                     rung state before the
#                                                     escalation; an attempt
#                                                     that changes the observed
#                                                     commit resets the counter
# base         ``AO_WATCHDOG_RESPAWN_BACKOFF``   ``60`` seconds; the delay after
#                                                     attempt *n* is
#                                                     ``min(base * 2**(n-1), 300)``
# ============ ============================== ======= ========================
#
# `3` attempts and `60` seconds, not `runaway.py`'s `5` / `30`: a watchdog tick
# runs every 2 minutes under cron, so a 30s base would elapse inside a single
# tick and the spacing would be decorative, and a fleet that is on stale code
# should reach a human in ~6 minutes rather than ~15.
#
# An unusable value is REFUSED (raises `WatchdogConfigError`), never silently
# fallen back to a default: a typo that disarms the attempt cap would restore the
# very runaway this bound exists to stop.

ENV_RESPAWN_ATTEMPTS = "AO_WATCHDOG_RESPAWN_ATTEMPTS"
ENV_RESPAWN_BACKOFF = "AO_WATCHDOG_RESPAWN_BACKOFF"
DEFAULT_RESPAWN_ATTEMPTS = 3
DEFAULT_RESPAWN_BACKOFF_SECONDS = 60
#: The harvested cap: `vendor/CMR/ops/retry.sh` never sleeps longer than this.
BACKOFF_CAP_SECONDS = 300
#: How long `git fetch` / `git merge --ff-only` may take before the remedy is
#: recorded as a failed attempt. A blocked network must not stall the pass.
FAST_FORWARD_TIMEOUT_SECONDS = 60

# ── the crash-loop escape (issue #366, AO-GR-21) ─────────────────────────────
# The safety rule "never restart a run just to update code" is enforced by an
# in-flight hold: a drifted rung whose run appears in flight is RECORDED as pending
# drift and acted on once the run completes. The hold is right. It is also not a
# bound — a rung whose runs die before they can report presents flight on every
# tick, so the hold is re-taken on every tick and the drift lock never opens.
# Measured 2026-09-14: the dispatcher held its own drift lock from 21:32 onward,
# executing code that predated five merged fixes, while the watchdog respawned the
# director in the same tick and wrote the hold into `.fleet/watchdog.log` each time.
#
# So the hold gets a budget of its own, counted in respawns DUE. A respawn is due
# on every tick the rung is drifted (or checkout-behind) and the remedy is reached:
# either it RUNS (`bounded_remedy`) or it is WITHHELD because a run appeared in
# flight (`record_pending`). Both are the same measurement — the rung was due a
# respawn and the observation did not move. `CRASH_LOOP_RESPAWNS` of those inside
# `CRASH_LOOP_WINDOW_SECONDS`, with nothing changing, is a crash loop, and the hold
# stops being honoured. The escape is not a second unbounded path: it goes through
# `bounded_remedy` like every other remedy, so the attempt cap, the backoff, the
# escalate-once and the park all still apply — which is what #773 is for.
#
# The ledger is reset the moment the observation moves, so the count can only ever
# describe respawns that changed NOTHING. That is what makes the number a bound
# rather than a tally: a fleet that is healing can never arm it.

#: N — how many respawns a rung may be DUE, on one unchanged observation, before
#: the watchdog calls it crash-looping and stops honouring the in-flight hold.
CRASH_LOOP_RESPAWNS = 3
#: The window, in seconds, those respawns are counted in.
CRASH_LOOP_WINDOW_SECONDS = 600.0
#: How many entries the per-rung respawn ledger keeps, so the remedy record — read
#: on every tick — stays bounded.
CRASH_LOOP_LEDGER_MAX = 16
#: The record key the ledger is stored under.
CRASH_LOOP_LEDGER_KEY = "respawns"


class WatchdogConfigError(RuntimeError):
    """A configured remedy budget is unusable — refuse rather than disarm."""


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive integer knob; an unusable value is refused, never ignored."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise WatchdogConfigError(f"{name}={raw!r} is not an integer") from None
    if value < 1:
        raise WatchdogConfigError(f"{name}={raw!r} must be >= 1")
    return value


def respawn_attempt_cap() -> int:
    """How many consecutive attempts one unchanged rung state gets. Refuses a typo."""
    return _env_positive_int(ENV_RESPAWN_ATTEMPTS, DEFAULT_RESPAWN_ATTEMPTS)


def respawn_backoff_seconds(attempt: int) -> float:
    """The delay after attempt `attempt` — `min(base * 2**(attempt-1), 300)`.

    The harvested formula, pinned numerically by `scripts/check-watchdog-bounded.sh`
    against the vendored contract whenever `vendor/CMR` is initialised.
    """
    base = _env_positive_int(ENV_RESPAWN_BACKOFF, DEFAULT_RESPAWN_BACKOFF_SECONDS)
    return float(min(base * (2 ** max(0, attempt - 1)), BACKOFF_CAP_SECONDS))


def drift_state_dir() -> Path:
    """The remedy ledger's directory: `.fleet/watchdog/`.

    Derived from `FLEET_DIR` on every call — never captured at import — so the
    fleet suite's isolation fixture (which redirects `watchdog.FLEET_DIR`) covers
    it, and a test can never append to the live fleet's ledger.
    """
    return FLEET_DIR / "watchdog"


def drift_record_path(rung: str) -> Path:
    return drift_state_dir() / f"{rung}.json"


def escalation_dir() -> Path:
    return drift_state_dir() / "escalations"


def load_drift_record(rung: str) -> dict | None:
    """The rung's persisted remedy record, or None.

    A torn or unreadable record reads as *absent*, which restarts the attempt
    counter rather than crashing the watchdog: the bound still holds (the cap is
    reached again from attempt 1), and a watchdog that dies on a bad JSON file
    stops watching the fleet.
    """
    try:
        record = json.loads(drift_record_path(rung).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def save_drift_record(rung: str, record: dict) -> None:
    """Persist the record atomically (tmp + rename), so a torn write cannot corrupt it."""
    path = drift_record_path(rung)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_drift_record(rung: str) -> None:
    """Drop the record — the finding is gone, so the counter must not survive it."""
    try:
        drift_record_path(rung).unlink()
    except OSError:
        pass


def observation_changed(record: dict | None, state: str, running: str) -> bool:
    """Did the rung's observation MOVE since `record` was written?

    Progress is the thing the drift lock protects, so it is measured the same way
    in both places that need it — the attempt counter (`bounded_remedy`) and the
    crash-loop ledger. A rung that is seen for the first time has no record, so
    nothing has moved yet: the first sighting is its own baseline.
    """
    if not record:
        return True
    return not (record.get("state") == state and record.get("running") == running)


def respawn_ledger(record: dict | None, when: float, *, changed: bool) -> list[float]:
    """The rung's respawn ledger, extended by the respawn that is due now (#366).

    "Due" is the watchdog's own decision on this tick, and it has exactly two
    outcomes: the respawn RUNS (`bounded_remedy`), or it is WITHHELD because a run
    appeared in flight (`record_pending`). Both are recorded, because a withheld
    respawn and a performed one that changed nothing are the same measurement — the
    rung was due a respawn, and the observation did not move.

    `changed` says whether the observation moved since the last entry. When it did,
    the rung made progress and the ledger restarts, so the count can only ever
    describe consecutive respawns that changed nothing.
    """
    source = record or {}
    previous = [
        float(item)
        for item in (source.get(CRASH_LOOP_LEDGER_KEY) or [])
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    ]
    kept = [] if changed else previous
    return [*kept, float(when)][-CRASH_LOOP_LEDGER_MAX:]


def crash_loop_verdict(name: str, when: float, record: dict | None = None) -> tuple[bool, str]:
    """Is this rung crash-looping — `CRASH_LOOP_RESPAWNS` inside the window, changing nothing?

    Returns `(crash-looping, why)`. The reason NAMES both constants, because a bound
    whose numbers are not in the log cannot be audited by the principal reading it.

    This is the distinction #366 is about. A run appearing in flight is evidence
    that a run EXISTS; it is not evidence that the run is making progress, and the
    drift lock exists to protect progress. A rung that has been due
    `CRASH_LOOP_RESPAWNS` respawns inside `CRASH_LOOP_WINDOW_SECONDS` without the
    observation ever moving has demonstrated the opposite: neither the respawns it
    got nor the run it was waiting for produced anything, and holding the lock open
    for it one more tick is the same defect one level down — a guard that protects a
    corpse. The escape is bounded all the same: it takes the ordinary remedy path,
    so the attempt cap, the backoff, the escalate-once and the park still apply.
    """
    source = load_drift_record(name) if record is None else record
    window = float(CRASH_LOOP_WINDOW_SECONDS)
    stamps = [
        float(item)
        for item in ((source or {}).get(CRASH_LOOP_LEDGER_KEY) or [])
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    ]
    recent = [stamp for stamp in stamps if 0.0 <= when - stamp <= window]
    if len(recent) < CRASH_LOOP_RESPAWNS:
        return False, ""
    return True, (
        f"crash-looping: {len(recent)} respawns due within {int(window)}s and none of them moved it "
        f"(N={CRASH_LOOP_RESPAWNS})"
    )


def remedy_parked(name: str, state: str, running: str) -> bool:
    """Has the remedy already PARKED this rung on this observation (#773, #366)?

    A park is terminal for the incident — the watchdog stops retrying until an
    principal rearms it — and `bounded_remedy` is the only thing allowed to decide
    that. So the in-flight hold must not be able to re-take a parked rung: the hold
    is a deferral, and a deferral that overwrites a park quietly un-parks the rung
    and lets it be acted on again.

    Measured by this lane's own probe rather than argued: with the escape's ledger
    counting in a sliding window, the tick at which the ledger had aged out of the
    window re-took the hold on a PARKED rung and flipped its phase back to
    `pending` — a crash loop that had already been parked would have been
    resurrected, one tick at a time, by the very check that bounds it.
    """
    record = load_drift_record(name) or {}
    return record.get("phase") == "parked" and not observation_changed(record, state, running)


def _git(target: Path, argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(target), *argv],
        capture_output=True,
        text=True,
        timeout=FAST_FORWARD_TIMEOUT_SECONDS,
    )


def _git_head(target: Path) -> str:
    try:
        result = _git(target, ["rev-parse", "--short", "HEAD"])
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def fast_forward_checkout(
    root: Path | None = None, *, remote: str = "origin/master"
) -> tuple[bool, str, str]:
    """Fast-forward the checkout to `remote`; `(changed, head, detail)`.

    This is the `checkout-behind` remedy (#773): when the rung already runs the
    local HEAD, a respawn cannot change the compared commit, so the *checkout*
    is what must move. `git fetch origin` first (the fetched ref is what the
    drift baseline reads), then `git merge --ff-only`. A refusal is reported, not
    swallowed: a diverged branch is `cannot fast-forward`, and the caller counts
    the attempt so the bound still applies.

    Never raises: a network-blocked or unreadable checkout is a recorded failed
    attempt, not a crash — a watchdog that dies stops watching.
    """
    target = Path(root) if root is not None else ROOT
    before = _git_head(target)
    try:
        fetched = _git(target, ["fetch", "origin"])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, before, f"git fetch origin failed ({type(exc).__name__}: {exc})"
    if fetched.returncode != 0:
        return False, before, f"git fetch origin failed ({(fetched.stderr or '').strip().splitlines()[-1:] or ['no output']})"
    return _fast_forward_move(target, remote, before)


def _fast_forward_move(target: Path, remote: str, before: str) -> tuple[bool, str, str]:
    """The move itself — `git merge --ff-only`, the fetch being the caller's job.

    Extracted so the rung remedy (#773) and the bootstrap (#780) perform the SAME
    move while keeping their own message shapes: they differ in what they decide
    *before* merging, never in how they merge.
    """
    try:
        merged = _git(target, ["merge", "--ff-only", remote])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, before, f"git merge --ff-only {remote} failed ({type(exc).__name__}: {exc})"
    after = _git_head(target)
    if merged.returncode != 0:
        reason = (merged.stderr or merged.stdout or "").strip().splitlines()
        return False, after, f"git merge --ff-only {remote} refused ({reason[-1] if reason else 'no output'})"
    if before != "unknown" and after != before:
        return True, after, f"fast-forwarded {before} -> {after}"
    return False, after, f"the checkout is already at {after}"


# ── the bootstrap kernel (issue #780) ───────────────────────────────────────
# cron runs this module FROM the checkout it supervises, so the copy in flight is
# the checkout's copy. Everything below therefore reads and moves through `git`
# subprocesses and the standard library only — never through the loaded module's
# own understanding of itself — so that a checkout which is BEHIND can still ask
# whether it is behind, and still be brought forward.

#: This module's own path inside the checkout. The bootstrap kernel identifies
#: itself by it, so it can ask the REMOTE what this file should say instead of
#: trusting the copy it is currently executing (#780).
SOURCE_RELPATH = "fleet/watchdog.py"
#: Set in the re-exec'd process's environment so the bootstrap happens at most
#: ONCE per invocation: the second process finds it set and does not re-exec.
ENV_BOOTSTRAPPED = "AO_WATCHDOG_BOOTSTRAPPED"

#: The freshness verdicts (`self_freshness`). `behind` is the one #780 exists for:
#: the checkout holds older code, so the code being executed is old code.
FRESH_CURRENT = "current"
FRESH_BEHIND = "behind"
FRESH_DIVERGED = "diverged"
FRESH_CANNOT_ASSESS = "cannot-assess"
FRESH_STATES = (FRESH_CURRENT, FRESH_BEHIND, FRESH_DIVERGED, FRESH_CANNOT_ASSESS)


def _short(value: str | None, width: int = 12) -> str:
    """A commit or blob hash, shortened for a log line — `unknown` when unreadable."""
    return value[:width] if value else "unknown"


def _git_read(target: Path, argv: list[str]) -> str | None:
    """One git READ, or `None`. A read that fails is never a value (#780)."""
    try:
        result = _git(Path(target), argv)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def source_blob(root: Path | None = None) -> str | None:
    """The blob hash of THIS module's file as the working tree holds it.

    `git hash-object` reads the bytes on disk, so this is the source of the code
    in flight — not the commit the tree claims to be at.
    """
    target = Path(root) if root is not None else ROOT
    return _git_read(target, ["hash-object", "--", str(target / SOURCE_RELPATH)])


def remote_source_blob(root: Path | None = None, *, remote: str = "origin/master") -> str | None:
    """The blob hash of THIS module's file AT `remote` — the remedy's own version.

    This is the read that dissolves the circularity (#780): "am I current?" is
    answered from the remote ref, so a checkout that is behind can still ask the
    question, and the answer does not depend on the copy doing the asking.
    """
    target = Path(root) if root is not None else ROOT
    return _git_read(target, ["rev-parse", "--verify", "--quiet", f"{remote}:{SOURCE_RELPATH}"])


def _is_ancestor(target: Path, older: str, newer: str) -> bool:
    """True when `older` is an ancestor of `newer`.

    `merge-base --is-ancestor` answers with an EXIT CODE (1 is a real answer, not a
    failure), so this cannot use `_git_read`, which folds every non-zero into
    `None`: an unreadable repository must not be confused with "not an ancestor".
    """
    try:
        result = _git(Path(target), ["merge-base", "--is-ancestor", older, newer])
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def self_freshness(root: Path | None = None, *, remote: str = "origin/master") -> dict:
    """Is the code THIS process is running the code at `remote`? (#780)

    The precondition #780 asks for: the checkout's freshness becomes an ASSERTED
    measurement instead of an assumption. Every input is a `git` subprocess, so a
    checkout that is behind can still make the measurement — the failure the old
    design could not see, because it could only ask the copy in flight.

    Verdicts (the `verdict` key, with `reason` naming both sides):

      * ``current``       — the working tree's blob EQUALS `remote`'s: this process
                            is running the code the remote holds.
      * ``behind``        — the blobs differ and the local HEAD is a STRICT ancestor
                            of `remote`: the checkout holds older code. This is the
                            condition the bootstrap exists to repair, and the one
                            #773 could not reach.
      * ``diverged``      — the blobs differ and the local HEAD is not a strict
                            ancestor: the checkout carries its own work — its own
                            commits (a lane worktree, which is AHEAD), or
                            uncommitted edits to this file — so `merge --ff-only`
                            cannot close the gap without rewriting it. Reported,
                            never silently repaired, and never confused with
                            `behind`. The strictness matters: a commit is an
                            ancestor of ITSELF, so a tree whose HEAD equals the
                            remote but whose file differs is a tree with local
                            edits, not a tree that is behind.
      * ``cannot-assess`` — a read failed. Fail-closed: an unreadable checkout is
                            never reported `current`, because "I cannot tell"
                            dressed as "I am current" is #739's defect one level up.
    """
    target = Path(root) if root is not None else ROOT
    local_head = _git_read(target, ["rev-parse", "--short", "HEAD"])
    remote_head = _git_read(target, ["rev-parse", "--short", remote])
    local_blob = source_blob(target)
    remote_blob = remote_source_blob(target, remote=remote)
    record: dict = {
        "root": str(target),
        "local_head": local_head,
        "remote_head": remote_head,
        "local_blob": local_blob,
        "remote_blob": remote_blob,
    }
    if None in (local_head, remote_head, local_blob, remote_blob):
        unreadable = [
            label
            for label, value in (
                ("the local HEAD", local_head),
                (remote, remote_head),
                (f"{SOURCE_RELPATH} in the working tree", local_blob),
                (f"{remote}:{SOURCE_RELPATH}", remote_blob),
            )
            if value is None
        ]
        record["verdict"] = FRESH_CANNOT_ASSESS
        record["reason"] = f"cannot read {', '.join(unreadable)}"
        return record
    record["reason"] = (
        f"running {_short(local_blob)} at {local_head}, {remote} {_short(remote_blob)} at {remote_head}"
    )
    if local_blob == remote_blob:
        record["verdict"] = FRESH_CURRENT
        return record
    if local_head != remote_head and _is_ancestor(target, local_head, remote_head):
        record["verdict"] = FRESH_BEHIND
        record["reason"] += " — the checkout is BEHIND, so the code in flight is older than the remote"
    else:
        record["verdict"] = FRESH_DIVERGED
        record["reason"] += (
            " — the checkout is NOT strictly behind the remote (it carries its own commits or "
            "uncommitted edits), so a fast-forward would have to discard them"
        )
    return record


def bootstrap_checkout(
    root: Path | None = None, *, remote: str = "origin/master"
) -> tuple[bool, str, str]:
    """Move a BEHIND checkout forward using nothing from the code it repairs (#780).

    `(moved, head, detail)`. The move is `git fetch origin` then a fast-forward,
    run as *git* subprocesses: the `git` binary is not in the checkout, so the
    remedy is available no matter how old the tree is — and no matter whether the
    loaded module knows about it, which is what makes this a bootstrap rather
    than a second instance of the same defect.

    A `current` verdict is a no-op. `diverged` is REFUSED by name, because a
    fast-forward would have to discard the checkout's own work. `cannot-assess`
    is a refusal too, never a silent success. Never raises: a watchdog that dies
    stops watching, so a blocked network is a recorded refusal, not a crash.
    """
    target = Path(root) if root is not None else ROOT
    before = _git_head(target)
    if before == "unknown":
        # Refuse BEFORE fetching: a directory that does not resolve to a git HEAD is
        # not a checkout that is behind, and a network call would tell us nothing
        # about it. The refusal names the path, so the principal learns WHICH tree
        # could not be read rather than only that something failed.
        return (
            False,
            before,
            f"REFUSED, the checkout cannot be read at all ({target} did not resolve to a git HEAD)",
        )
    try:
        fetched = _git(target, ["fetch", "origin"])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, before, f"git fetch origin failed ({type(exc).__name__}: {exc})"
    if fetched.returncode != 0:
        reason = (fetched.stderr or fetched.stdout or "").strip().splitlines()
        return False, before, f"git fetch origin failed ({reason[-1] if reason else 'no output'})"
    freshness = self_freshness(target, remote=remote)
    verdict = freshness["verdict"]
    if verdict == FRESH_CURRENT:
        return False, before, f"the code in flight is already {remote}'s ({freshness['reason']})"
    if verdict == FRESH_CANNOT_ASSESS:
        return False, before, f"REFUSED, the checkout cannot be certified current ({freshness['reason']})"
    if verdict == FRESH_DIVERGED:
        return False, before, (
            f"REFUSED, the checkout is not an ancestor of {remote} so a fast-forward would have to "
            f"discard it; only a BEHIND checkout is moved ({freshness['reason']})"
        )
    moved, head, detail = _fast_forward_move(target, remote, before)
    if not moved:
        return False, head, detail
    return True, head, f"{detail} (source {_short(freshness['remote_blob'])}, was {_short(freshness['local_blob'])})"


def reexec_watchdog(argv: list[str]) -> None:
    """Replace this process with a fresh interpreter on the NOW-CURRENT module (#780).

    The process that performed the fast-forward had already IMPORTED the old
    module, so without this the pass it goes on to run would still be the code it
    just replaced: the repair would not become the running code. `execv` re-reads
    the file from disk, so it must only be called AFTER the checkout has moved.
    Never returns.

    `ENV_BOOTSTRAPPED` is set first, so the process this creates does not bootstrap
    again — the bootstrap runs at most once per invocation.
    """
    os.environ[ENV_BOOTSTRAPPED] = "1"
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *argv])


def freshness_line(freshness: dict) -> str:
    """The one line a principal (and the gate) reads: verdict, both commits, both blobs."""
    return f"{freshness['verdict']} — {freshness['reason']}"


def bootstrap_preflight(argv: list[str], root: Path | None = None) -> int | None:
    """The `run` verb's precondition: the checkout must be the code being executed.

    Returns an exit code when the pass must NOT run, or `None` when it should
    continue. Re-execs — and so never returns — when the checkout moved, so the
    pass that follows is executed by the code that just arrived (#780).

    Fail-closed: a checkout that cannot be assessed returns CANNOT-ASSESS rather
    than running a pass whose currency it cannot vouch for. A checkout that is
    BEHIND and does not move is NOT-OK, because that state — a fleet silently
    pinned to old code — is exactly what #780 measured.
    """
    freshness = self_freshness(root)
    print(f"[watchdog] source: {freshness_line(freshness)}", flush=True)
    verdict = freshness["verdict"]
    if verdict == FRESH_CANNOT_ASSESS:
        print(
            f"[watchdog] source: CANNOT-ASSESS — {freshness['reason']}; refusing the pass rather "
            f"than running code whose currency cannot be vouched for",
            file=sys.stderr,
            flush=True,
        )
        return channel.EXIT_CANNOT_ASSESS
    if verdict != FRESH_BEHIND:
        return None
    moved, _head, detail = bootstrap_checkout(root)
    print(f"[watchdog] bootstrap: {detail}", flush=True)
    if moved:
        if os.environ.get(ENV_BOOTSTRAPPED):
            print(
                "[watchdog] bootstrap: the checkout moved but this process was already re-exec'd "
                "once — continuing on the loaded code rather than re-execing without bound",
                flush=True,
            )
            return None
        reexec_watchdog(argv)
    # The checkout did not move. Another process may have moved it in the interval,
    # in which case there is nothing wrong and the pass may proceed.
    after = self_freshness(root)
    if after["verdict"] == FRESH_CURRENT:
        print(f"[watchdog] source: {freshness_line(after)} — brought forward concurrently", flush=True)
        return None
    print(
        f"[watchdog] source: NOT-OK — the code in flight is not the remote's and the checkout was "
        f"not moved ({after['reason']}); this is the fleet pinned to old code (#780)",
        file=sys.stderr,
        flush=True,
    )
    return channel.EXIT_NOT_OK


def rung_log(name: str) -> Path:
    """The capture log for a rung: `.fleet/<rung>.log`.

    The path is the contract between the writer (this module, and
    `control.py` when it starts a rung), the reader (`fleet/console.py`) and the
    principal's windows (`fleet/run-fleet.sh` tails exactly this file).
    """
    return FLEET_DIR / f"{name}.log"


def open_log(name: str) -> TextIO:
    """Open a rung's capture log for appending, created if missing, line-buffered.

    Line buffering matters for the principal, not the machine: a rung writes a few
    lines and then blocks on its next poll, so a block-buffered handle would
    leave the window empty for minutes at a time and look like a dead rung.
    """
    path = rung_log(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8", buffering=1)


def spawn(name: str, command: list[str]) -> subprocess.Popen:
    """Start a rung detached, appending stdout+stderr to `.fleet/<name>.log`.

    The parent closes its copy of the handle as soon as the child holds its own:
    the log stays open for the child's lifetime without leaking a descriptor into
    a watchdog that exits a moment later.

    The environment is passed explicitly — ``runtime.runner_env()``, the same PATH
    the executor resolves its runner against. The watchdog is cron's own child, so
    without this the whole chain (watchdog -> launcher -> loop -> executor)
    inherits cron's minimal PATH: measured 2026-09-14 (#733), the dispatcher could not
    spawn a single executor because ``~/.local/bin`` was not on it.
    """
    handle = open_log(name)
    try:
        return subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=runtime.runner_env(),
        )
    finally:
        handle.close()


def loop_pid(pattern: str) -> int | None:
    """The pid of the loop matching `pattern`, or None."""
    result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def loop_pids(pattern: str) -> list[int]:
    """Every pid matching `pattern` — `loop_pid` returns only the first.

    Respawn verification needs the whole set: the pid we just tried to stop can
    linger (a SIGKILLed child stays visible until it is reaped), so "a pid exists"
    only proves a rung came up when it can be compared against the old one.
    """
    result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]


def process_alive(pid: int | None) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_beat(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_in_flight() -> bool:
    """True when a run marker's OWN evidence says a run is in flight (#793).

    The marker's ``pid`` is the **loop's**, and a loop outlives every run it
    dispatches, so asking whether that pid was alive answered True for a crashed
    run's leftover marker as long as the loop lived. Measured 2026-09-14: four
    markers ~5.6h old, every one with ``child_pid: null``, each naming the live
    dispatcher loop — the drift lock was held open on every tick, the dispatcher's own
    heartbeat (``state: idle``, ``runs: 0``) was ignored for that decision, and
    the rung stayed on pre-#723 code with no attempt budget.

    Flight is therefore the marker's own evidence: a live ``child_pid``, or a
    ``ts`` the run's own beater advanced (`governance/spawn/liveness.py`).
    """
    held, _ = spawn_liveness.runs_in_flight(RUNS_DIR)
    return held


def flight_evidence() -> str:
    """What the markers say about flight — for the HELD line, never the decision.

    The decision stays `run_in_flight()`, which the bounded-remedy gate and the
    test suite replace wholesale in order to drive a busy rung deterministically.
    This only supplies the sentence: NAMING the marker that holds the lock is what
    makes a held line checkable, instead of an assertion that "a run is in flight"
    the reader cannot audit (#793).
    """
    held, note = spawn_liveness.runs_in_flight(RUNS_DIR)
    return note if held else "a run is in flight"


def decide(
    pid: int | None,
    beat: dict | None,
    baseline: str,
    baseline_name: str = "origin/master",
    local_head: str | None = None,
) -> tuple[str, str]:
    """Classify a rung: missing / stale / cannot-assess / drifted / checkout-behind / healthy.

    `baseline` is the **remote** commit (`origin/master`), never the local
    checkout's HEAD. The local checkout is routinely the stale side in this
    fleet, so comparing to it made the comparison *stale-to-stale*: the loop's
    own start commit read back as the baseline it was judged against, so a loop
    executing pre-fix code reported `healthy` (#739, AO-GR-25).

    `local_head` is a **separate** input, and it does not weaken that (#773):
    it is used only to tell the two mismatches apart. `running != baseline` is
    always a finding; whether the remedy is a respawn (`drifted`: the rung is not
    on the checkout's HEAD either) or a fast-forward (`checkout-behind`: the rung
    IS the checkout's HEAD, so only the checkout can move) depends on it. A caller
    that does not know the local HEAD passes nothing and gets the pre-#773 verdict.

    Fail-closed by construction: an unreadable baseline is CANNOT-ASSESS, never
    healthy. The previous rule — `if head != "unknown" and running != head` —
    read an unreadable HEAD as *healthy*, which silently disabled drift
    detection entirely instead of reporting that it could not run.
    """
    if pid is None:
        return MISSING, "no loop process"
    if beat is None:
        return STALE, "no heartbeat from a live loop"
    age = channel.heartbeat_age_seconds(beat)
    if age is None:
        return STALE, "heartbeat has no timestamp"
    if age > channel.STALE_HEARTBEAT_SECONDS:
        return STALE, f"last beat {int(age)}s ago"
    running = str(beat.get("commit", "unknown"))
    drift_state, reason = channel.classify_drift(running, baseline, baseline_name, local_head)
    if drift_state == channel.DRIFT_DRIFTED:
        return DRIFTED, reason
    if drift_state == channel.DRIFT_CHECKOUT_BEHIND:
        return CHECKOUT_BEHIND, reason
    if drift_state == channel.DRIFT_CANNOT_ASSESS:
        return CANNOT_ASSESS, reason
    return HEALTHY, ""


def rung_came_up(
    pattern: str,
    pid_before: int | None,
    *,
    window: float | None = None,
    settle: float | None = None,
    clock=time.monotonic,
    sleep=time.sleep,
) -> bool:
    """True when a *new* live loop for `pattern` appears and survives `settle`.

    A spawn that started nothing leaves no pid to find, and a rung that exits at
    startup (singleton refusal, a crash) is gone before the settle window ends;
    both must read as a failed respawn rather than a fabricated success.
    """
    if window is None:
        window = RESPAWN_VERIFY_SECONDS
    if settle is None:
        settle = RESPAWN_SETTLE_SECONDS
    started = clock()
    deadline = started + window
    while True:
        up = any(pid != pid_before for pid in loop_pids(pattern))
        if up and (clock() - started) >= settle:
            return True
        if clock() >= deadline:
            return False
        sleep(RESPAWN_POLL_SECONDS)


def respawn(
    pattern: str,
    script: str,
    name: str = "",
    *,
    window: float | None = None,
    settle: float | None = None,
) -> bool:
    """Stop the rung (SIGTERM, then SIGKILL), start it detached, and VERIFY it came up.

    `name` selects the capture log (`.fleet/<name>.log`) and defaults to the
    launcher's own stem, which is right for every rung whose log is named after
    its script; the dispatcher passes its rung name explicitly because its launcher
    is `terminal.sh` while the principal's window is `sister`.

    Returns False — so the caller prints `RESPAWN FAILED` and the pass exits
    non-zero — when the spawn itself raises, or when no new rung process appears
    and survives the settle window.
    """
    pid = loop_pid(pattern)
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:            pass
        deadline = time.time() + 15
        while process_alive(pid) and time.time() < deadline:
            time.sleep(0.5)
        if process_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    rung = name or Path(script).stem
    refusal = freeze.refuse_if_frozen(rung)
    if refusal:
        print(f"[watchdog] {refusal}", flush=True)
        raise RespawnRefused(refusal)
    try:
        spawn(rung, ["setsid", "bash", str(ROOT / script)])
    except (OSError, subprocess.SubprocessError):
        return False
    return rung_came_up(pattern, pid, window=window, settle=settle)


def record_pending(
    name: str,
    state: str,
    reason: str,
    running: str,
    baseline: str,
    baseline_name: str,
    local_head: str | None,
    when: float,
) -> dict:
    """Record that a rung needs action but is BUSY — pending drift, not a dropped finding.

    Requirement 3 of #773: the dispatcher logged `drifted … but a run is in flight —
    left alone` on **every** tick and so was never updated even after its run
    finished. The protection (never restart a run to update code) is right and is
    kept; dropping the finding is not. The record carries no `next_attempt_at`, so
    the first pass after the run completes acts on it immediately.

    Issue #366: this is also where a WITHHELD respawn lands, in the same ledger a
    performed one lands in. A hold is a respawn the rung was due and did not get, so
    it has to count towards the budget that eventually stops the hold — otherwise
    the check that makes the hold safe is invisible to the bound that makes it
    finite.
    """
    record = load_drift_record(name) or {}
    record.update(
        {
            "rung": name,
            "phase": "pending",
            "state": state,
            "reason": reason,
            "running": running,
            "baseline": baseline,
            "baseline_name": baseline_name,
            "local_head": local_head,
            "attempts": int(record.get("attempts") or 0),
            "first_seen": float(record.get("first_seen") or when),
            "last_seen": when,
            CRASH_LOOP_LEDGER_KEY: respawn_ledger(
                record, when, changed=observation_changed(record, state, running)
            ),
        }
    )
    save_drift_record(name, record)
    return record


def escalate_once(name: str, record: dict) -> Path:
    """Write the ONE escalation artifact for this incident; return its path.

    Named after the incident's first sighting, so a later, genuinely new incident
    gets its own artifact instead of overwriting this one — "escalate once" is
    provable by counting artifacts, not by trusting a log line.
    """
    path = escalation_dir() / f"{name}.{int(record['first_seen'])}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    return path


def bounded_remedy(
    name: str,
    state: str,
    reason: str,
    running: str,
    baseline: str,
    baseline_name: str,
    local_head: str | None,
    script: str,
    pattern: str,
    when: float,
    checkout_root: Path | None,
) -> tuple[str, bool]:
    """Act on an unhealthy rung, bounded by an attempt cap (AO-GR-21, #773).

    Returns `(outcome_text, ok)`. The counter counts *consecutive attempts that did
    not change the observation*: an attempt after which the rung reports a
    different commit (or state) resets to 1, because the remedy worked. When the
    cap is exhausted the rung is escalated **once** and **parked** — the watchdog
    stops retrying it, which is the whole point: a remedy that cannot change the
    value it compares is a runaway, not a repair.

    The remedy itself depends on the case, which is why the case is a state and
    not a bare commit mismatch: `checkout-behind` needs the CHECKOUT to move
    (fast-forward), everything else needs the RUNG to move (respawn).
    """
    cap = respawn_attempt_cap()
    checkout = str(Path(checkout_root) if checkout_root is not None else ROOT)
    record = load_drift_record(name) or {}
    first_seen = float(record.get("first_seen") or when)
    changed = observation_changed(record, state, running)
    same_observation = not changed
    previous_attempts = int(record.get("attempts") or 0)
    attempts = previous_attempts + 1 if (same_observation and previous_attempts > 0) else 1
    # #366: the respawn due on this tick goes into the rung's respawn ledger, on the
    # same "did anything move?" test the attempt counter uses. The ledger is what
    # tells a later tick whether the in-flight hold is protecting progress or
    # protecting a corpse.
    ledger = respawn_ledger(record, when, changed=changed)

    if record.get("phase") == "parked" and same_observation:
        return (
            f"PARKED — {previous_attempts} attempts could not change it, escalated once; refusing to "
            f"retry (rearm: python3 fleet/watchdog.py rearm --rung {name})",
            False,
        )

    if attempts > cap:
        parked = {
            **record,
            "rung": name,
            "phase": "parked",
            "state": state,
            "reason": reason,
            "running": running,
            "baseline": baseline,
            "baseline_name": baseline_name,
            "local_head": local_head,
            "checkout": checkout,
            "attempts": cap,
            "first_seen": first_seen,
            "last_seen": when,
            "escalated_at": when,
        }
        path = escalate_once(name, parked)
        save_drift_record(name, parked)
        return (
            f"ESCALATED ONCE after {cap} attempts that did not change it — running {running}, "
            f"{baseline_name} {baseline}, checkout {checkout} ({path.name}); stopping retries (parked)",
            False,
        )

    next_attempt_at = float(record.get("next_attempt_at") or 0)
    if next_attempt_at and when < next_attempt_at:
        save_drift_record(
            name,
            {
                **record,
                "rung": name,
                "phase": "pending",
                "state": state,
                "reason": reason,
                "running": running,
                "baseline": baseline,
                "baseline_name": baseline_name,
                "local_head": local_head,
                "checkout": checkout,
                "attempts": attempts,
                "first_seen": first_seen,
                "last_seen": when,
            },
        )
        return (
            f"holding — attempt {attempts}/{cap} is due in {int(next_attempt_at - when)}s (backoff "
            f"{int(respawn_backoff_seconds(attempts))}s, capped at {BACKOFF_CAP_SECONDS}s)",
            True,
        )

    if state == CHECKOUT_BEHIND:
        changed, new_head, detail = fast_forward_checkout(checkout_root)
        if changed:
            try:
                ok = respawn(pattern, script, name)
                outcome = (
                    f"fast-forwarded the checkout ({detail}) and "
                    f"{'respawned' if ok else 'RESPAWN FAILED'} so the rung loads {new_head} "
                    f"(attempt {attempts}/{cap})"
                )
            except RespawnRefused as exc:
                ok = True
                outcome = (
                    f"fast-forwarded the checkout ({detail}) but did not respawn — {exc} "
                    f"(attempt {attempts}/{cap}, not counted)"
                )
        else:
            ok = True
            outcome = (
                f"fast-forward did not move the checkout ({detail}) — DRIFT UNRESOLVED, "
                f"attempt {attempts}/{cap}"
            )
    else:
        try:
            ok = respawn(pattern, script, name)
            outcome = f"{'respawned' if ok else 'RESPAWN FAILED'} (attempt {attempts}/{cap})"
        except RespawnRefused as exc:
            ok = True
            outcome = f"REFUSED (frozen) — {exc} (attempt {attempts}/{cap}, not counted)"

    save_drift_record(
        name,
        {
            "rung": name,
            "phase": "retrying",
            "state": state,
            "reason": reason,
            "running": running,
            "baseline": baseline,
            "baseline_name": baseline_name,
            "local_head": local_head,
            "checkout": checkout,
            "attempts": attempts,
            "first_seen": first_seen,
            "last_seen": when,
            "next_attempt_at": when + respawn_backoff_seconds(attempts),
            CRASH_LOOP_LEDGER_KEY: ledger,
        },
    )
    return outcome, ok


def rung_action(
    name: str,
    pattern: str,
    script: str,
    beat_path: Path,
    force: bool,
    baseline: str,
    baseline_name: str = "origin/master",
    *,
    local_head: str | None = None,
    when: float | None = None,
    checkout_root: Path | None = None,
) -> str:
    """One rung, one decision: what did the watchdog do about it — and what is it missing?

    Issue #319: the commit comparison (`decide`) is joined by the capability
    comparison, so a rung running a build that predates a merged control reports
    WHICH control is absent instead of only "drifted". A rung that is *current*
    and still missing a declared capability is reported too — and NOT respawned,
    because restarting a build that never had the capability cannot fix it.

    Issue #739: the healthy line names BOTH commits — the one the loop is running
    and the `origin/master` baseline it was judged against — so a principal can
    audit the comparison instead of trusting the verdict. A comparison against a
    baseline that is stale, or against the local checkout, is invisible in a bare
    `healthy`.

    Issue #773: every acting path goes through `bounded_remedy`, so no remedy is
    repeated indefinitely, and the `checkout-behind` case is repaired by moving
    the CHECKOUT rather than by respawning a rung that is already on its HEAD.

    Issue #366: the hold itself is bounded. "Never restart a run just to update
    code" is enforced by asking `run_in_flight`, and a rung whose runs die before
    they can report answers True on every tick — so the hold was re-taken forever
    and the drift lock never opened. `crash_loop_verdict` counts how many respawns
    this rung has been DUE (performed or withheld) inside `CRASH_LOOP_WINDOW_SECONDS`
    on an unchanged observation; once that reaches `CRASH_LOOP_RESPAWNS` the hold is
    not honoured, the remedy proceeds through `bounded_remedy` like every other, and
    the line names the constants that decided it.
    """
    moment = time.time() if when is None else when
    if local_head is None:
        local_head = channel.head_commit()
    pid = loop_pid(pattern)
    beat = read_beat(beat_path)
    state, reason = decide(pid, beat, baseline, baseline_name, local_head)
    running = str((beat or {}).get("commit", "unknown"))
    verbatim = f"running {running}, {baseline_name} {baseline}"
    if force:
        state, reason = "forced", "operator asked to respawn"
    capability = channel.capability_line(channel.capability_finding(name, beat, running))
    if state == HEALTHY:
        # The finding is gone: the counter must not survive it, or a fixed rung
        # would inherit a stale attempt budget from an earlier incident.
        clear_drift_record(name)
        return f"{name}: healthy ({verbatim}) | {capability}"
    if force:
        try:
            ok = respawn(pattern, script, name)
            clear_drift_record(name)
            return f"{name}: forced (operator asked to respawn) — {'respawned' if ok else 'RESPAWN FAILED'} | {capability}"
        except RespawnRefused as exc:
            clear_drift_record(name)
            return f"{name}: forced (operator asked to respawn) — REFUSED (frozen) — {exc} | {capability}"
    if (
        state in (DRIFTED, CHECKOUT_BEHIND)
        and name == "sister"
        and run_in_flight()
        and not remedy_parked(name, state, running)
    ):
        # #366: the hold is a bound, not a policy. A rung whose runs die before they
        # can report presents flight on every tick, so the hold would be re-taken
        # forever and the drift lock would never open — measured: the dispatcher held its
        # own lock with nothing running. `crash_loop_verdict` is the budget that ends
        # it, and it names N and the window when it does. A rung the remedy has
        # already PARKED is excluded first: a deferral must not un-park an incident
        # #773 has closed.
        looping, loop_note = crash_loop_verdict(name, moment)
        if not looping:
            record_pending(name, state, reason, running, baseline, baseline_name, local_head, moment)
            return (
                f"{name}: {state} ({reason}) — recorded as pending drift, {flight_evidence()} — left alone "
                f"until it completes, then acted on | {capability}"
            )
        escape = f" — {loop_note} — the drift lock cannot clear, so this respawn is NOT withheld"
    else:
        escape = ""
    # CANNOT_ASSESS respawns too: the watchdog cannot certify the rung, and a
    # respawn is the only action that can restore a readable comparison. It is
    # reported with its reason so the principal sees WHY it could not be judged —
    # never silently folded into `healthy`.
    outcome, ok = bounded_remedy(
        name,
        state,
        reason,
        running,
        baseline,
        baseline_name,
        local_head,
        script,
        pattern,
        moment,
        checkout_root,
    )
    return f"{name}: {state} ({reason}) — {outcome}{escape} | {capability}"


def monitor_missing() -> bool:
    """True when no ``fleet/monitor.py`` process is present (simple presence check)."""
    return loop_pid(MONITOR_PATTERN) is None


def start_monitor(*, window: float | None = None, settle: float | None = None) -> bool:
    """Start the monitor detached (its own session) and verify it came up.

    Like `respawn`, a successful `Popen` is not a running monitor: a monitor that
    dies at startup must surface as `RESPAWN FAILED` so the pass exits non-zero.
    """
    refusal = freeze.refuse_if_frozen(MONITOR_NAME)
    if refusal:
        print(f"[watchdog] {refusal}", flush=True)
        raise RespawnRefused(refusal)
    try:
        spawn(MONITOR_NAME, ["setsid", "python3", str(ROOT / "fleet" / "monitor.py")])
    except (OSError, subprocess.SubprocessError):
        return False
    return rung_came_up(MONITOR_PATTERN, None, window=window, settle=settle)


def watchdog_once(force: bool = False) -> int:
    """One pass over both loops plus the monitor.

    Tri-state, per the repo's gate convention (`channel.EXIT_*`):

      * **0 OK** — every rung healthy and judgeable (a respawn that succeeded
        counts: the state was repaired).
      * **1 NOT-OK** — a definite failure: `RESPAWN FAILED`, a `CAPABILITY STALE`
        rung, which is a silently absent control and the one finding no respawn can
        repair (issue #319), or a drift whose remedy did not resolve it — including
        the escalated, parked rung, which is reported on every pass so an exhausted
        remedy is never mistaken for a quiet fleet (#773, AO-GR-21).
      * **2 CANNOT-ASSESS** — a rung's drift could not be judged at all because
        the `origin/master` baseline was unreadable, or the configured remedy
        budget is unusable. Never folded into 0: the whole defect this replaced was
        a control that reported `healthy` for a comparison it could not actually
        make (#739, AO-GR-25).

    A NOT-OK verdict outranks CANNOT-ASSESS: a known failure is reported as the
    failure it is, and the unassessable rung is still named on its own line.
    """
    # #977 (issue #706 D5): acquire the single-writer lease BEFORE any rung is
    # acted on. Two replicas of the fleet-cron pair must never both respawn or
    # fast-forward the same rung; the loser no-ops this pass and exits 0 — it
    # is not an error, it means the other replica already has this tick.
    watchdog_lease = lease.make_lease(
        job=WATCHDOG_LEASE_JOB,
        path=FLEET_DIR / "watchdog.lease",
        ttl_seconds=WATCHDOG_LEASE_TTL_SECONDS,
    )
    if not watchdog_lease.acquire():
        record = lease.skipped_log(
            WATCHDOG_LEASE_JOB, backend=lease.backend_name(), detail="watchdog tick lease held elsewhere"
        )
        print(f"[watchdog] {json.dumps(record, sort_keys=True)}", flush=True)
        return channel.EXIT_OK
    try:
        return _watchdog_once_locked(force)
    finally:
        watchdog_lease.release()


def _self_reap_own_gate_lock() -> bool:
    """Reap THIS checkout's own leftover gate-lock file, never box-wide.

    RCA 2026-09-17 fix #3, and RCA-0015 before it
    (governance/lessons/rca/RCA-0015-zero-byte-gate-lock-wedge.md), already
    ruled a box-wide sweep unsafe: a sweeper walking every worktree can race
    a DIFFERENT worktree's in-flight `gatelock.acquire` between that
    acquirer's `os.open(O_CREAT)` and its `_try_lock` — the file is briefly
    unflocked and looks exactly like a leftover, so the sweep would unlink
    it, the acquirer's `_flock_fresh` re-check would fail, and a
    legitimately starting gate would report rc 12 CANNOT-ASSESS for a key it
    never touched. Scoped to ``ROOT`` — the one worktree this watchdog
    process itself runs in — the only acquirer that could ever be in that
    window is this same checkout, so the race is gone: this is the same
    operation this worktree's own `gatelock.release` already performs, just
    runnable on a schedule without a live gate around to call `release`
    first. `gatelock.health()` (called above, in `_watchdog_once_locked`)
    stays the box-wide, alert-only, never-reaps sweep; this is a second,
    narrower, self-owned call site — pulled into its own function so it is
    unit-testable without exercising the whole watchdog pass.

    Returns whether the call was unassessable (an exception), so the caller
    can fold that into its own CANNOT-ASSESS verdict.
    """
    try:
        reaped = gatelock.reap_own_worktree(ROOT)
    except Exception as exc:  # defensive: mirrors this pass's own CANNOT-ASSESS style
        print(f"[watchdog] gate-lock self-reap: CANNOT-ASSESS — {exc}", flush=True)
        return True
    if reaped:
        print(f"[watchdog] gate-lock self-reap: reaped own leftover for {ROOT}", flush=True)
    else:
        print("[watchdog] gate-lock self-reap: nothing to reap", flush=True)
    return False


def _watchdog_once_locked(force: bool) -> int:
    try:
        # Refuse a misconfigured bound BEFORE any rung is acted on: a typo must
        # not silently disarm the attempt cap that stops the runaway (#773).
        respawn_attempt_cap()
    except WatchdogConfigError as exc:
        print(
            f"[watchdog] config: CANNOT-ASSESS — {exc}; refusing the pass so a typo cannot "
            f"disarm the attempt cap",
            file=sys.stderr,
            flush=True,
        )
        return channel.EXIT_CANNOT_ASSESS
    # #780: the pass REPORTS whether the code it is executing is the remote's, so a
    # checkout pinned to old code is a visible finding rather than a silent
    # condition. It does not repair here — `bootstrap_preflight` does that on the
    # `run` verb, before this pass — because a function a test may call must not
    # move a repository as a side effect of being observed.
    print(f"[watchdog] source: {freshness_line(self_freshness())}", flush=True)
    baseline = channel.remote_head_commit()
    # The local HEAD is read ONCE and passed in, so both rungs are judged against
    # the same checkout — the input that separates `drifted` (the rung is stale)
    # from `checkout-behind` (the checkout is stale), #773.
    local = channel.head_commit()
    failed = False
    unassessable = False
    for name, pattern, script, beat_path in RUNGS:
        line = rung_action(name, pattern, script, beat_path, force, baseline, local_head=local)
        print(f"[watchdog] {line}", flush=True)
        disagreement = spawn_liveness.contradiction(read_beat(beat_path), RUNS_DIR)
        if disagreement:
            # Reported on EVERY pass, not only when a rung is drifting: two
            # artifacts of the fleet that disagree are a finding in their own
            # right, and before #793 the marker silently won the argument.
            print(f"[watchdog] {name}: {disagreement}", flush=True)
        if "FAILED" in line or "CAPABILITY STALE" in line:
            failed = True
        if "ESCALATED ONCE" in line or "PARKED" in line or "DRIFT UNRESOLVED" in line:
            failed = True
        if f": {CANNOT_ASSESS} (" in line:
            unassessable = True
    # Third rung: the monitor is a resident poller with no run-in-flight concern
    # and no code-drift concept, so a missing process is always restarted and a
    # present one is left alone.
    if monitor_missing():
        try:
            ok = start_monitor()
            print(f"[watchdog] monitor: missing — {'respawned' if ok else 'RESPAWN FAILED'}", flush=True)
            if not ok:
                failed = True
        except RespawnRefused as exc:
            print(f"[watchdog] monitor: missing — REFUSED (frozen) — {exc}", flush=True)
    else:
        print("[watchdog] monitor: healthy", flush=True)
    # Proactive gate-lock health sweep (RCA-0007, the #948 follow-up): a
    # zero-byte or owner-less worktree lock used to be found only reactively,
    # by tracing a starved box-wide verify-gate cap back to one leftover file
    # across many unrelated PRs' "PARKED (rc 10/rc 11)" evidence. This runs
    # the same sweep `gate-lock.sh doctor` runs, on every tick, so a leftover
    # is a printed finding on the next pass instead of hours of contention.
    # Alert-only: it reports by name and never reaps a lock it does not own.
    try:
        gate_report, gate_needs_attention = gatelock.health()
    except Exception as exc:  # defensive: mirrors this pass's own CANNOT-ASSESS style
        print(f"[watchdog] gate-lock health: CANNOT-ASSESS — {exc}", flush=True)
        unassessable = True
    else:
        for gate_line in gate_report.splitlines():
            print(f"[watchdog] gate-lock health: {gate_line}", flush=True)
        if gate_needs_attention:
            # Alert loudly, but don't fold into this pass's own pass/fail
            # verdict: the sweep is box-wide (every worktree's lock, not just
            # this checkout's), so a leftover left by an unrelated worktree
            # must not make an otherwise-healthy watchdog pass report NOT-OK.
            # `gate-lock.sh doctor` and its own exit 13 are the enforcement
            # surface; this pass is the loud, cheap, always-on notice.
            print(
                "[watchdog] gate-lock health: ATTENTION — see 'needing attention' "
                "above; run 'bash scripts/gate-lock.sh doctor' or 'status' to act",
                flush=True,
            )
    if failed:
        return channel.EXIT_NOT_OK
    if unassessable:
        return channel.EXIT_CANNOT_ASSESS
    return channel.EXIT_OK


def beat_path(rung: str) -> Path:
    """The beat this rung publishes (read through `channel`, so a test redirect applies)."""
    return channel.BRAIN_HEARTBEAT if rung == "brain" else channel.HEARTBEAT


def cmd_rearm(args: argparse.Namespace) -> int:
    """Clear a rung's attempt record after a principal has fixed the cause (#773).

    The escalation ARTIFACT is deliberately kept: the incident happened, and the
    record of it is the audit. What is cleared is the bound's own state, so the
    next pass judges the rung afresh instead of short-circuiting to `PARKED`.
    """
    record = load_drift_record(args.rung)
    clear_drift_record(args.rung)
    if record is None:
        print(f"watchdog rearm: {args.rung} had no remedy record — nothing to clear")
        return channel.EXIT_OK
    print(
        f"watchdog rearm: {args.rung} cleared (was {record.get('phase')} after "
        f"{record.get('attempts')} attempt(s)); the next pass judges it again"
    )
    return channel.EXIT_OK


def cmd_capabilities(args: argparse.Namespace) -> int:
    """Report the declared capabilities each rung does NOT implement (issue #319).

    Reads the rung's own beat — it never writes one — so the report measures the
    live fleet instead of declaring a capability set on its behalf. `--beat`
    reads a synthetic beat and `--commit` pins the **running** commit the rung's
    capability declaration is read from — the commit against which a capability
    set is judged, which is NOT the drift baseline (`origin/master`, above) and is
    deliberately named differently so the two cannot be confused (#739). This is
    how the runbook gate provokes each of the three cases and requires it to be
    reported (a check that cannot fail is a formality, GR-12).
    """
    rungs = args.rung or [name for name, _pattern, _script, _beat in RUNGS]
    if args.beat and len(rungs) != 1:
        print("watchdog capabilities: --beat needs exactly one --rung", file=sys.stderr)
        return channel.EXIT_CANNOT_ASSESS
    head = args.commit or channel.head_commit()
    findings = []
    for rung in rungs:
        path = Path(args.beat) if args.beat else beat_path(rung)
        findings.append(channel.capability_finding(rung, read_beat(path), head))
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "rung": finding.rung,
                        "case": finding.case,
                        "kind": finding.kind,
                        "missing": list(finding.missing),
                        "undeclared": list(finding.undeclared),
                        "detail": finding.detail,
                        "remediation": finding.remediation,
                    }
                    for finding in findings
                ],
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(channel.capability_line(finding))
    if any(finding.missing or finding.kind == channel.KIND_DOWN for finding in findings):
        return 1
    if any(finding.kind == channel.KIND_UNKNOWN for finding in findings):
        return 2
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Bring the checkout forward without requiring its own copy of this code (#780).

    The bootstrap's entry point, and deliberately thin: the freshness read is
    `git`'s and the move is `git`'s, so a stale checkout that HAS this verb can
    always run it — which is the whole point. `--root` names the checkout to
    repair, which is what lets the gate start from a deliberately stale checkout
    and prove the remedy reaches it; `--no-reexec` stops after the move.

    Tri-state, like every other verb here: 0 OK (the checkout now holds the code
    it is executing, whether or not it had to move), 1 NOT-OK (it is not current
    and was not moved — `diverged`, or a refused merge), 2 CANNOT-ASSESS (the
    checkout could not be read, so nothing can be claimed about it).
    """
    root = Path(args.root).resolve() if args.root else None
    before = self_freshness(root)
    print(f"[watchdog] source: {freshness_line(before)}", flush=True)
    if before["verdict"] == FRESH_CURRENT:
        print("watchdog bootstrap: the checkout already holds the code it is executing — nothing to do")
        return channel.EXIT_OK
    if before["verdict"] == FRESH_CANNOT_ASSESS:
        print(f"watchdog bootstrap: CANNOT-ASSESS — {before['reason']}", file=sys.stderr)
        return channel.EXIT_CANNOT_ASSESS
    moved, _head, detail = bootstrap_checkout(root)
    print(f"[watchdog] bootstrap: {detail}", flush=True)
    after = self_freshness(root)
    print(f"[watchdog] source: {freshness_line(after)}", flush=True)
    if not moved:
        print(
            f"watchdog bootstrap: NOT-OK — the checkout is not current and was not moved "
            f"({after['verdict']})",
            file=sys.stderr,
        )
        return channel.EXIT_NOT_OK
    if args.no_reexec or os.environ.get(ENV_BOOTSTRAPPED):
        return channel.EXIT_OK
    # The move is done and the file on disk is now the remote's, so re-exec onto it:
    # the remainder of the run is then executed by the code that just arrived, and
    # the re-exec'd process reports the `current` verdict it can now certify.
    reexec_watchdog(["bootstrap", *(["--root", str(root)] if root else []), "--no-reexec"])
    return channel.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-watchdog", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one watchdog pass (the cron entry point)")
    run.add_argument("--force", action="store_true", help="respawn the loop rungs even when healthy")
    run.set_defaults(func=lambda args: watchdog_once(args.force))
    rearm = sub.add_parser(
        "rearm",
        help="clear a rung's parked attempt record so the next pass judges it again (#773)",
    )
    rearm.add_argument("--rung", required=True, choices=[name for name, _p, _s, _b in RUNGS])
    rearm.set_defaults(func=cmd_rearm)
    boot = sub.add_parser(
        "bootstrap",
        help="bring a BEHIND checkout forward without needing its own copy of this code (#780)",
    )
    boot.add_argument("--root", help="the checkout to repair (default: the one this file lives in)")
    boot.add_argument(
        "--no-reexec",
        action="store_true",
        help="stop after the move instead of re-execing onto the code that just arrived",
    )
    boot.set_defaults(func=cmd_bootstrap)
    caps = sub.add_parser(
        "capabilities",
        help="report the declared capabilities each rung does not implement",
    )
    caps.add_argument(
        "--rung",
        choices=channel.CAPABILITY_RUNGS,
        action="append",
        help="a rung (repeatable; default: every supervised rung)",
    )
    caps.add_argument("--beat", help="read this beat instead of the live one (needs exactly one --rung)")
    caps.add_argument("--commit", help="read the capability declaration at this running commit instead of HEAD (the gate pins a sha)")
    caps.add_argument("--json", action="store_true", help="machine-readable output")
    caps.set_defaults(func=cmd_capabilities)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        # #780: the checkout's freshness is an asserted PRECONDITION of the pass, not
        # an assumption. cron runs this from the checkout, so a pass started from
        # behind the remote is the defect itself — the remedy is executed BEFORE the
        # code it repairs, and re-execs onto the code that just arrived.
        reexec_args = ["run", *(["--force"] if getattr(args, "force", False) else [])]
        preflight = bootstrap_preflight(reexec_args)
        if preflight is not None:
            return preflight
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
