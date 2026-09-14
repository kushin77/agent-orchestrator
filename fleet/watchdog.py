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
    """
    handle = open_log(name)
    try:
        return subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
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
    """True when a run marker names a loop pid that is still alive."""
    if not RUNS_DIR.exists():
        return False
    for path in RUNS_DIR.glob("*.json"):
        record = read_beat(path)
        if record and process_alive(record.get("pid")):
            return True
    return False


def decide(pid: int | None, beat: dict | None, head: str) -> tuple[str, str]:
    """Classify a rung: missing / stale / drifted / healthy, with a reason."""
    if pid is None:
        return "missing", "no loop process"
    if beat is None:
        return "stale", "no heartbeat from a live loop"
    age = channel.heartbeat_age_seconds(beat)
    if age is None:
        return "stale", "heartbeat has no timestamp"
    if age > channel.STALE_HEARTBEAT_SECONDS:
        return "stale", f"last beat {int(age)}s ago"
    running = str(beat.get("commit", "unknown"))
    if head != "unknown" and running != head:
        return "drifted", f"running {running}, HEAD {head}"
    return "healthy", ""


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
        except OSError:
            pass
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


def rung_action(name: str, pattern: str, script: str, beat_path: Path, force: bool, head: str) -> str:
    """One rung, one decision: what did the watchdog do about it — and what is it missing?

    Issue #319: the commit comparison (`decide`) is joined by the capability
    comparison, so a rung running a build that predates a merged control reports
    WHICH control is absent instead of only "drifted". A rung that is *current*
    and still missing a declared capability is reported too — and NOT respawned,
    because restarting a build that never had the capability cannot fix it.
    """
    pid = loop_pid(pattern)
    beat = read_beat(beat_path)
    state, reason = decide(pid, beat, head)
    if force:
        state, reason = "forced", "operator asked to respawn"
    capability = channel.capability_line(channel.capability_finding(name, beat, head))
    if state == "healthy":
        return f"{name}: healthy | {capability}"
    if state == "drifted" and name == "sister" and run_in_flight():
        return f"{name}: drifted ({reason}) but a run is in flight — left alone | {capability}"
    ok = respawn(pattern, script, name)
    return f"{name}: {state} ({reason}) — {'respawned' if ok else 'RESPAWN FAILED'} | {capability}"


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
    """One pass over both loops plus the monitor; 0 (ok/respawned) or 1 (failure).

    A pass fails on `RESPAWN FAILED` and on `CAPABILITY STALE`: a rung that does
    not implement a capability the repository declares is a silently absent
    control, and it is the one finding no respawn can repair (issue #319).
    """
    head = channel.head_commit()
    failed = False
    for name, pattern, script, beat_path in RUNGS:
        line = rung_action(name, pattern, script, beat_path, force, head)
        print(f"[watchdog] {line}", flush=True)
        if "FAILED" in line or "CAPABILITY STALE" in line:
            failed = True
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
    return 1 if failed else 0


def beat_path(rung: str) -> Path:
    """The beat this rung publishes (read through `channel`, so a test redirect applies)."""
    return channel.BRAIN_HEARTBEAT if rung == "brain" else channel.HEARTBEAT


def cmd_capabilities(args: argparse.Namespace) -> int:
    """Report the declared capabilities each rung does NOT implement (issue #319).

    Reads the rung's own beat — it never writes one — so the report measures the
    live fleet instead of declaring a capability set on its behalf. `--beat`
    reads a synthetic beat and `--head` pins the comparison commit, which is how
    the runbook gate provokes each of the three cases and requires it to be
    reported (a check that cannot fail is a formality, GR-12).
    """
    rungs = args.rung or [name for name, _pattern, _script, _beat in RUNGS]
    if args.beat and len(rungs) != 1:
        print("watchdog capabilities: --beat needs exactly one --rung", file=sys.stderr)
        return 2
    head = args.head or channel.head_commit()
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
    caps.add_argument("--head", help="compare against this commit instead of HEAD (the gate pins a sha)")
    caps.add_argument("--json", action="store_true", help="machine-readable output")
    caps.set_defaults(func=cmd_capabilities)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
