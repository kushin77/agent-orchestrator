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
#   2. no live process has it as its cwd AND no live process holds an OPEN FILE
#      under it (widened for issue #1159 — cwd alone cannot see a peer driving a
#      worktree from the shared shell);
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
# THREE MORE GUARANTEES ADDED FOR ISSUE #830:
#   * a worktree whose ONLY uncommitted paths are DECLARED runtime state — files a
#     MACHINE rewrote, not the lane — is no longer kept for ever (#830 measured 11
#     of 66 keeps held by a single such file). The declared set is READ, in exactly
#     one place, from `governance/isolation/worktree.py`'s MACHINE_MANAGED_PATHS
#     and MACHINE_MANAGED_PREFIXES (the latter for a whole gitignored directory
#     such as `.fleet/`, rather than one file at a time);
#     if it cannot be read NOTHING is excused and the stricter rule stands.
#   * LANE BRANCHES get a reaper (--branches): a local branch whose own change is
#     provably on origin/master is reapable. "Landed" is decided by CONTENT
#     EQUIVALENCE, never by ancestry — this repo squash-merges, so a fully-landed
#     branch is NEVER an ancestor of master, and an ancestry test keeps it for
#     ever. A branch this cannot prove is KEPT.
#   * TWO FACTS, TOLD APART: #830 also measured the failure this tool could not
#     see for itself — a cron line can be rendered by the schedule's owner,
#     recorded in its manifest and in the image's porting inventory, and
#     unit-tested — and still never run, because nothing ever INSTALLED it.
#     `crontab -l | grep -c prune-worktrees` answered 0 while every declaration
#     said otherwise, so the pile #516 had cleared rebuilt (135 worktrees, 3.6G,
#     measured on this box 2026-09-17). Every run therefore reports whether the
#     live crontab actually invokes this tool, and --schedule is that answer as
#     an exit code a scheduler or a gate can consume. The standing is read from
#     the LIVE crontab and never from a file in this repository: a declaration
#     is not an installation, which is what #830 paid for.
#
# ONE MORE GUARANTEE WIDENED FOR ISSUE #1159:
#   * LIVENESS IS cwd OR ANY OPEN FILE, never cwd alone. A peer driving a worktree
#     from the SHARED shell keeps its cwd elsewhere and holds the tree open
#     through file descriptors, so the old predicate classified LIVE trees as
#     removable — measured on this box: 10 live `.claude/worktrees/agent-*` trees
#     plus 4 scratch trees were reported "stale" while their working directories
#     had been written 5-31 minutes earlier (#1159). The nightly `ao-fleet-reap`
#     line runs this tool with --apply UNATTENDED, so that was a data-loss hazard
#     rather than a report. A live path matches a worktree when it IS that
#     worktree or lies UNDER it on a path boundary: an open file is always under
#     the tree ("equal" alone would make the new source useless), while a bare
#     prefix test would let /tmp/ao/a11 keep /tmp/ao/a115 — one lane's tree
#     holding another lane's.
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
#   bash scripts/prune-worktrees.sh --branches        # ALSO report landed lane branches
#   bash scripts/prune-worktrees.sh --branches --apply  # ...and delete them
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
branches=0
schedule=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    --check) check=1 ;;
    --strict) strict=1 ;;
    --branches) branches=1 ;;
    --schedule) schedule=1 ;;
    # The help is the header itself, delimited by the first code line rather than
    # by a line number a later edit can invalidate.
    -h|--help) awk 'NR > 1 && /^set -uo pipefail/{exit} NR > 1' "$0"; exit 0 ;;
    *) echo "prune-worktrees: unknown argument $arg" >&2; exit 2 ;;
  esac
done

current="$root"

scratch="$(mktemp)"
live_paths="$scratch.live"
live_wts="$scratch.held"
wt_paths="$scratch.wts"
live_lanes="$scratch.lanes"
declared="$scratch.declared"
crontab_err="$scratch.crontab"
trap 'rm -f "$scratch" "$live_paths" "$live_wts" "$wt_paths" "$live_lanes" "$declared" "$crontab_err"' EXIT

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

# --- liveness: is a live process holding this worktree? (issue #1159) --------
#
# A worktree is IN USE when a live process has it as its cwd OR holds ANY OPEN
# FILE under it. cwd ALONE was the predicate until #1159, and it could not see a
# peer driving a worktree from the SHARED shell: that peer's cwd is elsewhere and
# it holds the tree open through file descriptors, so LIVE trees were classified
# removable (10 `.claude/worktrees/agent-*` trees + 4 scratch trees measured on
# this box) while --apply now runs unattended from cron. A guard that cannot see
# the work is worse than no guard, because it authorises deletion.
#
# ONE `find` PER SOURCE, not a `readlink` per entry: this box carries ~630
# processes and ~13k open descriptors (measured 2026-09-18), and a per-descriptor
# `readlink` loop had NOT finished after a minute, where the two `find` calls
# together take ~100 ms. `-printf '%l'` prints a link's TARGET without following
# it, which is what both sources need.
#
# A NON-ZERO `find` IS EXPECTED HERE: some `/proc/<pid>/fd` directories belong to
# another user and cannot be read (rc=1). That is a blind spot the cwd list had
# too, and it is not a reason to refuse. What IS refused is being unable to search
# AT ALL — if no live path can be seen, nothing is seen as in use, so nothing may
# be removed (fail closed).
#
# MATCHING IS ANCHORED ON A PATH BOUNDARY: a live path matches when it IS the
# worktree or begins with the worktree followed by "/".
#
# Both halves of the predicate are named in ONE place, so the check can flip each
# of them and prove the flip matters (GR-12):
#   LIVE_SOURCES — which process facts count as liveness.
#   LIVE_MATCH   — "under" (the tree, or anything below it) or "exact" (the tree
#                  path itself only).
LIVE_SOURCES="cwd fd"
LIVE_MATCH="under"

live_paths_of() { # live_paths_of <source> — the live paths of that source, one per line
  case "$1" in
    cwd) find /proc/[0-9]*/cwd -maxdepth 0 -printf '%l\n' 2>/dev/null ;;
    fd) find /proc/[0-9]*/fd -mindepth 1 -maxdepth 1 -printf '%l\n' 2>/dev/null ;;
    *) return 3 ;;
  esac
}

if [ ! -d /proc ]; then
  echo "prune-worktrees: CANNOT-ASSESS — /proc is unavailable, so liveness cannot be measured; removing nothing" >&2
  exit 2
fi
: > "$live_paths"
while IFS= read -r source; do
  [ -z "$source" ] && continue
  source_paths="$(live_paths_of "$source")"
  source_rc=$?
  case "$source_rc" in
    # 1 is find's "some directories could not be read" (another user's process);
    # it is the normal outcome here and is not a reason to refuse.
    0|1) : ;;
    *)
      echo "prune-worktrees: CANNOT-ASSESS — the '$source' liveness search failed (rc=$source_rc); nothing was seen, so nothing is removed" >&2
      exit 2
      ;;
  esac
  printf '%s\n' "$source_paths" >> "$live_paths"
done < <(printf '%s\n' "$LIVE_SOURCES" | tr ' ' '\n')

if ! grep -q '^/' "$live_paths"; then
  echo "prune-worktrees: CANNOT-ASSESS — the liveness search found no usable live path; removing nothing" >&2
  exit 2
fi

# Resolve ONCE, in a single pass: asking "is any of ~13k live paths under this
# tree?" separately for every worktree would be ~10^6 comparisons in the shell.
git -C "$root" worktree list --porcelain | awk '/^worktree /{ print $2 }' > "$wt_paths"
awk -v list="$wt_paths" -v mode="$LIVE_MATCH" '
  BEGIN { while ((getline line < list) > 0) if (line != "") tree[line] = 1 }
  { for (t in tree) if ($0 == t || (mode != "exact" && index($0, t "/") == 1)) held[t] = 1 }
  END { for (t in held) print t }
' "$live_paths" > "$live_wts"

live_path_count="$(wc -l < "$live_paths" | tr -d ' ')"
wt_count="$(wc -l < "$wt_paths" | tr -d ' ')"
live_wt_count="$(wc -l < "$live_wts" | tr -d ' ')"
liveness_desc="cwd+open files ($LIVE_SOURCES), $LIVE_MATCH match"

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

# --- item 3 (issue #830): declared runtime state is not a lane's work --------
#
# The dirty test above counts GENERATED runtime state as uncommitted lane work, so
# a finished lane whose only dirt is a file a MACHINE rewrote can never be
# reclaimed. The set is DECLARED, in exactly one place — `MACHINE_MANAGED_PATHS` in
# `governance/isolation/worktree.py` (#834) — and is READ from there, never copied:
# a second copy here would be a declaration that could disagree with its owner.
#
# FAIL CLOSED. An unreadable or unparsable declaration excuses NOTHING, so a lane
# whose dirt is real is still kept. Widening what may be discarded is never the
# safe direction for a failure.
machine_managed_paths() { # machine_managed_paths — the declared runtime-state paths and prefixes, one per line
  # Exact paths are printed bare; a declared PREFIX is printed with a trailing
  # "*" marker so the bash matcher below can tell the two apart without a
  # second file or a second round of parsing.
  python3 - "$root" <<'PY'
import ast
import sys
from pathlib import Path

source = Path(sys.argv[1]) / "governance" / "isolation" / "worktree.py"
try:
    tree = ast.parse(source.read_text(encoding="utf-8"))
except (OSError, SyntaxError, ValueError):
    sys.exit(3)

names = {"MACHINE_MANAGED_PATHS": None, "MACHINE_MANAGED_PREFIXES": None}
for node in tree.body:
    targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
    for target in targets:
        if isinstance(target, ast.Name) and target.id in names:
            try:
                names[target.id] = ast.literal_eval(node.value)
            except ValueError:
                sys.exit(3)

paths = names["MACHINE_MANAGED_PATHS"]
prefixes = names["MACHINE_MANAGED_PREFIXES"] or ()
if not isinstance(paths, (list, tuple)) or not all(isinstance(item, str) for item in paths):
    sys.exit(3)
if not isinstance(prefixes, (list, tuple)) or not all(isinstance(item, str) for item in prefixes):
    sys.exit(3)
for item in paths:
    print(item)
for item in prefixes:
    print(item + "*")
PY
}

if ! machine_managed_paths > "$declared" 2>/dev/null; then
  : > "$declared"
fi

is_declared_managed() { # is_declared_managed <path-name> — exact match or declared-prefix match
  local name="$1" line
  [ -s "$declared" ] || return 1
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in
      *'*')
        case "$name" in
          "${line%\*}"*) return 0 ;;
        esac
        ;;
      *)
        [ "$name" = "$line" ] && return 0 ;;
    esac
  done < "$declared"
  return 1
}

foreign_dirt() { # foreign_dirt <worktree> — uncommitted paths that are NOT declared runtime state
  local path="$1" count=0 entry name listing
  # Fast path: a clean worktree needs no second look.
  [ -z "$(git -C "$path" status --porcelain 2>/dev/null)" ] && { printf '0'; return; }
  # -uall, so a declared file inside an otherwise-untracked directory is named
  # individually rather than collapsed to the directory that contains it.
  listing="$(git -C "$path" status --porcelain --untracked-files=all 2>/dev/null)"
  while IFS= read -r entry; do
    [ -z "$entry" ] && continue
    name="${entry:3}"
    case "$name" in
      *" -> "*) name="${name##* -> }" ;;
    esac
    if is_declared_managed "$name"; then
      continue
    fi
    count=$((count + 1))
  done <<< "$listing"
  printf '%s' "$count"
}

# --- item 2 (issue #830): lane branches have no reaper ----------------------
#
# #830 measured 302 unmatched lane branches that no tool looks at. LANDED IS
# DECIDED BY CONTENT, NEVER BY ANCESTRY: the landing path squash-merges, so a
# fully-landed branch is never an ancestor of master and `merge-base --is-ancestor`
# would call every landed lane "unmerged", keeping it for ever. The test here takes
# the merge base, lists every path the branch changed since it, and requires
# origin/master to hold EXACTLY the branch's content for that path. If master moved
# on and differs anywhere — or nothing can be compared — the branch is KEPT.
landed_by_content() { # landed_by_content <ref> — is the ref's own change already on origin/master?
  local ref="$1" base path theirs ours changed=0
  if git -C "$root" merge-base --is-ancestor "$ref" origin/master 2>/dev/null; then
    return 0
  fi
  base="$(git -C "$root" merge-base "$ref" origin/master 2>/dev/null)" || return 1
  [ -z "$base" ] && return 1
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    changed=$((changed + 1))
    theirs="$(git -C "$root" rev-parse --verify --quiet "$ref:$path" 2>/dev/null || true)"
    ours="$(git -C "$root" rev-parse --verify --quiet "origin/master:$path" 2>/dev/null || true)"
    if [ "$theirs" != "$ours" ]; then
      return 1
    fi
  done < <(git -C "$root" diff --name-only "$base" "$ref" 2>/dev/null)
  [ "$changed" -gt 0 ]
}

# Snapshot BEFORE the sweep: a worktree this run removes must not release the
# branch it was holding into the same run's reach.
checked_out="$(git -C "$root" worktree list --porcelain \
  | awk '/^branch /{ sub("^refs/heads/", "", $2); print $2 }')"

stale=0
unsafe=0
while read -r path sha ref; do
  [ -z "$path" ] && continue
  case "$path" in
    "$current") continue ;;
  esac
  dirty="$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$(foreign_dirt "$path")" != "0" ]; then
    printf '  KEEP   %s — %s uncommitted file(s)\n' "$path" "$dirty"
    unsafe=$((unsafe + 1))
    continue
  fi
  if [ "$dirty" != "0" ]; then
    printf '  NOTE   %s — %s uncommitted path(s), all declared runtime state; not the lane s work (#830)\n' "$path" "$dirty"
  fi
  if grep -qxF -- "$path" "$live_wts"; then
    printf '  KEEP   %s — in use by a live process (cwd or an open file under it, #1159)\n' "$path"
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

branch_stale=0
branch_kept=0
if [ "$branches" -eq 1 ]; then
  while IFS= read -r branch; do
    [ -z "$branch" ] && continue
    case "$branch" in
      master|main|HEAD) continue ;;
    esac
    # Bash-native, line-anchored containment (issue #1145). A quiet grep exits
    # on its FIRST match and can SIGPIPE its producer; under this script's
    # pipefail the pipeline is then reported as failed for a branch that IS
    # present -- i.e. a branch a worktree is standing on would read as free,
    # and the sweep would delete it.
    is_checked_out=0
    while IFS= read -r co_branch; do
      if [ "$co_branch" = "$branch" ]; then
        is_checked_out=1
        break
      fi
    done <<< "$checked_out"
    if [ "$is_checked_out" -eq 1 ]; then
      continue
    fi
    if ! landed_by_content "$branch"; then
      printf '  KEEP   branch %s — its own change is not provably on origin/master\n' "$branch"
      branch_kept=$((branch_kept + 1))
      continue
    fi
    branch_stale=$((branch_stale + 1))
    if [ "$apply" -eq 1 ]; then
      git -C "$root" branch -D -- "$branch" >/dev/null 2>&1 \
        && printf '  REMOVED branch %s (its change is on origin/master by content)\n' "$branch" \
        || printf '  KEEP   branch %s — deletion failed\n' "$branch"
    else
      printf '  STALE  branch %s — removable (its change is on origin/master by content)\n' "$branch"
    fi
  done < <(git -C "$root" for-each-ref --format='%(refname:short)' refs/heads)
fi

echo "prune-worktrees: $stale stale, $unsafe kept (dirty, in use, claimed, parked, or unpreserved)"
# The predicate itself, on every run. #1159 was invisible without this: a tree
# that is genuinely stale and a tree a peer holds open read identically in the
# line above, which is exactly how 14 live trees came to be called removable.
printf 'prune-worktrees: liveness: %s; %s live path(s) seen, %s of %s worktree(s) held\n' \
  "$liveness_desc" "$live_path_count" "$live_wt_count" "$wt_count"
if [ "$branches" -eq 1 ]; then
  echo "prune-worktrees: $branch_stale landed branch(es) reapable, $branch_kept kept (not provably landed)"
fi
# The tool's own standing, on every run — the line #830 needed and did not have.
# REPORTED, never acted on: a missing schedule is a finding for an operator, not
# a reason for the reaper to refuse work it can safely do.
printf 'prune-worktrees: schedule: %s — %s\n' "$schedule_state" "$schedule_detail"
if [ "$check" -eq 1 ] && [ "$stale" -gt 0 ]; then
  echo "prune-worktrees: NOT-OK — $stale stale worktree(s); run with --apply" >&2
  exit 1
fi
if [ "$check" -eq 1 ] && [ "$branches" -eq 1 ] && [ "$branch_stale" -gt 0 ]; then
  echo "prune-worktrees: NOT-OK — $branch_stale landed branch(es) still exist; run with --branches --apply" >&2
  exit 1
fi
exit 0
