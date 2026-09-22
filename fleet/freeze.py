#!/usr/bin/env python3
"""Cutover freeze/drain/thaw — the D7 half of the fleet-cron migration (issue #715).

---knowledge---
module_id: fleet.freeze
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [live_rungs, is_drained, is_frozen, parity_evidence_present, refuse_if_frozen, cmd_status, cmd_drain, cmd_freeze, (+3 more)]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

The owner's cutover policy (issue #706, D7): once the remote container pair
holds lease capability (D5, issue #713) AND dual-run parity evidence (D6,
issue #714) is green, any **locally running** job is allowed to finish, **no
new local dispatch starts**, and only then is the host crontab **frozen**
(commented, never deleted — `fleet/cron.py disable`). `fleet/cron.py enable`
is the rollback. Decommission (removing host state entirely) happens only
after a soak on the frozen state.

This module owns exactly that half of the cutover — it does **not** touch
`fleet/cron.py`, `fleet/watchdog.py` or `fleet/singleton.py` (other lanes own
them; wiring the refusal check into the watchdog's dispatch path is a
follow-up, noted in the PR, not done here).

    python3 fleet/freeze.py status    # flag state, live rung pids, crontab state
    python3 fleet/freeze.py drain     # set .fleet/freeze.flag: refuse new local dispatch
    python3 fleet/freeze.py freeze    # drain must be complete + parity green, then `cron.py disable`
    python3 fleet/freeze.py thaw      # `cron.py enable` + remove the flag (the rollback)

``refuse_if_frozen(rung)`` is the tiny check function other modules (chiefly
the watchdog's dispatch path) are meant to call before starting new local
work for a rung. It is defined here, exported, and — as of this issue —
called from nowhere yet: wiring it into `fleet/watchdog.py` is out of scope
for this lane (watchdog.py is owned elsewhere) and is called out explicitly
in the PR description.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import runtime

ROOT = Path(__file__).resolve().parent.parent
FLEET_DIR = runtime.FLEET_DIR

#: The drain/freeze marker. Its mere existence means "no new local dispatch" —
#: `drain` creates it, `thaw` removes it. It is never used to gate anything
#: destructive by itself: `freeze` additionally requires parity evidence and a
#: fully-drained rung set before it will touch the crontab.
FLAG = FLEET_DIR / "freeze.flag"

#: The rungs whose live pid must be gone before a drain counts as complete.
#: Sourced from fleet/control.py's own LIVE_RUNGS so this module names no
#: rung fleet/control.py does not already recognise.
LIVE_RUNGS = ("brain", "sister", "monitor")

#: Where D6 (issue #714) is specified to write its dual-run parity evidence —
#: named exactly as the issue's acceptance criteria: `{ticks, one_writer: true,
#: diffs: []}`. `freeze` refuses without it.
PARITY_EVIDENCE = FLEET_DIR / "parity.json"


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def _rung_pid(rung: str) -> int | None:
    """The pid a rung's heartbeat last recorded, or None if there is none."""
    path = FLEET_DIR / f"{rung}.heartbeat.json"
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = beat.get("pid")
    try:
        return int(pid)
    except (TypeError, ValueError):
        return None


def live_rungs() -> dict[str, int]:
    """Rung name -> pid, for every LIVE_RUNGS entry whose recorded pid is alive."""
    live: dict[str, int] = {}
    for rung in LIVE_RUNGS:
        pid = _rung_pid(rung)
        if _pid_alive(pid):
            live[rung] = pid  # type: ignore[assignment]
    return live


def is_drained() -> bool:
    """True once no LIVE_RUNGS pid is alive — the precondition `freeze` enforces."""
    return not live_rungs()


def is_frozen() -> bool:
    return FLAG.exists()


def parity_evidence_present() -> bool:
    """D6's evidence file exists and states `one_writer: true` with no diffs.

    Reads exactly the shape issue #714 commits to:
    ``{"ticks": ..., "one_writer": true, "diffs": []}``. Any other shape, or a
    missing/unreadable file, is treated as absent — `freeze` must never
    proceed on evidence it cannot actually verify.
    """
    try:
        data = json.loads(PARITY_EVIDENCE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("one_writer") is True and data.get("diffs") == []


def refuse_if_frozen(rung: str) -> str | None:
    """The tiny check other modules call before starting new local work.

    Returns a human-readable refusal string when the fleet is drained/frozen
    and `rung` should NOT start new work locally, or ``None`` when it is clear
    to proceed. Pure — it only reads `FLAG`, so it is safe to call from any
    dispatch path without side effects.

    Not wired into `fleet/watchdog.py` by this issue: watchdog.py is owned by
    another lane. Wiring this call into its respawn/dispatch path is a
    follow-up (noted in the PR).
    """
    if not FLAG.exists():
        return None
    return (
        f"[{rung}] REFUSED — {FLAG} is set (fleet-cron cutover drain/freeze, "
        f"issue #715): no new local dispatch starts while the flag is present. "
        f"An in-flight run is allowed to finish; this call is for NEW work only. "
        f"Rollback: `python3 fleet/cron.py enable` after `python3 fleet/freeze.py thaw`."
    )


def cmd_status(args: argparse.Namespace) -> int:
    frozen = is_frozen()
    live = live_rungs()
    parity = parity_evidence_present()
    print(f"freeze: flag {'SET' if frozen else 'not set'} ({FLAG})")
    if live:
        print(f"freeze: {len(live)} live local rung(s) — drain NOT complete")
        for rung, pid in live.items():
            print(f"  {rung}: pid {pid}")
    else:
        print("freeze: no live local rungs (drain complete)")
    print(f"freeze: parity evidence ({PARITY_EVIDENCE}) {'GREEN' if parity else 'absent/not-green'}")
    status = subprocess.run(
        ["python3", str(ROOT / "fleet" / "cron.py"), "status"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print("freeze: crontab —")
    print(status.stdout.rstrip() or status.stderr.rstrip())
    return 0


def cmd_drain(args: argparse.Namespace) -> int:
    """Set the flag: refuse new local dispatch. In-flight work is untouched."""
    FLEET_DIR.mkdir(parents=True, exist_ok=True)
    FLAG.write_text(
        json.dumps({"drained_at": time.time(), "reason": "fleet-cron cutover (#715)"}) + "\n",
        encoding="utf-8",
    )
    print(f"freeze: drain set — {FLAG}. No new local dispatch will start.")
    live = live_rungs()
    if live:
        print(f"freeze: {len(live)} rung(s) still live and allowed to finish: {', '.join(live)}")
    else:
        print("freeze: no live local rungs — drain is already complete.")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    """Commit the cutover: requires a complete drain + green parity, then disables cron."""
    if not FLAG.exists():
        print(
            "freeze: REFUSED — not drained. Run `python3 fleet/freeze.py drain` first.",
            file=sys.stderr,
        )
        return 1
    live = live_rungs()
    if live:
        print(
            f"freeze: REFUSED — {len(live)} local rung(s) still running, drain incomplete: "
            f"{', '.join(f'{r}(pid {p})' for r, p in live.items())}",
            file=sys.stderr,
        )
        return 1
    if not parity_evidence_present():
        print(
            f"freeze: REFUSED — dual-run parity evidence ({PARITY_EVIDENCE}) is missing or "
            f"not green (needs one_writer: true, diffs: []). See issue #714.",
            file=sys.stderr,
        )
        return 1
    result = subprocess.run(
        ["python3", str(ROOT / "fleet" / "cron.py"), "disable"], cwd=ROOT
    )
    if result.returncode != 0:
        print("freeze: `fleet/cron.py disable` failed — crontab NOT frozen.", file=sys.stderr)
        return result.returncode
    print("freeze: host crontab frozen (commented, not deleted). Rollback: `python3 fleet/freeze.py thaw`.")
    return 0


def cmd_thaw(args: argparse.Namespace) -> int:
    """The rollback: `fleet/cron.py enable` + remove the flag."""
    result = subprocess.run(["python3", str(ROOT / "fleet" / "cron.py"), "enable"], cwd=ROOT)
    if result.returncode != 0:
        print("freeze: `fleet/cron.py enable` failed — flag left in place.", file=sys.stderr)
        return result.returncode
    if FLAG.exists():
        FLAG.unlink()
    print("freeze: host crontab re-enabled and flag removed — cutover rolled back.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="flag state, live local rung pids, crontab state").set_defaults(
        func=cmd_status
    )
    sub.add_parser(
        "drain", help="set .fleet/freeze.flag: refuse new local dispatch"
    ).set_defaults(func=cmd_drain)
    sub.add_parser(
        "freeze",
        help="drain complete + parity evidence green -> `fleet/cron.py disable`",
    ).set_defaults(func=cmd_freeze)
    sub.add_parser("thaw", help="`fleet/cron.py enable` + remove the flag (rollback)").set_defaults(
        func=cmd_thaw
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
