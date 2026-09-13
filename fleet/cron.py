#!/usr/bin/env python3
"""The fleet's cron jobs — installed, managed and respawned from this terminal.

TWO marked crontab lines own the fleet, and this module is their single owner:

* the **watchdog** line — every N minutes it runs `fleet/watchdog.py run`, which
  respawns a missing/stale/drifted rung and does nothing when the fleet is
  healthy; and
* the **prune** line — once a day it runs `fleet/prune.py run --apply`, which
  ages out the answered mailbox entries and rotates the append-only ledgers so
  `.fleet/` cannot grow without bound (issue #280).

Cron is the code-native automation this repo sanctions (no GitHub Actions,
GR-15); the same lines are how the fleet survives a reboot or a crashed loop —
and how its own runtime state stays bounded — without a human.

    python3 fleet/cron.py install [--interval 2]   # add/refresh both crontab lines
    python3 fleet/cron.py status                    # what is installed, and recent logs
    python3 fleet/cron.py enable / disable          # toggle without deleting
    python3 fleet/cron.py run                        # run the watchdog once, now
    python3 fleet/cron.py respawn                    # force-respawn both rungs
    python3 fleet/cron.py prune [--apply]            # run the pruner once (dry-run first)
    python3 fleet/cron.py uninstall                  # remove both lines

Each line is identifiable by its trailing marker (`# ao-fleet-watchdog`,
`# ao-fleet-prune`), the same convention the other cron jobs on this box use, so
`uninstall` removes exactly these jobs and `status`/`disable`/`enable` act on
them alone — a foreign crontab line is never touched.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "ao-fleet-watchdog"
LOG = ROOT / ".fleet" / "watchdog.log"

# The retention job (issue #280) is the second marked line: daily, off the
# watchdog's every-N-minutes cadence, because mailbox aging is measured in days.
PRUNE_MARKER = "ao-fleet-prune"
PRUNE_LOG = ROOT / ".fleet" / "prune.log"
PRUNE_SCHEDULE = "23 4 * * *"
# The reconciliation worker (issue #304) is the third marked line. It rides the
# watchdog's cadence rather than a daily one: an orphaned lane blocks work, and a
# pass with no sessions to reconcile is a no-op, so the tick is cheap.
RECONCILE_MARKER = "ao-fleet-reconcile"
RECONCILE_LOG = ROOT / ".fleet" / "reconcile.log"
MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER)


def line(interval: int) -> str:
    return (
        f"*/{interval} * * * * cd {ROOT} && /usr/bin/python3 fleet/watchdog.py run "
        f">> {LOG} 2>&1 # {MARKER}"
    )


def prune_line() -> str:
    return (
        f"{PRUNE_SCHEDULE} cd {ROOT} && /usr/bin/python3 fleet/prune.py run --apply "
        f">> {PRUNE_LOG} 2>&1 # {PRUNE_MARKER}"
    )


def reconcile_line(interval: int) -> str:
    """The orphan sweep, on the watchdog's cadence.

    It is its own line rather than a step inside `watchdog.py run` on purpose: a
    sweep acts on real lanes, and anything the watchdog's pass does is exercised
    by the watchdog's own tests, which must never be able to reclaim a live
    worktree as a side effect.
    """
    return (
        f"*/{interval} * * * * cd {ROOT} && /usr/bin/python3 governance/reconcile/cli.py "
        f"watch --once --apply >> {RECONCILE_LOG} 2>&1 # {RECONCILE_MARKER}"
    )


def _is_ours(entry: str) -> bool:
    """Does this crontab line carry one of our markers (enabled or commented out)?"""
    return any(entry.rstrip().endswith(f"# {marker}") for marker in MARKERS)


def install_lines(lines: list[str], interval: int) -> list[str]:
    """The crontab after an install: our lines refreshed, every other line kept.

    Pure, so the merge is testable without touching the real crontab.
    """
    return [entry for entry in lines if not _is_ours(entry)] + [
        line(interval),
        prune_line(),
        reconcile_line(interval),
    ]


def remove_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split a crontab into (foreign lines kept, our lines removed)."""
    ours = [entry for entry in lines if _is_ours(entry)]
    return [entry for entry in lines if not _is_ours(entry)], ours


def read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def write_crontab(lines: list[str]) -> None:
    content = "\n".join(lines).rstrip("\n") + ("\n" if lines else "")
    subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True, check=True)


def installed_lines(lines: list[str]) -> list[str]:
    return [entry for entry in lines if _is_ours(entry)]


def cmd_install(args: argparse.Namespace) -> int:
    lines = read_crontab()
    merged = install_lines(lines, args.interval)
    write_crontab(merged)
    print(f"cron: installed — watchdog every {args.interval} minute(s): {line(args.interval)}")
    print(f"cron: installed — prune daily ({PRUNE_SCHEDULE}): {prune_line()}")
    print(f"cron: installed — reconcile every {args.interval} minute(s): {reconcile_line(args.interval)}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    lines = read_crontab()
    kept, ours = remove_lines(lines)
    write_crontab(kept)
    print(f"cron: removed {len(ours)} fleet line(s)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    lines = read_crontab()
    own = installed_lines(lines)
    if not own:
        print("cron: NOT installed (install with `python3 fleet/cron.py install`)")
        return 1
    print(f"cron: installed ({len(own)} line(s))")
    for entry in own:
        print(f"  {entry}")
    for label, path in (("watchdog", LOG), ("prune", PRUNE_LOG)):
        if path.exists():
            tail = path.read_text(encoding="utf-8").strip().splitlines()[-3:]
            print(f"recent {label} log:")
            for entry in tail:
                print(f"  {entry}")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    lines = read_crontab()
    changed = 0
    for index, entry in enumerate(lines):
        if _is_ours(entry) and not entry.lstrip().startswith("#"):
            lines[index] = "# " + entry
            changed += 1
    write_crontab(lines)
    print(f"cron: disabled {changed} line(s) (kept, commented out)")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    lines = read_crontab()
    changed = 0
    for index, entry in enumerate(lines):
        if entry.lstrip().startswith("#") and _is_ours(entry):
            stripped = entry.lstrip()
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


def cmd_prune(args: argparse.Namespace) -> int:
    """Run the retention job once, now — dry-run unless `--apply` is passed."""
    command = ["python3", str(ROOT / "fleet" / "prune.py"), "run"]
    if args.apply:
        command.append("--apply")
    return subprocess.call(command, cwd=ROOT)


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
    prune = sub.add_parser("prune", help="run the .fleet retention job once (dry-run unless --apply)")
    prune.add_argument("--apply", action="store_true", help="perform the prune (default: dry-run)")
    prune.set_defaults(func=cmd_prune)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
