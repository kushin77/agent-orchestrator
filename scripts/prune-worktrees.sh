#!/usr/bin/env bash
# prune-worktrees.sh — reclaim stale lane worktrees, safely.
#
# Every lane gets a worktree, so they accumulate. Observed on this machine
# (2026-09-13): 11 stale worktrees under the 16 GiB /tmp tmpfs plus 8 under
# ~/ao-worktrees — the tmpfs ones are also the RAM hazard the repo's own
# conventions warn about, and the operator was left to clean them up by hand.
# That is a human step, so it becomes code.
#
# A worktree is REMOVABLE only when all three hold:
#   1. it is not the current worktree and not the repo's own checkout;
#   2. no live process has it as its cwd;
#   3. its HEAD commit is preserved outside it — reachable from origin/master or
#      contained in a remote branch. Anything unreachable is reported, never
#      deleted: unmerged work belongs to its lane.
#
# Default is a dry run. Pass --apply to remove.
#
# TWO GUARANTEES ADDED FOR ISSUE #516:
#   * a lane the repository still believes is OPEN is never reclaimed, however
#     safely its commits are preserved — its working directory may be all a live
#     agent has. The signals are repository state, not inference: a record in
#     .fleet/lanes/ (deleted when the lane closes), a live pid in .fleet/runs/,
#     or a process sitting in the worktree. If the guard cannot run, nothing is
#     removed (fail closed).
#   * --strict reclaims only work that survives WITHOUT the worktree: a commit
#     already in origin/master, or a detached scratch tree whose commits live on
#     a remote branch. A worktree parked on a local branch is reported PARKED and
#     kept, because its owner may still be working in it.
#
# TWO FACTS, TOLD APART (issue #830):
#   #830 measured the failure this tool could not see for itself: a cron line can
#   be rendered by the schedule's owner, recorded in its manifest and in the
#   image's porting inventory, and unit-tested — and still never run, because
#   nothing ever INSTALLED it. `crontab -l | grep -c prune-worktrees` answered 0
#   while every declaration said otherwise, so the pile #516 had cleared rebuilt
#   (135 worktrees, 3.6G, measured on this box 2026-09-17). Every run therefore
#   reports whether the live crontab actually invokes this tool, and --schedule
#   is that answer as an exit code a scheduler or a gate can consume. The
#   standing is read from the LIVE crontab and never from a file in this
#   repository: a declaration is not an installation, which is what #830 paid
#   for.
#
# Exit-code contract: 0 OK / 1 NOT-OK (stale worktrees found in --check, or — for
# --schedule — no installed crontab line invokes this tool) / 2 CANNOT-ASSESS
# (the question could not be measured).
#
# Usage:
#   bash scripts/prune-worktrees.sh                  # report what would be removed
#   bash scripts/prune-worktrees.sh --apply          # remove the safe ones
#   bash scripts/prune-worktrees.sh --check          # exit 1 if any stale exist
#   bash scripts/prune-worktrees.sh --check --strict  # count only work preserved
#                                                     # outside its own worktree
#   bash scripts/prune-worktrees.sh --schedule       # is THIS TOOL scheduled?
#                                                     # exit 1 if nothing runs it
set -uo pipefail

# The repo to operate on is the one this script is RUN IN, not the one it lives
# in: deriving it from $BASH_SOURCE made the tool ignore its cwd and act on the
# operator's checkout instead (caught by this script's own tests, which were
# passing for that reason).
root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$root" ]; then
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$root" || exit 2

apply=0
check=0
strict=0
schedule=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    --check) check=1 ;;
    --strict) strict=1 ;;
    --schedule) schedule=1 ;;
    -h|--help) sed -n '2,55p' "$0"; exit 0 ;;
    *) echo "prune-worktrees: unknown argument $arg" >&2; exit 2 ;;
  esac
done

current="$root"

scratch="$(mktemp)"
live_cwds="$scratch.cwds"
live_lanes="$scratch.lanes"
crontab_err="$scratch.crontab"
trap 'rm -f "$scratch" "$live_cwds" "$live_lanes" "$crontab_err"' EXIT

# --- is this tool actually scheduled? (issue #830) ---------------------------
#
# ANSWERED BEFORE THE REPOSITORY CHECK, ON PURPOSE: a crontab is a fact about
# the SCHEDULER, not about this checkout, so --schedule has to be answerable
# where the schedule actually lives — including an image whose `.git` is
# excluded from the build context. Everything after that check still needs a
# repository, and still fails closed without one.
#
# READ FROM THE LIVE CRONTAB, NEVER FROM THIS REPOSITORY. Every declaration of
# this schedule — `config/fleet-jobs.json`, `fleet/cron.py`'s marker, the image's
# `infra/fleet/inventory.yaml`, and the tests that pin all three — was in place
# and green while nothing ran this tool, because a declaration is not an
# installation. The only artifact that answers "does anything run this?" is the
# crontab the scheduler actually reads.
#
# A commented-out line does NOT count. `fleet/cron.py disable` comments a line
# out IN PLACE — the marker stays while the job cannot fire — so counting it
# would answer SCHEDULED for a schedule that is switched off.
#
# An unreadable crontab is CANNOT-ASSESS, never NOT-SCHEDULED: "I could not
# look" and "it is not there" are different answers, and collapsing them is how
# a control fails open. The ONE exception is the crontab binary's own
# "no crontab for <user>", which is a measured EMPTY schedule rather than an
# unreadable one.
#
# The matching line is COUNTED, not echoed: the tool must not copy a crontab
# line's text — which can carry an inline credential — into a log.
crontab_rc=0
crontab_text="$(crontab -l 2>"$crontab_err")" || crontab_rc=$?
self_name="$(basename "${BASH_SOURCE[0]}")"
schedule_state="CANNOT-ASSESS"
schedule_detail=""
if [ "$crontab_rc" -ne 0 ] && ! grep -q '^no crontab for ' "$crontab_err"; then
  schedule_detail="crontab -l failed with rc=$crontab_rc: $(head -n 1 "$crontab_err")"
else
  schedule_lines="$(printf '%s\n' "$crontab_text" | grep -v '^[[:space:]]*#' | grep -F -- "$self_name" || true)"
  if [ -n "$schedule_lines" ]; then
    schedule_count="$(printf '%s\n' "$schedule_lines" | wc -l | tr -d ' ')"
    schedule_state="SCHEDULED"
    schedule_detail="$schedule_count installed crontab line(s) invoke $self_name"
  else
    schedule_state="NOT-SCHEDULED"
    schedule_detail="no installed crontab line invokes $self_name — the declaration is not an installation (#830)"
  fi
fi
if [ "$schedule" -eq 1 ]; then
  printf 'prune-worktrees: schedule: %s — %s\n' "$schedule_state" "$schedule_detail"
  case "$schedule_state" in
    SCHEDULED) exit 0 ;;
    NOT-SCHEDULED)
      echo "prune-worktrees: NOT-SCHEDULED — nothing installs a line that runs this tool, so it reclaims nothing; the pile rebuilds (#830)" >&2
      exit 1 ;;
    *)
      echo "prune-worktrees: CANNOT-ASSESS — $schedule_detail" >&2
      exit 2 ;;
  esac
fi

if ! git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
  echo "prune-worktrees: CANNOT-ASSESS — not a git repository" >&2
  exit 2
fi

# cwd of every live process — a worktree in use must never be removed.
for link in /proc/[0-9]*/cwd; do
  readlink "$link" 2>/dev/null || true
done > "$live_cwds"

# Worktrees an OPEN lane still claims (issue #516). A .fleet/lanes/ record is
# deleted when the lane closes, so one that still exists means it never closed; a
# live pid in .fleet/runs/ is a dispatched lane mid-flight. Both are repository
# state, so the guard does not have to guess from a process table.
live_lane_worktrees() { # live_lane_worktrees <repo> — one claimed worktree per line
  python3 - "$1" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])


def git(*args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def read_json(path):
    """An unreadable record keeps MORE, never less: report it as unknown."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


listed = git("worktree", "list", "--porcelain")
if listed.returncode != 0:
    sys.exit(2)
paths = [line[len("worktree ") :] for line in listed.stdout.splitlines() if line.startswith("worktree ")]

# .fleet is NOT inside a lane's worktree: it is gitignored runtime state and lives
# in the main checkout. Reading it relative to whichever checkout the pruner
# happens to run in finds no records and silently disables the guard — which is
# why the suite also runs the pruner from inside a linked worktree.
common = git("rev-parse", "--git-common-dir")
if common.returncode != 0:
    sys.exit(2)
common_dir = Path(common.stdout.strip())
if not common_dir.is_absolute():
    common_dir = root / common_dir
fleet = common_dir.resolve().parent / ".fleet"

unclosed = set()
for record in sorted((fleet / "lanes").glob("*.json")):
    data = read_json(record)
    worktree = data.get("worktree") if data else None
    if worktree:
        unclosed.add(worktree)

live_suffixes = []
for marker in sorted((fleet / "runs").glob("*.json")):
    data = read_json(marker)
    pid = data.get("pid") if data else None
    if not isinstance(pid, int):
        continue
    try:
        os.kill(pid, 0)
    except OSError:
        continue
    live_suffixes.append(marker.stem.split("-")[0][:8])

for path in paths:
    name = Path(path).name
    if path in unclosed or any(suffix and suffix in name for suffix in live_suffixes):
        print(path)
PY
}

if ! live_lane_worktrees "$root" > "$live_lanes" 2>/dev/null; then
  echo "prune-worktrees: CANNOT-ASSESS — the live-lane guard could not run; removing nothing" >&2
  exit 2
fi

landed_in_master() { # landed_in_master <sha> — already part of origin/master
  local sha="$1"
  [ -z "$sha" ] && return 1
  git -C "$root" merge-base --is-ancestor "$sha" origin/master 2>/dev/null
}

on_a_remote_branch() { # on_a_remote_branch <sha> — preserved by a remote branch
  local sha="$1"
  [ -z "$sha" ] && return 1
  [ -n "$(git -C "$root" branch -r --contains "$sha" 2>/dev/null | head -1)" ]
}

preserved() { # preserved <sha> — is this commit reachable outside the worktree?
  landed_in_master "$1" || on_a_remote_branch "$1"
}

stale=0
unsafe=0
while read -r path sha ref; do
  [ -z "$path" ] && continue
  case "$path" in
    "$current") continue ;;
  esac
  dirty="$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$dirty" != "0" ]; then
    printf '  KEEP   %s — %s uncommitted file(s)\n' "$path" "$dirty"
    unsafe=$((unsafe + 1))
    continue
  fi
  if grep -qxF -- "$path" "$live_cwds"; then
    printf '  KEEP   %s — in use by a live process\n' "$path"
    unsafe=$((unsafe + 1))
    continue
  fi
  if grep -qxF -- "$path" "$live_lanes"; then
    printf '  KEEP   %s — claimed by an open lane (this worktree may be all it has)\n' "$path"
    unsafe=$((unsafe + 1))
    continue
  fi
  if ! preserved "$sha"; then
    printf '  KEEP   %s — HEAD %s is not preserved on origin (unmerged lane work)\n' "$path" "$sha"
    unsafe=$((unsafe + 1))
    continue
  fi
  if [ "$strict" -eq 1 ] && ! landed_in_master "$sha" && [ "$ref" != "detached" ]; then
    printf '  PARKED %s — %s holds work preserved only on a remote branch; kept (--strict)\n' "$path" "$ref"
    unsafe=$((unsafe + 1))
    continue
  fi
  stale=$((stale + 1))
  if [ "$apply" -eq 1 ]; then
    git -C "$root" worktree remove --force "$path" >/dev/null 2>&1 \
      && printf '  REMOVED %s (HEAD %s preserved on origin)\n' "$path" "$sha" \
      || printf '  KEEP   %s — removal failed\n' "$path"
  else
    printf '  STALE  %s — removable (HEAD %s preserved on origin)\n' "$path" "$sha"
  fi
done < <(git -C "$root" worktree list --porcelain | awk '
  /^worktree /{ if (p != "") print p, h, (b == "" ? "detached" : b); p=$2; h=""; b=""; next }
  /^HEAD /{ h=$2 }
  /^branch /{ b=$2 }
  END{ if (p != "") print p, h, (b == "" ? "detached" : b) }
' | grep -v "^$current ")

git -C "$root" worktree prune 2>/dev/null || true

echo "prune-worktrees: $stale stale, $unsafe kept (dirty, in use, claimed, parked, or unpreserved)"
# The tool's own standing, on every run — the line #830 needed and did not have.
# REPORTED, never acted on: a missing schedule is a finding for an operator, not
# a reason for the reaper to refuse work it can safely do.
printf 'prune-worktrees: schedule: %s — %s\n' "$schedule_state" "$schedule_detail"
if [ "$check" -eq 1 ] && [ "$stale" -gt 0 ]; then
  echo "prune-worktrees: NOT-OK — $stale stale worktree(s); run with --apply" >&2
  exit 1
fi
exit 0
