#!/usr/bin/env python3
"""The fleet's cron job — installed, managed and respawned from this terminal.

One crontab line owns the brain/sister fleet: every N minutes it runs
`fleet/watchdog.py run`, which respawns a missing/stale/drifted rung and does
nothing when the fleet is healthy. Cron is the code-native automation this repo
sanctions (no GitHub Actions, GR-15); the same line is how the fleet survives a
reboot or a crashed loop without a human.

    python3 fleet/cron.py install [--interval 2]   # add the crontab line
    python3 fleet/cron.py status                    # is it installed, and recent log
    python3 fleet/cron.py enable / disable          # toggle without deleting
    python3 fleet/cron.py run                        # run the watchdog once, now
    python3 fleet/cron.py respawn                    # force-respawn both rungs
    python3 fleet/cron.py uninstall                  # remove the line

The line is identifiable by its trailing marker (`# ao-fleet-watchdog`), the same
convention the other cron jobs on this box use, so `uninstall` removes exactly
this job and `status`/`disable` act on it alone.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "ao-fleet-watchdog"
LOG = ROOT / ".fleet" / "watchdog.log"


def line(interval: int) -> str:
    return (
        f"*/{interval} * * * * cd {ROOT} && /usr/bin/python3 fleet/watchdog.py run "
        f">> {LOG} 2>&1 # {MARKER}"
    )


def read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def write_crontab(lines: list[str]) -> None:
    content = "\n".join(lines).rstrip("\n") + ("\n" if lines else "")
    subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True, check=True)


def installed_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.endswith(f"# {MARKER}")]


def cmd_install(args: argparse.Namespace) -> int:
    lines = read_crontab()
    fresh = line(args.interval)
    lines = [ln for ln in lines if not ln.endswith(f"# {MARKER}")] + [fresh]
    write_crontab(lines)
    print(f"cron: installed — every {args.interval} minute(s): {fresh}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    lines = read_crontab()
    before = installed_lines(lines)
    lines = [ln for ln in lines if not ln.endswith(f"# {MARKER}")]
    write_crontab(lines)
    print(f"cron: removed {len(before)} fleet-watchdog line(s)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    lines = read_crontab()
    own = installed_lines(lines)
    if not own:
        print("cron: NOT installed (install with `python3 fleet/cron.py install`)")
        return 1
    print(f"cron: installed ({len(own)} line(s))")
    for ln in own:
        print(f"  {ln}")
    if LOG.exists():
        tail = LOG.read_text(encoding="utf-8").strip().splitlines()[-5:]
        print("recent watchdog log:")
        for entry in tail:
            print(f"  {entry}")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    lines = read_crontab()
    changed = 0
    for index, ln in enumerate(lines):
        if ln.endswith(f"# {MARKER}") and not ln.lstrip().startswith("#"):
            lines[index] = "# " + ln
            changed += 1
    write_crontab(lines)
    print(f"cron: disabled {changed} line(s) (kept, commented out)")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    lines = read_crontab()
    changed = 0
    for index, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and f"# {MARKER}" in ln:
            stripped = ln.lstrip()
            lines[index] = stripped[2:].lstrip() if stripped.startswith("# ") else stripped[1:].lstrip()
            changed += 1
    write_crontab(lines)
    print(f"cron: enabled {changed} line(s)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return subprocess.call(["python3", str(ROOT / "fleet" / "watchdog.py"), "run"], cwd=ROOT)


def cmd_respawn(args: argparse.Namespace) -> int:
    return subprocess.call(
        ["python3", str(ROOT / "fleet" / "watchdog.py"), "run", "--force"], cwd=ROOT
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-cron", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--interval", type=int, default=2)
    install.set_defaults(func=cmd_install)
    sub.add_parser("uninstall").set_defaults(func=cmd_uninstall)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("enable").set_defaults(func=cmd_enable)
    sub.add_parser("disable").set_defaults(func=cmd_disable)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("respawn").set_defaults(func=cmd_respawn)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
