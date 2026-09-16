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
(healthy but old-code) sister is respawned only when idle.

Every rung this module spawns is started detached with its stdout+stderr
appended to a per-rung capture log, `.fleet/<rung>.log`. Spawning used to send
both streams to `DEVNULL`, so the brain — the middle rung of the hierarchy — was
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
are different facts, and only the second tells an operator what is absent. A rung
that is *current* and still missing a declared capability is reported and never
respawned: no restart adds a capability the build does not have. `python3
fleet/watchdog.py capabilities` prints that report on its own.

**The remedy is bounded (issue #773, AO-GR-21).** Drift detection is only half a
control; the other half is an action that can actually change what was compared.
Measured 2026-09-15: `#739` made the watchdog compare the running commit against
`origin/master`, and on a mismatch it respawned — but a respawn re-executes the
*same checkout*. When the drift was the checkout being behind, the watchdog took
an action that could not change the compared value and repeated it without bound:
**132 `drifted … — respawned` decisions, 45 clean stops, a brain process never
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
an operator rearms it (`python3 fleet/watchdog.py rearm --rung <name>`) or the
rung's state changes on its own. A drifted rung that is busy is *recorded* as
pending drift and acted on when the run completes, instead of being dropped every
tick.
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
import runtime  # noqa: E402

from governance.spawn import liveness as spawn_liveness  # noqa: E402  (#793)

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
# failure, not a spawn the operator can trust.
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

# ── the remedy that does not live in the checkout (issue #780, AO-GR-25) ─────
# The `checkout-behind` remedy above is right, and it could not run: cron ran the
# CHECKOUT's copy of this module, so a stale checkout ran a stale watchdog and the
# fast-forward that would have repaired it lived in the code that copy could not
# see. Measured 2026-09-15: the sister ran `592b132` for ~5.5 hours while
# `origin/master` was `3a44f27`, and a HUMAN ran the fast-forward.
#
# So the remedy is now executed from a PINNED path OUTSIDE every checkout —
# `scripts/checkout-bootstrap.sh --install` writes it there, from the REMOTE's
# bytes — and this module PREFERS it whenever it exists. The pinned copy is not
# moved by a fast-forward, and it re-executes the remote's copy when it has itself
# fallen behind, so no copy of the fleet code has to be current for the checkout
# to be brought forward. When no pinned copy is installed this module falls back
# to the in-process remedy below, unchanged, and the returned detail says which
# one ran — a remedy whose provenance is unstated is a remedy nobody can audit.
ENV_BOOTSTRAP = "AO_FLEET_BOOTSTRAP"
#: The pinned bootstrap's default home, deliberately outside any checkout.
DEFAULT_BOOTSTRAP = Path.home() / ".ao-fleet" / "checkout-bootstrap.sh"


def checkout_bootstrap_path() -> Path | None:
    """The PINNED bootstrap, or None when this host has not installed one.

    Resolution is one honoured override plus one default, never a search of the
    checkout: a candidate inside the checkout would be the stale copy again, which
    is the defect this exists to remove. A pinned path that is present but not
    executable is still returned — the caller runs it through `bash`, so the
    executable bit is not a way for the remedy to silently disappear.
    """
    override = os.environ.get(ENV_BOOTSTRAP, "").strip()
    candidate = Path(override) if override else DEFAULT_BOOTSTRAP
    return candidate if candidate.is_file() else None


def _last_line(text: str) -> str:
    """The last non-empty line of a command's output — the bootstrap's own verdict."""
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1].strip() if lines else "no output"


def _fast_forward_via_bootstrap(
    target: Path, script: Path, remote: str, before: str
) -> tuple[bool, str, str]:
    """Run the PINNED bootstrap and translate its tri-state into our tuple.

    The bootstrap's exit code is the verdict (0 current/moved, 1 refused, 2
    cannot-assess) and its last line names both commits, so the detail this module
    returns is the bootstrap's own finding rather than a re-derivation of it. A
    refusal keeps the word `refused`: the attempt counter and the escalation text
    downstream both read it.
    """
    try:
        result = subprocess.run(
            ["bash", str(script), str(target), "--ref", remote],
            capture_output=True,
            text=True,
            timeout=FAST_FORWARD_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, before, f"the pinned bootstrap {script} failed ({type(exc).__name__}: {exc})"
    after = _git_head(target)
    finding = _last_line(f"{result.stdout}\n{result.stderr}")
    if result.returncode == 0:
        if before != "unknown" and after != before:
            return True, after, f"fast-forwarded {before} -> {after} (pinned bootstrap: {finding})"
        return False, after, f"the checkout is already at {after} (pinned bootstrap: {finding})"
    if result.returncode == 2:
        return False, after, f"cannot-assess — the pinned bootstrap could not read the checkout ({finding})"
    return False, after, f"the pinned bootstrap refused the fast-forward ({finding})"


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
    is what must move. It is executed by the PINNED bootstrap when one is
    installed (#780: the remedy must not be the stale copy of this file), and
    in-process otherwise — `git fetch origin` first (the fetched ref is what the
    drift baseline reads), then `git merge --ff-only`. A refusal is reported, not
    swallowed: a diverged branch is `cannot fast-forward`, and the caller counts
    the attempt so the bound still applies.

    Never raises: a network-blocked or unreadable checkout is a recorded failed
    attempt, not a crash — a watchdog that dies stops watching.
    """
    target = Path(root) if root is not None else ROOT
    before = _git_head(target)
    pinned = checkout_bootstrap_path()
    if pinned is not None:
        return _fast_forward_via_bootstrap(target, pinned, remote, before)
    try:
        fetched = _git(target, ["fetch", "origin"])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, before, f"git fetch origin failed ({type(exc).__name__}: {exc})"
    if fetched.returncode != 0:
        return False, before, f"git fetch origin failed ({(fetched.stderr or '').strip().splitlines()[-1:] or ['no output']})"
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


def rung_log(name: str) -> Path:
    """The capture log for a rung: `.fleet/<rung>.log`.

    The path is the contract between the writer (this module, and
    `control.py` when it starts a rung), the reader (`fleet/console.py`) and the
    operator's windows (`fleet/run-fleet.sh` tails exactly this file).
    """
    return FLEET_DIR / f"{name}.log"


def open_log(name: str) -> TextIO:
    """Open a rung's capture log for appending, created if missing, line-buffered.

    Line buffering matters for the operator, not the machine: a rung writes a few
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
    without this the whole chain (watchdog -> launcher -> loop -> subagent)
    inherits cron's minimal PATH: measured 2026-09-14 (#733), the sister could not
    spawn a single subagent because ``~/.local/bin`` was not on it.
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
    sister loop — the drift lock was held open on every tick, the sister's own
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
    its script; the sister passes its rung name explicitly because its launcher
    is `terminal.sh` while the operator's window is `sister`.

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
    try:
        spawn(name or Path(script).stem, ["setsid", "bash", str(ROOT / script)])
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

    Requirement 3 of #773: the sister logged `drifted … but a run is in flight —
    left alone` on **every** tick and so was never updated even after its run
    finished. The protection (never restart a run to update code) is right and is
    kept; dropping the finding is not. The record carries no `next_attempt_at`, so
    the first pass after the run completes acts on it immediately.
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
    same_observation = record.get("state") == state and record.get("running") == running
    previous_attempts = int(record.get("attempts") or 0)
    attempts = previous_attempts + 1 if (same_observation and previous_attempts > 0) else 1

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
            ok = respawn(pattern, script, name)
            outcome = (
                f"fast-forwarded the checkout ({detail}) and {'respawned' if ok else 'RESPAWN FAILED'} "
                f"so the rung loads {new_head} (attempt {attempts}/{cap})"
            )
        else:
            ok = True
            outcome = (
                f"fast-forward did not move the checkout ({detail}) — DRIFT UNRESOLVED, "
                f"attempt {attempts}/{cap}"
            )
    else:
        ok = respawn(pattern, script, name)
        outcome = f"{'respawned' if ok else 'RESPAWN FAILED'} (attempt {attempts}/{cap})"

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
    and the `origin/master` baseline it was judged against — so an operator can
    audit the comparison instead of trusting the verdict. A comparison against a
    baseline that is stale, or against the local checkout, is invisible in a bare
    `healthy`.

    Issue #773: every acting path goes through `bounded_remedy`, so no remedy is
    repeated indefinitely, and the `checkout-behind` case is repaired by moving
    the CHECKOUT rather than by respawning a rung that is already on its HEAD.
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
        ok = respawn(pattern, script, name)
        clear_drift_record(name)
        return f"{name}: forced (operator asked to respawn) — {'respawned' if ok else 'RESPAWN FAILED'} | {capability}"
    if state in (DRIFTED, CHECKOUT_BEHIND) and name == "sister" and run_in_flight():
        record_pending(name, state, reason, running, baseline, baseline_name, local_head, moment)
        return (
            f"{name}: {state} ({reason}) — recorded as pending drift, {flight_evidence()} — left alone "
            f"until it completes, then acted on | {capability}"
        )
    # CANNOT_ASSESS respawns too: the watchdog cannot certify the rung, and a
    # respawn is the only action that can restore a readable comparison. It is
    # reported with its reason so the operator sees WHY it could not be judged —
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
    return f"{name}: {state} ({reason}) — {outcome} | {capability}"


def monitor_missing() -> bool:
    """True when no ``fleet/monitor.py`` process is present (simple presence check)."""
    return loop_pid(MONITOR_PATTERN) is None


def start_monitor(*, window: float | None = None, settle: float | None = None) -> bool:
    """Start the monitor detached (its own session) and verify it came up.

    Like `respawn`, a successful `Popen` is not a running monitor: a monitor that
    dies at startup must surface as `RESPAWN FAILED` so the pass exits non-zero.
    """
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
        ok = start_monitor()
        print(f"[watchdog] monitor: missing — {'respawned' if ok else 'RESPAWN FAILED'}", flush=True)
        if not ok:
            failed = True
    else:
        print("[watchdog] monitor: healthy", flush=True)
    if failed:
        return channel.EXIT_NOT_OK
    if unassessable:
        return channel.EXIT_CANNOT_ASSESS
    return channel.EXIT_OK


def beat_path(rung: str) -> Path:
    """The beat this rung publishes (read through `channel`, so a test redirect applies)."""
    return channel.BRAIN_HEARTBEAT if rung == "brain" else channel.HEARTBEAT


def cmd_rearm(args: argparse.Namespace) -> int:
    """Clear a rung's attempt record after an operator has fixed the cause (#773).

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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
