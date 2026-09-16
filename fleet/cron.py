#!/usr/bin/env python3
"""The fleet's cron jobs — installed, managed and respawned from this terminal.

The crontab is generated from a tracked manifest, ``config/fleet-jobs.json``
(issue #241 — the later adaptation of the leaderboard's generated crontab,
parent #160; the pattern is harvested, never copied — docs/CANNIBALIZATION.md
§12). The manifest declares every fleet job: its marker, schedule (or interval),
command, log path, singleton flag and the user it runs as. This module is the
manifest's single renderer and reconciler — the installed crontab is never
hand-edited.

    python3 fleet/cron.py render                    # print the rendered crontab
    python3 fleet/cron.py install [--interval 2]    # add/refresh the fleet lines
    python3 fleet/cron.py reconcile [--apply]       # report and heal drift (dry-run first)
    python3 fleet/cron.py status                    # what is installed, and recent logs
    python3 fleet/cron.py enable / disable          # toggle without deleting
    python3 fleet/cron.py run                        # run the watchdog once, now
    python3 fleet/cron.py respawn                    # force-respawn the rungs
    python3 fleet/cron.py prune [--apply]            # run the pruner once (dry-run first)
    python3 fleet/cron.py uninstall                  # remove the fleet lines

Each line is identifiable by its trailing marker (`# ao-fleet-watchdog`,
`# ao-fleet-prune`, `# ao-fleet-reconcile`), the same convention the other cron
jobs on this box use, so `uninstall` removes exactly these jobs and
`status`/`disable`/`enable` act on them alone — a foreign crontab line is never
touched. Every job is a singleton: the rendered line wraps its command in
`flock -n -E 99 <lock>` (a unique lock file per job), so a tick that overlaps a
still-running predecessor exits 99 (skipped) rather than piling up.
"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
from pathlib import Path

import runtime

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "config" / "fleet-jobs.json"

MARKER = "ao-fleet-watchdog"
LOG = runtime.FLEET_DIR / "watchdog.log"

# The retention job (issue #280) is the second marked line: daily, off the
# watchdog's every-N-minutes cadence, because mailbox aging is measured in days.
PRUNE_MARKER = "ao-fleet-prune"
PRUNE_LOG = runtime.FLEET_DIR / "prune.log"
PRUNE_SCHEDULE = "23 4 * * *"
# The reconciliation worker (issue #304) is the third marked line. It rides the
# watchdog's cadence rather than a daily one: an orphaned lane blocks work, and a
# pass with no sessions to reconcile is a no-op, so the tick is cheap.
RECONCILE_MARKER = "ao-fleet-reconcile"
RECONCILE_LOG = runtime.FLEET_DIR / "reconcile.log"

# The fourth job the manifest declares, ship-gated OFF (issue #241): refreshing
# the committed board snapshot is the one network-touching cron path, so it does
# not change installed behaviour until an operator flips `enabled: true`.
SNAPSHOT_REFRESH_MARKER = "ao-fleet-snapshot-refresh"

#: The enabled jobs' markers — the lines `install` writes and the image's
#: inventory (`infra/fleet/inventory.yaml`) re-measures. A disabled job's marker
#: is deliberately NOT here: it is declared in the manifest and recognised by the
#: reconciler (so a stale line for it is removed), but never installed.
MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER)

#: Every marker this module has ever owned, enabled or not. `_is_ours` matches
#: against these so `uninstall`/`reconcile` remove a line whose job is now
#: disabled or dropped, not just one whose schedule drifted.
DECLARED_MARKERS = MARKERS + (SNAPSHOT_REFRESH_MARKER,)

INTERPRETER = "/usr/bin/python3"
FLOCK = "/usr/bin/flock"


# The three enabled jobs, re-expressed here so the module still works (and the
# existing tests still hold) when the manifest is unreadable. When the manifest
# IS readable — the normal state — `_job_by_name` returns its entries instead,
# so the manifest, not this fallback, is the source of truth.
_LEGACY_JOBS = (
    {
        "name": "watchdog",
        "marker": MARKER,
        "interval": 2,
        "command": f"{INTERPRETER} fleet/watchdog.py run",
        "user": "",
        "log": "watchdog.log",
        "singleton": True,
        "enabled": True,
    },
    {
        "name": "prune",
        "marker": PRUNE_MARKER,
        "schedule": PRUNE_SCHEDULE,
        "command": f"{INTERPRETER} fleet/prune.py run --apply",
        "user": "",
        "log": "prune.log",
        "singleton": True,
        "enabled": True,
    },
    {
        "name": "reconcile",
        "marker": RECONCILE_MARKER,
        "interval": 2,
        "command": f"{INTERPRETER} governance/reconcile/cli.py watch --once --apply",
        "user": "",
        "log": "reconcile.log",
        "singleton": True,
        "enabled": True,
    },
)


def load_manifest(path: Path | None = None) -> dict:
    """Read the fleet-jobs manifest; a missing or malformed file is an error.

    A missing manifest is a defect, not an empty default: `install`/`reconcile`
    fall back to `_LEGACY_JOBS` only so the legacy image/dev-run surfaces keep
    working, while `scripts/check-fleet-jobs.sh` fails the gate when this file
    is gone.
    """
    target = Path(path) if path is not None else MANIFEST_PATH
    with target.open(encoding="utf-8") as handle:
        return json.load(handle)


def manifest_jobs(manifest: dict) -> list[dict]:
    return list(manifest.get("jobs", []))


def enabled_jobs(manifest: dict) -> list[dict]:
    """Only the jobs whose `enabled` flag is set — what gets installed."""
    return [job for job in manifest_jobs(manifest) if job.get("enabled") is True]


def validate_manifest(manifest: dict) -> list[str]:
    """Named problems in the manifest, [] when it meets the renderer's contract.

    The renderer will not invent a missing field: a job without a command, a
    schedule/interval, a log or a unique marker is refused by name here, so the
    check can fail the gate for exactly the field a lane dropped.
    """
    problems: list[str] = []
    if not isinstance(manifest, dict) or not isinstance(manifest.get("jobs"), list):
        return ["manifest-shape: 'jobs' must be a list of job objects"]
    seen: dict[str, int] = {}
    for index, job in enumerate(manifest["jobs"]):
        label = f"jobs[{index}]"
        if not isinstance(job, dict):
            problems.append(f"{label}: not an object")
            continue
        name = str(job.get("name") or "")
        marker = str(job.get("marker") or "")
        if not name:
            problems.append(f"{label}: missing name")
        if not marker:
            problems.append(f"{label}: missing marker")
        elif marker in seen:
            problems.append(f"{label}: duplicate marker {marker} (also jobs[{seen[marker]}])")
        else:
            seen[marker] = index
        if not str(job.get("command") or "").strip():
            problems.append(f"{label}: missing command")
        if job.get("interval") is None and not str(job.get("schedule") or "").strip():
            problems.append(f"{label}: missing schedule (need 'interval' or 'schedule')")
        if not str(job.get("log") or "").strip():
            problems.append(f"{label}: missing log")
    return problems


def declared_markers(manifest: dict) -> tuple[str, ...]:
    """Every job's marker — enabled and disabled — in manifest order."""
    return tuple(str(job["marker"]) for job in manifest_jobs(manifest) if job.get("marker"))


def _current_user() -> str:
    try:
        return getpass.getuser()
    except OSError:
        return ""


def _schedule_of(job: dict, interval: int | None = None) -> str:
    """The cron schedule for a job: its `interval` (overridable) or its `schedule`."""
    if job.get("interval") is not None:
        minutes = interval if interval is not None else int(job["interval"])
        return f"*/{minutes} * * * *"
    return str(job.get("schedule", ""))


def render_job(
    job: dict,
    *,
    root: Path | None = None,
    current_user: str | None = None,
    interval: int | None = None,
) -> str:
    """One crontab line, rendered from a manifest job (pure — no crontab I/O).

    The line carries the job's schedule, `cd <root>`, a `sudo -u <user>` prefix
    only when the declared user differs from the current one (a no-op render on
    this box, where the fleet runs as the current user), the `flock -n -E 99`
    singleton wrapper when the job declares `singleton`, the command, and the
    job's own log path and trailing marker.
    """
    base = Path(root) if root is not None else ROOT
    user = current_user if current_user is not None else _current_user()
    schedule = _schedule_of(job, interval)
    marker = str(job["marker"])
    log_name = str(job.get("log") or f"{job.get('name', 'job')}.log")
    log_path = runtime.FLEET_DIR / log_name
    parts = [schedule, f"cd {base} &&"]
    declared_user = str(job.get("user") or "")
    if declared_user and declared_user != user:
        parts.append(f"sudo -u {declared_user}")
    if job.get("singleton") is True:
        lock = runtime.FLEET_DIR / (Path(log_name).stem + ".lock")
        parts.append(f"{FLOCK} -n -E 99 {lock}")
    parts.append(str(job["command"]))
    return " ".join(parts) + f" >> {log_path} 2>&1 # {marker}"


def _job_by_name(name: str) -> dict:
    try:
        for job in manifest_jobs(load_manifest()):
            if job.get("name") == name:
                return job
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    for job in _LEGACY_JOBS:
        if job["name"] == name:
            return dict(job)
    raise KeyError(name)


def line(interval: int) -> str:
    return render_job(_job_by_name("watchdog"), interval=interval)


def prune_line() -> str:
    return render_job(_job_by_name("prune"))


def reconcile_line(interval: int) -> str:
    """The orphan sweep, on the watchdog's cadence.

    It is its own line rather than a step inside `watchdog.py run` on purpose: a
    sweep acts on real lanes, and anything the watchdog's pass does is exercised
    by the watchdog's own tests, which must never be able to reclaim a live
    worktree as a side effect.
    """
    return render_job(_job_by_name("reconcile"), interval=interval)


def render_lines(
    jobs: list[dict],
    *,
    root: Path | None = None,
    current_user: str | None = None,
    interval: int | None = None,
) -> list[str]:
    """The multi-job crontab the manifest declares, one line per job, in order."""
    return [render_job(job, root=root, current_user=current_user, interval=interval) for job in jobs]


def _marker_of(entry: str) -> str:
    for marker in DECLARED_MARKERS:
        if entry.rstrip().endswith(f"# {marker}"):
            return marker
    return ""


def _is_ours(entry: str) -> bool:
    """Does this crontab line carry one of our markers (enabled or commented out)?"""
    return _marker_of(entry) != ""


def reconcile_lines(
    lines: list[str],
    jobs: list[dict],
    *,
    root: Path | None = None,
    current_user: str | None = None,
    interval: int | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """The crontab after reconciliation, and what changed.

    Pure, so drift is testable without touching the real crontab: foreign lines
    are kept; a declared job whose line is missing is installed, one whose line
    differs is refreshed, and a marked line whose job is no longer declared is
    reported stale and removed.
    """
    base = Path(root) if root is not None else ROOT
    user = current_user if current_user is not None else _current_user()
    desired = {
        str(job["marker"]): render_job(job, root=base, current_user=user, interval=interval)
        for job in jobs
    }
    kept = [entry for entry in lines if not _is_ours(entry)]
    report: dict[str, list[str]] = {"installed": [], "stale": [], "refreshed": [], "clean": []}
    ordered: list[str] = []
    for job in jobs:
        marker = str(job["marker"])
        wanted = desired[marker]
        present = [entry for entry in lines if _marker_of(entry) == marker]
        if not present:
            report["installed"].append(marker)
        elif present == [wanted]:
            report["clean"].append(marker)
        else:
            report["refreshed"].append(marker)
        ordered.append(wanted)
    for entry in lines:
        if _is_ours(entry) and _marker_of(entry) not in desired:
            report["stale"].append(entry)
    return kept + ordered, report


def _enabled_jobs_safe() -> list[dict]:
    try:
        return enabled_jobs(load_manifest())
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return [dict(job) for job in _LEGACY_JOBS]


def install_lines(lines: list[str], interval: int | None = None) -> list[str]:
    """The crontab after an install: our lines refreshed, every other line kept.

    Pure, so the merge is testable without touching the real crontab.
    """
    return reconcile_lines(lines, _enabled_jobs_safe(), interval=interval)[0]


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
    for job in _enabled_jobs_safe():
        print(f"cron: installed — {job['name']} ({_schedule_of(job, args.interval)}): "
              f"{render_job(job, interval=args.interval)}")
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
    for job in _enabled_jobs_safe():
        path = runtime.FLEET_DIR / str(job["log"])
        if path.exists():
            tail = path.read_text(encoding="utf-8").strip().splitlines()[-3:]
            print(f"recent {job['name']} log:")
            for entry in tail:
                print(f"  {entry}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Print the rendered crontab — what `install` would write."""
    for entry in render_lines(_enabled_jobs_safe()):
        print(entry)
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Report drift between the installed crontab and the manifest; heal with --apply.

    Dry-run by default (the repo's tri-state convention for anything that acts):
    exit 0 clean, 1 drift present but not applied, 2 when the manifest cannot be
    read. Never touches the crontab without --apply.
    """
    try:
        manifest = load_manifest()
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"reconcile: CANNOT-ASSESS — the manifest could not be read ({exc})", file=sys.stderr)
        return 2
    jobs = enabled_jobs(manifest)
    lines = read_crontab()
    merged, report = reconcile_lines(lines, jobs)
    for change in ("installed", "refreshed"):
        for marker in report[change]:
            print(f"reconcile: {change}: {marker}")
    for entry in report["stale"]:
        print(f"reconcile: stale: {entry}")
    for marker in report["clean"]:
        print(f"reconcile: clean: {marker}")
    drift = bool(report["installed"] or report["stale"] or report["refreshed"])
    if not drift:
        print("reconcile: clean — the installed crontab matches the manifest")
        return 0
    if args.apply:
        write_crontab(merged)
        print("reconcile: applied — the installed crontab now matches the manifest")
        return 0
    print("reconcile: drift present — re-run with --apply to heal")
    return 1


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
    sub.add_parser("render").set_defaults(func=cmd_render)
    reconcile = sub.add_parser(
        "reconcile", help="report drift between the crontab and the manifest (heal with --apply)"
    )
    reconcile.add_argument("--apply", action="store_true", help="heal the drift (default: dry-run)")
    reconcile.set_defaults(func=cmd_reconcile)
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
