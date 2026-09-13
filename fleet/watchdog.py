#!/usr/bin/env python3
"""Fleet watchdog — the cron-owned keeper of the brain/sister loops.

Run by cron (installed with ``python3 fleet/cron.py install``) or by hand
(``python3 fleet/watchdog.py run`` / ``--force``). It respawns a missing, stale or
drifted rung and does nothing when the fleet is healthy, so a cron tick is cheap
and idempotent.

Safety, the one rule a watchdog must never break: **a run in flight is never
restarted just to update code.** A *missing* loop is respawned regardless — its
run is already orphaned and the fresh loop self-heals the claim. A *drifted*
(healthy but old-code) sister is respawned only when idle.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet"))

import channel  # noqa: E402

RUNGS = (
    ("brain", "fleet/brain.py", "fleet/brain.sh", channel.BRAIN_HEARTBEAT),
    ("sister", "fleet/terminal.py", "fleet/terminal.sh", channel.HEARTBEAT),
)
RUNS_DIR = ROOT / ".fleet" / "runs"


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


def respawn(pattern: str, script: str) -> bool:
    """Stop the rung (SIGTERM, then SIGKILL) and start it detached."""
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
    subprocess.Popen(
        ["setsid", "bash", str(ROOT / script)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
    ok = respawn(pattern, script)
    return f"{name}: {state} ({reason}) — {'respawned' if ok else 'RESPAWN FAILED'}"


def watchdog_once(force: bool = False) -> int:
    """One pass over both rungs; returns 0 (all ok/respawned) or 1 (a failure)."""
    head = channel.head_commit()
    failed = False
    for name, pattern, script, beat_path in RUNGS:
        line = rung_action(name, pattern, script, beat_path, force, head)
        print(f"[watchdog] {line}", flush=True)
        if "FAILED" in line:
            failed = True
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-watchdog", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one watchdog pass (the cron entry point)")
    run.add_argument("--force", action="store_true", help="respawn both rungs even when healthy")
    run.set_defaults(func=lambda args: watchdog_once(args.force))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
