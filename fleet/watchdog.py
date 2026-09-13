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

RUNGS = (
    ("brain", "fleet/brain.py", "fleet/brain.sh", channel.BRAIN_HEARTBEAT),
    ("sister", "fleet/terminal.py", "fleet/terminal.sh", channel.HEARTBEAT),
)
MONITOR_PATTERN = "fleet/monitor.py"
MONITOR_NAME = "monitor"
FLEET_DIR = ROOT / ".fleet"
RUNS_DIR = FLEET_DIR / "runs"


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


def respawn(pattern: str, script: str, name: str = "") -> bool:
    """Stop the rung (SIGTERM, then SIGKILL) and start it detached.

    `name` selects the capture log (`.fleet/<name>.log`) and defaults to the
    launcher's own stem, which is right for every rung whose log is named after
    its script; the sister passes its rung name explicitly because its launcher
    is `terminal.sh` while the operator's window is `sister`.
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
    spawn(name or Path(script).stem, ["setsid", "bash", str(ROOT / script)])
    return True


def rung_action(name: str, pattern: str, script: str, beat_path: Path, force: bool, head: str) -> str:
    """One rung, one decision: what did the watchdog do about it."""
    pid = loop_pid(pattern)
    state, reason = decide(pid, read_beat(beat_path), head)
    if force:
        state, reason = "forced", "operator asked to respawn"
    if state == "healthy":
        return f"{name}: healthy"
    if state == "drifted" and name == "sister" and run_in_flight():
        return f"{name}: drifted ({reason}) but a run is in flight — left alone"
    ok = respawn(pattern, script, name)
    return f"{name}: {state} ({reason}) — {'respawned' if ok else 'RESPAWN FAILED'}"


def monitor_missing() -> bool:
    """True when no ``fleet/monitor.py`` process is present (simple presence check)."""
    return loop_pid(MONITOR_PATTERN) is None


def start_monitor() -> bool:
    """Start the monitor detached (its own session, like the loop rungs)."""
    try:
        spawn(MONITOR_NAME, ["setsid", "python3", str(ROOT / "fleet" / "monitor.py")])
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def watchdog_once(force: bool = False) -> int:
    """One pass over both loops plus the monitor; 0 (ok/respawned) or 1 (failure)."""
    head = channel.head_commit()
    failed = False
    for name, pattern, script, beat_path in RUNGS:
        line = rung_action(name, pattern, script, beat_path, force, head)
        print(f"[watchdog] {line}", flush=True)
        if "FAILED" in line:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-watchdog", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one watchdog pass (the cron entry point)")
    run.add_argument("--force", action="store_true", help="respawn the loop rungs even when healthy")
    run.set_defaults(func=lambda args: watchdog_once(args.force))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
