#!/usr/bin/env python3
"""Brain-side control plane — the human-override terminal.

Run these from the brain/human terminal to steer and maintain the fleet without
stopping it:

    python3 fleet/control.py refresh    # git pull --ff-only + snapshot + verify
    python3 fleet/control.py update     # refresh + rebuild the knowledge index
    python3 fleet/control.py poke       # ping the sister; it acks (liveness)
    python3 fleet/control.py halt       # stop the sister loop cleanly
    python3 fleet/control.py debug      # full non-destructive state dump
    python3 fleet/control.py watch      # idle-watch the slog (same as listen)

Roles (see fleet/directive.json): the sister is a dumb terminal (DeepSeek v4.1
Flash, no thinking); the brain is DSv4PM with human override; THIS terminal is
where the human overrides the entire fleet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = str(ROOT / "fleet" / "channel.py")


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  FAIL {cmd[0]} rc={result.returncode}: {(result.stdout + result.stderr)[-400:]}", file=sys.stderr)
        raise SystemExit(result.returncode or 1)
    return result


def _send_control(action: str) -> None:
    message = {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "control": action,
        "body": f"control:{action}",
    }
    path = ROOT / ".fleet" / f"control-{uuid.uuid4().hex[:8]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(message))
    _run(["python3", CHANNEL, "send", "--message", str(path)])
    path.unlink(missing_ok=True)


def cmd_refresh(args: argparse.Namespace) -> int:
    print("== refresh: pull --ff-only ==")
    _run(["git", "pull", "--ff-only"], check=False)
    print("== refresh: board snapshot ==")
    _run(["python3", "governance/dispatch/cli.py", "snapshot", "--from-github"], check=False)
    print("== refresh: make verify ==")
    _run(["make", "verify"], check=False)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    cmd_refresh(args)
    print("== update: rebuild knowledge index ==")
    _run(["python3", "governance/knowledge/cli.py", "build"], check=False)
    return 0


def cmd_poke(args: argparse.Namespace) -> int:
    _send_control("poke")
    print("poke sent — the sister will ack on its next poll")
    return 0


def cmd_halt(args: argparse.Namespace) -> int:
    _send_control("halt")
    print("halt sent — the sister loop will stop cleanly")
    return 0


def cmd_debug(args: argparse.Namespace) -> int:
    print("== channel ==")
    _run(["python3", CHANNEL, "status"], check=False)
    print("== board ==")
    _run(["python3", "governance/dispatch/cli.py", "status"], check=False)
    print("== slog tail ==")
    slog = ROOT / ".fleet" / "slog.jsonl"
    if slog.exists():
        lines = slog.read_text().splitlines()
        for line in lines[-args.tail :]:
            entry = json.loads(line)
            print(f"  {entry.get('ts')} {entry.get('type')} {entry.get('from')}->{entry.get('to')} corr={entry.get('correlation_id','')[:8]} {entry.get('severity') or '-'}")
    else:
        print("  (no slog yet)")
    print("== sister loop process ==")
    _run(["pgrep", "-af", "fleet/terminal.py"], check=False)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    return subprocess.call(["python3", CHANNEL, "listen", "--timeout-seconds", "0"], cwd=ROOT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-control", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (
        ("refresh", cmd_refresh),
        ("update", cmd_update),
        ("poke", cmd_poke),
        ("halt", cmd_halt),
        ("debug", cmd_debug),
        ("watch", cmd_watch),
    ):
        sub.add_parser(name, help=f"control.{name}").set_defaults(func=func)
    debug = sub.choices["debug"]
    debug.add_argument("--tail", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
