#!/usr/bin/env python3
"""The fleet's cron jobs — installed, managed and respawned from this terminal.

---knowledge---
module_id: fleet.cron
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [interpreter, load_manifest, manifest_jobs, job_enabled, enabled_jobs, ambient_env, validate_manifest, declared_markers, (+29 more)]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

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

* the **watchdog** line — every N minutes it runs `fleet/watchdog.py run`, which
  respawns a missing/stale/drifted rung and does nothing when the fleet is
  healthy; and
* the **prune** line — once a day it runs `fleet/prune.py run --apply`, which
  ages out the answered mailbox entries and rotates the append-only ledgers so
  `.fleet/` cannot grow without bound (issue #280); and
* the **reap** line — once a day it runs `scripts/prune-worktrees.sh --branches
  --apply`, which reclaims stale LANE WORKTREES (issue #207, #516) so the pile
  that #516 measured does not silently rebuild (issue #830), AND drains LANDED
  LANE BRANCHES (issue #1118, scheduled by #1360). The tool itself is fail
  closed — claimed, dirty, in-use and unpreserved worktrees are always kept —
  so scheduling it daily is the whole fix; nothing here re-implements its
  judgment.

Cron is the code-native automation this repo sanctions (no GitHub Actions,
GR-15); the same lines are how the fleet survives a reboot or a crashed loop —
and how its own runtime state stays bounded — without a human.

    python3 fleet/cron.py status                    # what is installed, and recent logs
    python3 fleet/cron.py enable / disable          # toggle without deleting
    python3 fleet/cron.py run                        # run the watchdog once, now
    python3 fleet/cron.py respawn                    # force-respawn the rungs
    python3 fleet/cron.py prune [--apply]            # run the pruner once (dry-run first)
    python3 fleet/cron.py reap [--apply]             # run the worktree reaper once (dry-run first)
    python3 fleet/cron.py runner [--apply]           # run the PR-runner rung once (issue #1343)
    python3 fleet/cron.py uninstall                  # remove the fleet lines

Each line is identifiable by its trailing marker (`# ao-fleet-watchdog`,
`# ao-fleet-prune`, `# ao-fleet-reconcile`, `# ao-fleet-reap`), the same
convention the other cron jobs on this box use, so `uninstall` removes exactly
these jobs and `status`/`disable`/`enable` act on them alone — a foreign
crontab line is never touched. Every job is a singleton: the rendered line
wraps its command in `flock -n -E 99 <lock>` (a unique lock file per job), so
a tick that overlaps a still-running predecessor exits 99 (skipped) rather
than piling up.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
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

# The worktree reaper (issue #830, closing the gap #516 left open) is the
# fourth marked line: daily, like prune, because a worktree pile grows on the
# scale of a day's lanes, not a watchdog tick. It shells out to
# `scripts/prune-worktrees.sh`, the tool #207/#516 already shipped — this
# module adds no second copy of its reclaim policy, only the schedule.
REAP_MARKER = "ao-fleet-reap"
REAP_LOG = runtime.FLEET_DIR / "reap.log"
REAP_SCHEDULE = "47 3 * * *"

# The portal-promotion rung (issue #1329, parent #1295): every N minutes it
# runs `infra/fleet/promote_portal.py run --apply`, which reads the newest
# immutable Artifact Registry tag reachable from `origin/master`, compares it
# to the running `shared-services-agentconsole` container, and recreates,
# health-gates, rolls back and records as `run_cycle` (promote_portal.py)
# decides. It is declared here (constants + DECLARED_MARKERS + a `promote`
# subcommand) the way #906 declared `reap`; unlike `reap` it is NOT added to
# the enabled `config/fleet-jobs.json` manifest by this lane (out of this
# lane's file scope — see the PR's "## Wiring needed"), so `install` will not
# schedule it until a follow-up lane adds a manifest entry mirroring this
# marker/schedule/command.
PROMOTE_MARKER = "ao-fleet-promote-portal"
PROMOTE_LOG = runtime.FLEET_DIR / "promote-portal.log"
PROMOTE_SCHEDULE = "*/10 * * * *"

# The PR-runner rung (issue #1343, parent #1295): every N minutes it runs
# `fleet/runner/cli.py run --once --apply`, which verifies every open PR head
# lacking evidence, posts `ao/gate-of-record`, and merges the greens through
# `scripts/merge-pr.sh` once the merged tree is proven (docs/PR-RUNNER.md).
# It replaces the nohup prototypes in ~/ao-runner on 192.168.168.42. It is
# ROLE-GATED, not flag-gated: the manifest entry carries
# `enabled_when: {env: AO_RUNNER_HOST_ROLE, equals: primary}` — the env contract
# (infra/fleet/env_contract.py) declares that variable, `enabled_jobs()` reads
# it, and only the shared-services primary renders the line. Everywhere else
# (this box, the image's gate, a standby) the job is declared, recognised by the
# reconciler, and never installed; `cli.py` refuses `host-role-not-primary` as a
# second wall. The rendered line carries the role assignment inline so the
# rung's own log shows which role installed it.
RUNNER_MARKER = "ao-fleet-runner"
RUNNER_LOG = runtime.FLEET_DIR / "runner.log"
RUNNER_SCHEDULE = "*/3 * * * *"
RUNNER_ROLE_ENV = "AO_RUNNER_HOST_ROLE"
RUNNER_ROLE_PRIMARY = "primary"

# The fifth job the manifest declares, ship-gated OFF (issue #241): refreshing
# the committed board snapshot is the one network-touching cron path, so it does
# not change installed behaviour until a principal flips `enabled: true`.
SNAPSHOT_REFRESH_MARKER = "ao-fleet-snapshot-refresh"

# The sixth job the manifest declares, and the second ship-gated OFF (issue
# #1207): the PR-failure scanner is the code-native replacement for the retired
# `ci-failure-scanner.yml` workflow (#812, GR-15), and it FILES a board issue for
# every open PR whose checks concluded FAILURE. Filing is a write, so it ships
# `--apply` behind `enabled: false` and changes nothing until a principal flips
# it on (GR-5). It was previously reachable from nothing — not named `check-*`,
# so `scripts/discover-checks.sh` never wired it; absent from the Makefile, the
# manifest and cron — and declaring it here is what gives it a schedule:
# `install` renders every enabled job, and `reconcile`/`uninstall` recognise a
# stale line for this marker whether or not the job is enabled (below).
SCAN_PR_FAILURES_MARKER = "ao-fleet-scan-pr-failures"

#: The enabled jobs' markers — the lines `install` writes and the image's
#: inventory (`infra/fleet/inventory.yaml`) re-measures. A disabled job's marker
#: is deliberately NOT here: it is declared in the manifest and recognised by the
#: reconciler (so a stale line for it is removed), but never installed.
MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER, REAP_MARKER)

# The PATH block (issue #1370): when AO_FLEET_CRON_PATH is set, `install`
# emits exactly one `PATH=...` line at the top of the managed block so every
# rung — not just the ones this module renders inline — inherits it (the
# runner rung needs `gh`/`gcloud` from /snap/bin; cron's own PATH does not
# have it). Unset, no PATH= line is emitted: current behaviour is unchanged.
#
# The marker rides on its OWN line, directly ABOVE the assignment, and never as
# a trailing comment on it (issue #1416). Cron does not strip a trailing
# comment from an environment-setting line — everything after `=` is the VALUE
# — so the form #1370 shipped, `PATH=/a:/b # ao-fleet-path`, installs
# `PATH=/a:/b # ao-fleet-path`, whose LAST `:`-element is the corrupted
# `/a # ao-fleet-path`-shaped string and is therefore never a directory.
# Measured on the shared-services primary: the corrupted element was `/usr/bin`,
# so every binary that lives only there vanished from cron's PATH — the runner
# rung's role-inline `env` prefix died `flock: failed to execute env: Permission
# denied`, and `fleet/watchdog.py`'s by-name `git` reads all returned nothing so
# every pass was refused `source: CANNOT-ASSESS`. A PATH line that silently
# drops a directory is worse than no PATH line at all: it is a managed line that
# cannot do its job, and its failure is attributed to whatever needed the lost
# binary.
PATH_MARKER = "ao-fleet-path"
#: The block's first line — a crontab comment, recognised by `_marker_of` like
#: any other managed line. The assignment below it carries NO marker, so cron
#: reads exactly the declared value; see `_path_block_indices` for how the pair
#: is kept together.
PATH_COMMENT = f"# {PATH_MARKER}"
PATH_ENV = "AO_FLEET_CRON_PATH"

#: Every marker this module has ever owned, enabled or not. `_is_ours` matches
#: against these so `uninstall`/`reconcile` remove a line whose job is now
#: disabled or dropped, not just one whose schedule drifted.
DECLARED_MARKERS = MARKERS + (
    SNAPSHOT_REFRESH_MARKER,
    SCAN_PR_FAILURES_MARKER,
    PROMOTE_MARKER,
    RUNNER_MARKER,
    PATH_MARKER,
)

#: The interpreter every job's command is rendered with, resolved at INSTALL
#: TIME (never frozen at import) from AO_FLEET_PYTHON — issue #1370: the
#: runner rung needs the ~/ao-verify-venv Python 3.14 venv (3.12's
#: `Path.glob("**")` silently skips files — #1245's knowledge-index red — and
#: 3.14 is what the venv ships), not the box's system `/usr/bin/python3`.
#: Unset, behaviour is unchanged: every job still renders with the literal
#: default below.
DEFAULT_INTERPRETER = "/usr/bin/python3"
FLOCK = "/usr/bin/flock"


def interpreter() -> str:
    """The interpreter to render jobs with, read from the environment NOW
    (install time), not cached — so a changed AO_FLEET_PYTHON is picked up on
    the next `install`/`reconcile`/`status` without restarting anything."""
    return os.environ.get("AO_FLEET_PYTHON", DEFAULT_INTERPRETER)


#: Back-compat alias: some callers/tests still refer to INTERPRETER as a
#: constant. It is the DEFAULT only — the actual render always calls
#: `interpreter()` so AO_FLEET_PYTHON is honoured.
INTERPRETER = DEFAULT_INTERPRETER


# The three enabled jobs, re-expressed here so the module still works (and the
# existing tests still hold) when the manifest is unreadable. When the manifest
# IS readable — the normal state — `_job_by_name` returns its entries instead,
# so the manifest, not this fallback, is the source of truth.
_LEGACY_JOBS = (
    {
        "name": "watchdog",
        "marker": MARKER,
        "interval": 2,
        "command": f"{DEFAULT_INTERPRETER} fleet/watchdog.py run",
        "user": "",
        "log": "watchdog.log",
        "singleton": True,
        "enabled": True,
    },
    {
        "name": "prune",
        "marker": PRUNE_MARKER,
        "schedule": PRUNE_SCHEDULE,
        "command": f"{DEFAULT_INTERPRETER} fleet/prune.py run --apply",
        "user": "",
        "log": "prune.log",
        "singleton": True,
        "enabled": True,
    },
    {
        "name": "reconcile",
        "marker": RECONCILE_MARKER,
        "interval": 2,
        "command": f"{DEFAULT_INTERPRETER} governance/reconcile/cli.py watch --once --apply",
        "user": "",
        "log": "reconcile.log",
        "singleton": True,
        "enabled": True,
    },
    {
        "name": "reap",
        "marker": REAP_MARKER,
        "schedule": REAP_SCHEDULE,
        "command": "bash scripts/prune-worktrees.sh --branches --apply",
        "user": "",
        "log": "reap.log",
        "singleton": False,
        "enabled": True,
    },
    {
        "name": "promote-portal",
        "marker": PROMOTE_MARKER,
        "schedule": PROMOTE_SCHEDULE,
        "command": f"{DEFAULT_INTERPRETER} infra/fleet/promote_portal.py run --apply",
        "user": "",
        "log": "promote-portal.log",
        "singleton": True,
        # Ship-gated OFF, same posture as snapshot-refresh/scan-pr-failures:
        # the manifest (config/fleet-jobs.json) is out of this lane's file
        # scope, so this entry documents the rung `install` WOULD render once
        # a follow-up lane flips it on there — see PROMOTE_MARKER's comment.
        "enabled": False,
    },
    {
        "name": "runner",
        "marker": RUNNER_MARKER,
        "schedule": RUNNER_SCHEDULE,
        "command": f"{DEFAULT_INTERPRETER} fleet/runner/cli.py run --once --apply",
        "user": "",
        "log": "runner.log",
        "singleton": True,
        "enabled": False,
        "enabled_when": {"env": RUNNER_ROLE_ENV, "equals": RUNNER_ROLE_PRIMARY},
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


def job_enabled(job: dict, env: dict[str, str] | None = None) -> bool:
    """Is this job installed HERE? `enabled: true`, or an `enabled_when` env
    condition (`{"env": NAME, "equals": VALUE}`) that the environment satisfies.

    `env` is the environment the condition is judged against. It defaults to
    EMPTY — the flag-only reading — so every reader that does not pass one
    (`infra/fleet/healthz.py`, `scripts/check-fleet-jobs.sh`, the image's
    inventory) derives the same four-rung set on every host regardless of the
    ambient environment; only the installer paths (`install`, `render`,
    `reconcile`, `status`) pass `os.environ` and become role-aware. A malformed
    condition enables nothing: a job that cannot say when it runs does not run.
    """
    if job.get("enabled") is True:
        return True
    condition = job.get("enabled_when")
    if not isinstance(condition, dict):
        return False
    name = str(condition.get("env") or "")
    wanted = condition.get("equals")
    if not name or wanted is None:
        return False
    source = {} if env is None else env
    return source.get(name) == str(wanted)


def enabled_jobs(manifest: dict, env: dict[str, str] | None = None) -> list[dict]:
    """Only the jobs installed here: `enabled: true` or an `enabled_when` that
    `env` satisfies (see `job_enabled`; no `env` = the flag-only set)."""
    return [job for job in manifest_jobs(manifest) if job_enabled(job, env)]


def ambient_env() -> dict[str, str]:
    """The installer's environment — the one place `os.environ` is read here."""
    return dict(os.environ)


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
        condition = job.get("enabled_when")
        if condition is not None and (
            not isinstance(condition, dict) or not str(condition.get("env") or "") or condition.get("equals") is None
        ):
            problems.append(f"{label}: malformed enabled_when (need {{'env': NAME, 'equals': VALUE}})")
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
    condition = job.get("enabled_when")
    if isinstance(condition, dict) and condition.get("env") and condition.get("equals") is not None:
        # A role-gated job carries its role inline: the line only exists on a
        # host whose environment satisfied the condition at install time, and
        # cron's own environment is empty, so the command must restate it.
        parts.append(f"env {condition['env']}={condition['equals']}")
    command = str(job["command"])
    # The manifest (config/fleet-jobs.json) and the legacy fallback both spell
    # a python job's command with the literal default interpreter; substitute
    # AO_FLEET_PYTHON's value here, at render time, so every job — manifest-
    # driven or not — inherits it without templating the manifest itself.
    if command == DEFAULT_INTERPRETER or command.startswith(DEFAULT_INTERPRETER + " "):
        command = interpreter() + command[len(DEFAULT_INTERPRETER):]
    parts.append(command)
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


def reap_line() -> str:
    """The worktree-reap line: daily, applying, shelling out to the tool #207 shipped.

    RENDERED FROM THE MANIFEST — never re-spelled here — exactly like `line`,
    `prune_line` and `reconcile_line` above it. It was the one builder that
    hard-coded its own command, so the same rung had two declarations that could
    drift apart, and issue #1360 measured the drift: adding `--branches` to the
    manifest left this copy re-spelling the old command, and
    `scripts/check-fleet-jobs.sh` refused it by name (LEGACY-BUILDERS-LOCKSTEP,
    and CLEAN-TREE-NOOP, which saw a permanently `refreshed` reap line).
    `_job_by_name` prefers the manifest and falls back to `_LEGACY_JOBS` when it
    cannot be read, so both paths still render a line.

    `prune-worktrees.sh` is fail-closed on its own (claimed/dirty/in-use/
    unpreserved worktrees are always kept), so `--apply` here is safe on the
    same grounds the daily prune line already relies on — and `--branches` is
    the half that drains LANDED LANE BRANCHES, without which every landed branch
    eventually ages past `check-reconcile`'s 24h grace and reds the gate of
    record for every lane (#1360).
    """
    return render_job(_job_by_name("reap"))


def path_lines(env: dict[str, str] | None = None) -> list[str] | None:
    """The managed PATH block — the marker comment, then the assignment — or
    None when AO_FLEET_CRON_PATH is unset.

    TWO crontab lines carrying ONE `PATH=` line. The assignment is clean because
    it has to be: cron's environment-setting line has no comment syntax, so
    anything after the value becomes part of the value (PATH_MARKER). The
    marker therefore leads, on a line of its own, and `_marker_of` recognises it
    by the same trailing-marker convention every other managed line uses.
    """
    source = os.environ if env is None else env
    value = source.get(PATH_ENV, "")
    if not value:
        return None
    return [PATH_COMMENT, f"PATH={value}"]


def _path_block_indices(lines: list[str]) -> set[int]:
    """The indices of the managed PATH block: the marker line, plus the `PATH=`
    assignment directly below it.

    The assignment carries no marker — it must not (PATH_MARKER) — so position
    is the only thing that can identify it: a `PATH=` line that does NOT
    immediately follow our marker is foreign, and is left exactly where it is.
    """
    found: set[int] = set()
    for index, entry in enumerate(lines):
        if _marker_of(entry) != PATH_MARKER:
            continue
        found.add(index)
        if index + 1 < len(lines) and lines[index + 1].strip().startswith("PATH="):
            found.add(index + 1)
    return found


def _path_block(lines: list[str]) -> list[str]:
    """The installed PATH block as it stands, in place — `[]` when there is none."""
    indices = _path_block_indices(lines)
    return [entry for index, entry in enumerate(lines) if index in indices]


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
    env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """The crontab after reconciliation, and what changed.

    Pure, so drift is testable without touching the real crontab: foreign lines
    are kept; a declared job whose line is missing is installed, one whose line
    differs is refreshed, and a marked line whose job is no longer declared is
    reported stale and removed. When AO_FLEET_CRON_PATH (`env`, default
    `os.environ`) is set, one `PATH=...` line (issue #1370) is kept at the TOP
    of the managed block, ahead of every job line, so every rung inherits it —
    preceded by the block's own marker comment (`path_lines`); unset, no such
    line is installed and a leftover block from a prior install is reported
    stale and removed — same lifecycle as a job.
    """
    base = Path(root) if root is not None else ROOT
    user = current_user if current_user is not None else _current_user()
    desired = {
        str(job["marker"]): render_job(job, root=base, current_user=user, interval=interval)
        for job in jobs
    }
    path_indices = _path_block_indices(lines)
    kept = [
        entry
        for index, entry in enumerate(lines)
        if not _is_ours(entry) and index not in path_indices
    ]
    report: dict[str, list[str]] = {"installed": [], "stale": [], "refreshed": [], "clean": []}
    ordered: list[str] = []

    wanted_path = path_lines(env)
    present_path = _path_block(lines)
    if wanted_path is not None:
        if not present_path:
            report["installed"].append(PATH_MARKER)
        elif present_path == wanted_path:
            report["clean"].append(PATH_MARKER)
        else:
            report["refreshed"].append(PATH_MARKER)
        ordered.extend(wanted_path)
    elif present_path:
        report["stale"].extend(present_path)

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
        marker = _marker_of(entry)
        if marker == PATH_MARKER:
            continue  # handled above, whichever way it went
        if _is_ours(entry) and marker not in desired:
            report["stale"].append(entry)
    return kept + ordered, report


def _enabled_jobs_safe() -> list[dict]:
    try:
        return enabled_jobs(load_manifest(), env=ambient_env())
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return [dict(job) for job in _LEGACY_JOBS]


def install_lines(
    lines: list[str], interval: int | None = None, env: dict[str, str] | None = None
) -> list[str]:
    """The crontab after an install: our lines refreshed, every other line kept.

    Pure, so the merge is testable without touching the real crontab. `env`
    defaults to the real process environment (AO_FLEET_PYTHON/AO_FLEET_CRON_PATH
    included); tests pass an explicit dict instead.
    """
    return reconcile_lines(lines, _enabled_jobs_safe(), interval=interval, env=env)[0]


def remove_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split a crontab into (foreign lines kept, our lines removed).

    The PATH block's assignment has no marker of its own (PATH_MARKER), so it is
    removed by position with the marker line above it; leaving it behind would
    keep a PATH export alive after `uninstall`.
    """
    path_indices = _path_block_indices(lines)
    ours = [
        entry
        for index, entry in enumerate(lines)
        if _is_ours(entry) or index in path_indices
    ]
    kept = [
        entry
        for index, entry in enumerate(lines)
        if not _is_ours(entry) and index not in path_indices
    ]
    return kept, ours


def read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def write_crontab(lines: list[str]) -> None:
    content = "\n".join(lines).rstrip("\n") + ("\n" if lines else "")
    subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True, check=True)


def installed_lines(lines: list[str]) -> list[str]:
    """Our managed lines, the PATH block included.

    The block's assignment carries no marker (PATH_MARKER), so it is added by
    position — otherwise `status` would print the marker comment and silently
    omit the line that actually sets the PATH, which is the line a principal
    reads to check the install.
    """
    path_indices = _path_block_indices(lines)
    return [
        entry
        for index, entry in enumerate(lines)
        if _is_ours(entry) or index in path_indices
    ]


def cmd_install(args: argparse.Namespace) -> int:
    lines = read_crontab()
    merged = install_lines(lines, args.interval)
    write_crontab(merged)
    block = path_lines()
    if block is not None:
        print(f"cron: installed — PATH block ({PATH_ENV}): {block[1]}")
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


def config_drift(lines: list[str]) -> list[str]:
    """Named drift between the installed crontab and what AO_FLEET_PYTHON /
    AO_FLEET_CRON_PATH would currently render (issue #1370) — one string per
    variable that differs, empty when both are in sync.

    `AO_FLEET_PYTHON` and `AO_FLEET_CRON_PATH` are declared config (see
    `infra/fleet/env_contract.py`), so `status` treats them the same as any
    manifest job: rendered from the CURRENT environment and compared against
    what is actually installed.
    """
    findings: list[str] = []
    wanted_interp = interpreter()
    own = [entry for entry in lines if _marker_of(entry) and _marker_of(entry) != PATH_MARKER]
    for entry in own:
        marker = _marker_of(entry)
        try:
            job = next(j for j in _enabled_jobs_safe() if str(j["marker"]) == marker)
        except StopIteration:
            continue
        wanted = render_job(job)
        if entry != wanted and wanted_interp in wanted and wanted_interp not in entry:
            findings.append(
                f"AO_FLEET_PYTHON drift: {marker} installed with a different interpreter "
                f"than the current env would render ({wanted_interp!r})"
            )
            break
    wanted_path = path_lines()
    present_path = _path_block(lines)
    if wanted_path is None and present_path:
        findings.append(
            f"AO_FLEET_CRON_PATH drift: a PATH= line is installed but {PATH_ENV} is now unset"
        )
    elif wanted_path is not None and not present_path:
        findings.append(
            f"AO_FLEET_CRON_PATH drift: {PATH_ENV} is set but no PATH= line is installed"
        )
    elif wanted_path is not None and present_path != wanted_path:
        findings.append(
            f"AO_FLEET_CRON_PATH drift: installed PATH= line does not match the current {PATH_ENV}"
        )
    return findings


def cmd_status(args: argparse.Namespace) -> int:
    lines = read_crontab()
    own = installed_lines(lines)
    if not own:
        print("cron: NOT installed (install with `python3 fleet/cron.py install`)")
        return 1
    print(f"cron: installed ({len(own)} line(s))")
    for entry in own:
        print(f"  {entry}")
    drift = config_drift(lines)
    if drift:
        for finding in drift:
            print(f"cron: DRIFT: {finding}")
    else:
        print("cron: AO_FLEET_PYTHON / AO_FLEET_CRON_PATH in-sync with the installed crontab")
    for job in _enabled_jobs_safe():
        path = runtime.FLEET_DIR / str(job["log"])
        if path.exists():
            tail = path.read_text(encoding="utf-8").strip().splitlines()[-3:]
            print(f"recent {job['name']} log:")
            for entry in tail:
                print(f"  {entry}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Print the rendered crontab — what `install` would write.

    The PATH block is part of that, and its absence here (#1416) was how a
    render could look right while `install` wrote a different crontab: the
    block is rendered by `path_lines`, not by a manifest job.
    """
    for entry in (path_lines() or []) + render_lines(_enabled_jobs_safe()):
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
    jobs = enabled_jobs(manifest, env=ambient_env())
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


def cmd_reap(args: argparse.Namespace) -> int:
    """Run the worktree reaper once, now — dry-run unless `--apply` is passed."""
    command = ["bash", str(ROOT / "scripts" / "prune-worktrees.sh")]
    if args.apply:
        command.append("--apply")
    return subprocess.call(command, cwd=ROOT)


def cmd_promote(args: argparse.Namespace) -> int:
    """Run the portal-promotion rung once, now — dry-run unless `--apply` is passed."""
    command = ["python3", str(ROOT / "infra" / "fleet" / "promote_portal.py"), "run"]
    if not args.apply:
        command.append("--dry-run")
    return subprocess.call(command, cwd=ROOT)


def cmd_runner(args: argparse.Namespace) -> int:
    """Run the PR-runner rung once, now — merges are dry-run unless `--apply`."""
    command = ["python3", str(ROOT / "fleet" / "runner" / "cli.py"), "run", "--once"]
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
    reap = sub.add_parser("reap", help="run the stale-worktree reaper once (dry-run unless --apply)")
    reap.add_argument("--apply", action="store_true", help="perform the reap (default: dry-run)")
    reap.set_defaults(func=cmd_reap)
    promote = sub.add_parser(
        "promote", help="run the portal-promotion rung once (dry-run unless --apply)"
    )
    promote.add_argument("--apply", action="store_true", help="perform the promotion (default: dry-run)")
    promote.set_defaults(func=cmd_promote)
    runner = sub.add_parser("runner", help="run the PR-runner rung once (merges dry-run unless --apply)")
    runner.add_argument("--apply", action="store_true", help="really merge (default: verify + post, merges dry-run)")
    runner.set_defaults(func=cmd_runner)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
