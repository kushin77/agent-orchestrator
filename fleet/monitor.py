#!/usr/bin/env python3
"""Fleet monitor — the cron-owned, change-only progress watcher (the third rung).

WHY it exists: the fleet's brain/sister loops are long-running, and their
observable progress (which rungs are up, which claims are held, what the wave
dispatch list is, what code is at HEAD) used to be watched only by an ad-hoc,
gitignored shell script (``.fleet/open-eye.sh``) that no gate or watchdog knew
about. This module makes that monitor a first-class, TRACKED rung: it runs the
same change-only polling, and ``fleet/watchdog.py`` now respawns it when it is
missing — so the monitor survives a crash or a reboot without a human.

It polls every ``POLL_SECONDS`` and appends ONE timestamped line to
``.fleet/open-eye.log`` only when the observable state changed since the previous
tick (sister/brain state, held claims, wave dispatch list, git HEAD). Every tick
it rewrites ``.fleet/open-eye.heartbeat`` with its own liveness line. It exits
cleanly on SIGTERM/SIGINT. Runtime output lives entirely under the gitignored
``.fleet/`` directory; this module itself is tracked.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLEET_DIR = ROOT / ".fleet"
LOG = FLEET_DIR / "open-eye.log"
HEARTBEAT = FLEET_DIR / "open-eye.heartbeat"
WAVES_DIR = FLEET_DIR / "waves"
SISTER_HEARTBEAT = FLEET_DIR / "sister.heartbeat.json"
BRAIN_HEARTBEAT = FLEET_DIR / "brain.heartbeat.json"
POLL_SECONDS = 20

_stop = False


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _heartbeat_state(path: Path) -> str:
    """The ``state`` field of a rung heartbeat, or ``?`` when unreadable/absent."""
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "?"
    state = beat.get("state") if isinstance(beat, dict) else None
    return state if isinstance(state, str) and state else "?"


def sister_state() -> str:
    return _heartbeat_state(SISTER_HEARTBEAT)


def brain_state() -> str:
    return _heartbeat_state(BRAIN_HEARTBEAT)


def held_claims() -> str:
    """Live-claim lines from the dispatch ledger, joined like the old watcher."""
    try:
        result = subprocess.run(
            ["python3", "governance/dispatch/cli.py", "status"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [ln.strip() for ln in result.stdout.splitlines() if "held by" in ln]
    return "|".join(lines)


def wave_dispatch() -> str:
    """The union of ``dispatched`` issue numbers across every wave file."""
    dispatched: list[str] = []
    if WAVES_DIR.exists():
        for path in sorted(WAVES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            issues = data.get("dispatched") if isinstance(data, dict) else None
            if isinstance(issues, list):
                dispatched.extend(str(item) for item in issues)
    return ",".join(dispatched)


def git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    return result.stdout.strip() or "?"


def snapshot() -> str:
    """One line capturing the fleet's observable state (comparable across ticks)."""
    return (
        f"sister={sister_state()} brain={brain_state()} "
        f"held=[{held_claims()}] dispatched=[{wave_dispatch()}] head={git_head()}"
    )


def _request_stop(signum: int, frame: object) -> None:
    global _stop
    _stop = True


def run() -> int:
    FLEET_DIR.mkdir(parents=True, exist_ok=True)
    # The monitor's stdout is captured to `.fleet/monitor.log` by whoever spawns
    # it (the watchdog, or `control.py`), and that is what the `monitor` window in
    # the `fleet` tmux session tails. Without these two prints the capture would be
    # an empty file that looks like a dead rung.
    print(f"[monitor] up {now_iso()} pid={os.getpid()} poll={POLL_SECONDS}s log={LOG}", flush=True)
    last: str | None = None
    while not _stop:
        current = snapshot()
        stamp = now_iso()
        if current != last:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} {current}\n")
            print(f"[monitor] {stamp} {current}", flush=True)
            last = current
        HEARTBEAT.write_text(f"{stamp} alive pid={os.getpid()}\n", encoding="utf-8")
        # Sleep in small slices so a SIGTERM is honoured within a second.
        for _ in range(POLL_SECONDS):
            if _stop:
                break
            time.sleep(1)
    return 0


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
