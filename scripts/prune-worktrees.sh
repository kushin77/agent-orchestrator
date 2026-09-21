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
#      worktree from the shared shell), AND no LIVE GATE holds it (issue #1345 —
#      see below; the gate declares itself in a store, which is the only signal
#      that survives the gap between two of a gate's short-lived checks);
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
# ONE MORE GUARANTEE ADDED FOR ISSUE #1345:
#   * A VENUE A LIVE GATE IS USING IS NEVER REMOVED, and a destroyed venue is
#     never silent. The `/proc` predicate above is a measurement of the INSTANT,
#     and a gate is not a long-lived reader: `make verify` runs a sequence of
#     short-lived checks, so a scan landing between two of them sees a venue with
#     no holder at all. That is how this tool came to delete the git admin
#     directory of a venue a running gate was USING (measured 2026-09-18, #1345):
#     five gate venues survived as directories whose `.git` file pointed at an
#     admin dir that no longer existed, and the gate that had run inside one of
#     them published 20 `rc 2 CANNOT-ASSESS` checks folded into `skipped` — a
#     FALSE verdict that still read like a small, believable failure. (Reporting
#     ONE named venue-invalid verdict instead of 20 skips is the COMPOSITE's half
#     of that failure and belongs in scripts/verify.sh; it was owned by other open
#     lanes, so it is tracked by #1351.)
#     The gate already DECLARES that it is running: `scripts/gate-lock.sh`
#     (fleet/gatelock.py) records, for every admitted gate, the HOLDER PID and the
#     HOLDER WORKTREE in a store outside every workspace. That is a declaration,
#     not an inference, so it is consulted BEFORE the /proc fallback, and a
#     venue it names is KEEP by name. Liveness of a record is NOT its bytes —
#     `release` truncates a permit's record and leaves the file — so a record is
#     live only while its `flock` is HELD or a pid it records is still alive.
#     A venue whose `.git` file already names a missing admin dir is reported
#     `BROKEN` by name, and is a `--check` finding: the destruction must be
#     visible, never 20 silent skips.
#
# ONE MORE GUARANTEE ADDED FOR ISSUE #1337:
#   * A TREE THE REAPER DID NOT CREATE IS NEVER REMOVED UNLESS OWNERSHIP IS
#     DECLARED. The two guards above are measurements of an INSTANT, and a tree
#     can be live while no instant shows it: a harness session working inside a
#     worktree holds no descriptor between two of its commands and runs no gate,
#     so neither the /proc scan (#1159) nor the permit store (#1345) can see it —
#     while `ao-fleet-reap` runs this tool with --apply UNATTENDED from cron.
#     Measured on this box (2026-09-18, #1337): of 14 trees the sweep called
#     removable, 9 were `.claude/worktrees/agent-*` and 5 were scratch roots, and
#     NOT ONE carried a `.fleet/lanes/` record — a working directory destroyed
#     under a live session, with no commit at risk and no warning either.
#     Ownership is therefore DECLARED, by one of two things that outlive the
#     instant: a `.fleet/lanes/` record naming the tree (the lane-claim guard
#     above, which runs first and is the stronger statement — that lane is
#     OPEN), or a root listed in the declared reap allowlist read below. A tree
#     with neither is KEEP, and the KEEP NAMES the reason
#     (`not-created-by-the-reaper`), because a silence here is indistinguishable
#     from an honest keep.
#     IT IS A LIST OF ROOTS, NOT A BLANKET REFUSAL. #1159's acceptance item — "a
#     genuinely stale, preserved, unclaimed tree is still reaped" — has to keep
#     holding, so the homes this reaper OWNS are declared rather than guessed
#     here (that ownership list is what PR #1326 deferred the remedy for, rather
#     than inventing one). Measured on this box 2026-09-18: all 33 worktrees of
#     this repository, and all 8 the dry run called removable, lie under exactly
#     two roots.
#     AN ABSENT, UNREADABLE, NON-REGULAR OR ROOT-LESS DECLARATION IS
#     CANNOT-ASSESS (rc 2) and NOTHING is removed. "I could not read the
#     ownership declaration" and "nothing is declared" are different answers, and
#     neither of them is "reap it": widening what may be discarded is never the
#     safe direction for a failure.
#
# Exit-code contract: 0 OK / 1 NOT-OK (stale worktrees found in --check, a
# venue-invalid venue found in --check, or — for --schedule — no installed
# crontab line invokes this tool) / 2 CANNOT-ASSESS (the question could not be
# measured).
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
# Where THIS TOOL's own source lives — distinct from `$root` below, which is
# the repository being SCANNED (its scratch-test doubles have no
# `governance/isolation` package at all). content_landed()'s implementation is
# always loaded from here, never from the target repo.
self_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$root" ]; then
  root="$self_root"
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
gate_stores_file="$scratch.gatestores"
gate_held="$scratch.gateheld"
gate_err="$scratch.gateerr"
venue_roots="$scratch.roots"
allow="$scratch.allow"
allow_err="$scratch.allowerr"
owned_wts="$scratch.owned"
trap 'rm -f "$scratch" "$live_paths" "$live_wts" "$wt_paths" "$live_lanes" "$declared" "$crontab_err" "$gate_stores_file" "$gate_held" "$gate_err" "$venue_roots" "$allow" "$allow_err" "$owned_wts"' EXIT

# --- is this tool actually scheduled? (issue #830, revised #1627) ------------
#
# ANSWERED BEFORE THE REPOSITORY CHECK, ON PURPOSE: --schedule has to be
# answerable where the schedule is declared — including an image whose `.git`
# is excluded from the build context. Everything after that check still needs
# a repository, and still fails closed without one.
#
# SINCE THE SINGLE-DEVELOPER METHOD CUTOVER (docs/EXECUTION-PLAN.md §7,
# 2026-09-21) THE HOST CRONTAB IS RETIRED: box-state checks no longer run
# there, and `crontab -l` on this box is permanently empty regardless of
# whether the job is meant to run. The schedule is now IaC — declared in
# `config/fleet-jobs.json` and rendered by `fleet/cron.py` — so this probe
# reads the manifest instead. A job entry with a `schedule` and `enabled: true`
# IS the installation under this method; there is no separate crontab to
# install it into. CANNOT-ASSESS only when the manifest itself is unreadable
# (missing, unparsable) — "I could not look" and "it is not declared" stay
# different answers.
self_name="$(basename "${BASH_SOURCE[0]}")"
manifest_path="$root/config/fleet-jobs.json"
schedule_state="CANNOT-ASSESS"
schedule_detail=""
if [ ! -r "$manifest_path" ]; then
  schedule_detail="cannot read $manifest_path"
elif ! manifest_jobs="$(python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as fh:
        manifest = json.load(fh)
except Exception as exc:
    print(f"MANIFEST-ERROR: {exc}")
    sys.exit(0)
for job in manifest.get("jobs", []):
    cmd = job.get("command", "")
    name = job.get("name", "?")
    sched = job.get("schedule", "")
    if sys.argv[2] in cmd and sched and job.get("enabled"):
        print(name + " " + sched)
' "$manifest_path" "$self_name" 2>"$crontab_err")"; then
  schedule_detail="manifest probe failed: $(head -n 1 "$crontab_err")"
elif [ -n "$manifest_jobs" ] && printf '%s\n' "$manifest_jobs" | grep -q '^MANIFEST-ERROR:'; then
  schedule_detail="$(printf '%s\n' "$manifest_jobs" | head -n 1)"
elif [ -n "$manifest_jobs" ]; then
  schedule_count="$(printf '%s\n' "$manifest_jobs" | grep -c .)"
  schedule_state="SCHEDULED"
  schedule_detail="$schedule_count declared, enabled fleet-jobs.json entr(y|ies) invoke $self_name"
else
  schedule_state="NOT-SCHEDULED"
  schedule_detail="no enabled config/fleet-jobs.json entry with a schedule invokes $self_name"
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

# --- liveness: is a LIVE GATE holding this worktree? (issue #1345) -----------
#
# PRIMARY, because it is DECLARED rather than inferred. The predicate above is a
# measurement of the instant, and a gate is not a long-lived reader: `make verify`
# runs a sequence of SHORT-LIVED checks, so a scan that lands between two of them
# sees a venue with no holder at all. Measured consequences (#1345): five gate
# venues survived as directories whose `.git` file pointed at an admin dir that no
# longer existed, and a gate that had run inside one of them reported 20
# `rc 2 CANNOT-ASSESS` checks folded into `skipped` — a FALSE verdict that still
# read like a small honest failure, because `rc 2` is deliberately not a pass.
#
# `scripts/gate-lock.sh` (fleet/gatelock.py) already records, for every admitted
# gate, the HOLDER PID and the HOLDER WORKTREE — in a store outside every
# workspace precisely because a bound stored inside one is edited per worktree and
# bounds nothing. This reads that store, so a gate announces itself instead of
# having to be caught in the act.
#
# LIVENESS IS NOT BYTES. `release` TRUNCATES a permit's record and leaves the
# file, so a record with bytes is evidence a gate WAS here, never that it is here
# now; a bytes-only rule would pin every venue that ever ran a gate, for ever. A
# record is live when the `flock` on its file is HELD — the holder holds it for
# the whole gate, so a held flock IS a running gate — or when any pid it records
# can still be signalled.
#
# BOTH VIEWS OF THE STORE ARE READ. A lane may export `AO_GATE_LOCK_ROOT` while
# the scheduler that runs this tool does not, and the two processes then disagree
# about where the store is; reading only one view would let this tool remove a
# venue a live gate holds. Every candidate root is read, in the gate's own order
# of preference, and a venue named by a live record in ANY of them is kept.
#
# FAIL CLOSED. A store that exists but cannot be read, or a non-empty record that
# is not a readable gate record, is CANNOT-ASSESS: nothing was seen, so nothing is
# removed. Widening what may be discarded is never the safe direction for a
# failure.
#
# The signal is named in ONE place so a check can switch it off and prove the
# switch matters (GR-12): GATE_SIGNAL — "permit" honours the store, anything else
# ignores it, which is exactly the pre-#1345 predicate.
GATE_SIGNAL="permit"
GATE_STORE_SUBDIR="agent-orchestrator-gates"
GATE_STORE_PERMIT_DIR="permits"
GATE_STORE_WORKTREE_DIR="worktrees"

{
  [ -n "${AO_GATE_LOCK_ROOT:-}" ] && printf '%s\n' "${AO_GATE_LOCK_ROOT:-}"
  printf '%s\n' "${XDG_RUNTIME_DIR:-/tmp}/$GATE_STORE_SUBDIR"
} | awk 'NF && !seen[$0]++' > "$gate_stores_file"

: > "$gate_held"
: > "$gate_err"
gate_held_count=0
gate_signal_desc="OFF (GATE_SIGNAL=$GATE_SIGNAL) — the gate permit/lock store was not consulted"

gate_held_reader() { # gate_held_reader <roots-file> — "<worktree>\t<what holds it>" per LIVE gate record
  python3 - "$1" "$GATE_STORE_PERMIT_DIR" "$GATE_STORE_WORKTREE_DIR" <<'PY'
import fcntl
import json
import os
import sys
from pathlib import Path

PERMIT_DIR = sys.argv[2]
WORKTREE_DIR = sys.argv[3]


def pid_alive(pid):
    """Can this pid still be signalled? A pid nobody can signal is not a gate."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def flock_held(path):
    """Is the file's flock held right now? The holder holds it for the whole gate."""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


try:
    raw_roots = Path(sys.argv[1]).read_text(encoding="utf-8")
except OSError as exc:
    sys.stderr.write(f"the store-root list cannot be read: {exc}\n")
    sys.exit(3)

roots = [line.strip() for line in raw_roots.splitlines() if line.strip()]
for root in roots:
    base = Path(root)
    if not base.is_dir():
        continue
    for subdir, kind in ((PERMIT_DIR, "permit"), (WORKTREE_DIR, "lock")):
        directory = base / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.lock")):
            try:
                raw = path.read_bytes()
            except OSError as exc:
                sys.stderr.write(f"{path} cannot be read: {exc}\n")
                sys.exit(3)
            if not raw:
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
                worktree = str(record.get("worktree") or "")
                pids = [
                    int(value)
                    for value in (record.get("pid"), record.get("owner_pid"))
                    if value
                ]
            except (AttributeError, TypeError, ValueError, UnicodeDecodeError):
                sys.stderr.write(
                    f"{path} carries {len(raw)} bytes that are not a readable gate record\n"
                )
                sys.exit(3)
            if not worktree:
                sys.stderr.write(
                    f"{path} names no worktree, so a live gate behind it cannot be honoured\n"
                )
                sys.exit(3)
            if not flock_held(path) and not any(pid_alive(pid) for pid in pids):
                continue
            detail = (
                f"{kind} {path.name} (holder pid {record.get('pid')}, "
                f"gate pid {record.get('owner_pid')}, "
                f"started {record.get('started_at', 'unknown')})"
            )
            # Both the recorded path and its realpath, so a worktree listed by git
            # under a different spelling of the same directory still matches.
            for form in {worktree, os.path.realpath(worktree)}:
                print(f"{form}\t{detail}")
PY
}

if [ "$GATE_SIGNAL" = "permit" ]; then
  gate_held_reader "$gate_stores_file" > "$gate_held" 2>"$gate_err"
  gate_rc=$?
  if [ "$gate_rc" -ne 0 ]; then
    echo "prune-worktrees: CANNOT-ASSESS — the gate permit/lock store could not be read ($(head -n 1 "$gate_err")); nothing was seen, so nothing is removed" >&2
    exit 2
  fi
  gate_held_count="$(awk -F'\t' 'NF { print $1 }' "$gate_held" | LC_ALL=C sort -u | wc -l | tr -d ' ')"
  gate_signal_desc="live gate permit/lock ($GATE_STORE_PERMIT_DIR + $GATE_STORE_WORKTREE_DIR)"
fi

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

# --- ownership: was this tree the reaper's to remove? (issue #1337) ----------
#
# The guards above are measurements of the INSTANT, and a tree can be live while
# no instant shows it: a harness session working inside a worktree holds no
# descriptor between two of its commands and runs no gate, so neither the /proc
# scan (#1159) nor the permit store (#1345) can see it — while `ao-fleet-reap`
# runs this tool with --apply UNATTENDED from cron. Measured on this box
# (2026-09-18, #1337): of 14 trees the sweep called removable, 9 were
# `.claude/worktrees/agent-*` and 5 were scratch roots, and NOT ONE carried a
# `.fleet/lanes/` record.
#
# So ownership is DECLARED, by one of two things that outlive the instant: a
# `.fleet/lanes/` record naming the tree (the lane-claim guard above, which runs
# first — an OPEN lane is the stronger statement), or a root listed in the
# declared reap allowlist read here. A tree with neither is KEEP, and the KEEP
# NAMES the reason, because a silence here is indistinguishable from an honest
# keep.
#
# IT IS A LIST OF ROOTS, NOT A BLANKET REFUSAL: #1159's acceptance item — "a
# genuinely stale, preserved, unclaimed tree is still reaped" — has to keep
# holding, so the homes this reaper OWNS are declared in ONE file rather than
# guessed here. The declaration is read from the repository being SCANNED (the
# same place MACHINE_MANAGED_PATHS is read from), never from a copy.
#
# FAIL CLOSED, LOUDLY. An absent, unreadable, non-regular or root-less
# declaration is CANNOT-ASSESS (rc 2) and NOTHING is removed: "I could not read
# the ownership declaration" and "nothing is declared" are different answers,
# and neither is "reap it".
#
# The signal is named in ONE place so a check can switch it off and prove the
# switch matters (GR-12): REAP_OWNERSHIP — "declared" reads the allowlist and
# enforces it, anything else gates the rule off ENTIRELY (the read AND the
# refusal), which is exactly the pre-#1337 predicate. A switch that only stopped
# the read would make the refusal STRONGER with the rule off, and no mutant could
# then tell the rule from its absence. The path is named once too, and the check's
# fixtures WRITE their declarations where this line says they are read, so the
# reader and its harness cannot drift apart.
REAP_OWNERSHIP="declared"
REAP_ALLOWLIST="config/reap-allowlist.txt"

reap_root_lines() { # reap_root_lines <relative-base> <declaration> — the declared roots, normalised, one per line
  python3 - "$1" "$2" <<'PY'
import os
import sys
from pathlib import Path

# A RELATIVE root is relative to the REPOSITORY — its main checkout — never to the
# worktree this tool happens to be run from. Running it from a lane worktree is
# the normal case (a lane tests with it), and a root such as `.claude/worktrees`
# resolved against that lane names a directory that does not exist, silently
# owning nothing: measured, 23 of 33 trees went unowned that way.
base = Path(sys.argv[1])
source = Path(sys.argv[2])
if not source.is_file():
    sys.stderr.write("the declared reap allowlist is not a readable file\n")
    sys.exit(3)
try:
    lines = source.read_text(encoding="utf-8").splitlines()
except OSError as exc:
    sys.stderr.write(f"the declared reap allowlist cannot be read: {exc}\n")
    sys.exit(3)

home = os.environ.get("HOME") or ""
roots = []
for line in lines:
    entry = line.strip()
    if not entry or entry.startswith("#"):
        continue
    if entry.startswith("~/"):
        if not home:
            continue
        entry = home + entry[1:]
    elif not entry.startswith("/"):
        entry = f"{base}/{entry}"
    entry = entry.rstrip("/") or "/"
    if entry == "/":
        # Deliberate, not accidental: the filesystem root is not a worktree home,
        # and the awk boundary pass below would silently own nothing under it. A
        # declaration whose ONLY line is `/` is therefore root-less — rc 2.
        continue
    if entry not in roots:
        roots.append(entry)

if not roots:
    sys.stderr.write("no usable root line in the declared reap allowlist\n")
    sys.exit(3)
for entry in roots:
    print(entry)
PY
}

: > "$allow"
: > "$owned_wts"
owned_count=0
allow_total=0
allow_existing=0
allow_missing=""
reap_ownership_desc="OFF (REAP_OWNERSHIP=$REAP_OWNERSHIP) — this rule is gated off: the declared reap allowlist is not consulted and every worktree is treated as owned, so nothing is refused for want of a declaration"
if [ "$REAP_OWNERSHIP" = "declared" ]; then
  # A relative root needs a base: `git worktree list` names the MAIN checkout
  # first, so the first line of the list this run already built is it, whichever
  # worktree this tool was started from.
  allow_base="$(awk 'NR == 1 { print; exit }' "$wt_paths")"
  [ -n "$allow_base" ] || allow_base="$root"
  if ! reap_root_lines "$allow_base" "$root/$REAP_ALLOWLIST" > "$allow" 2>"$allow_err"; then
    echo "prune-worktrees: CANNOT-ASSESS — the reap ownership declaration $REAP_ALLOWLIST cannot be read ($(head -n 1 "$allow_err")); nothing is declared, so nothing is removed" >&2
    exit 2
  fi
  allow_total="$(wc -l < "$allow" | tr -d ' ')"
  while IFS= read -r declared_root; do
    if [ -n "$declared_root" ] && [ -d "$declared_root" ]; then
      allow_existing=$((allow_existing + 1))
    else
      allow_missing="$allow_missing $declared_root"
    fi
  done < "$allow"
  # One pass over the worktree list, not one comparison per tree, and ANCHORED ON
  # A PATH BOUNDARY: a bare prefix test would let ~/ao-worktrees-old be owned by
  # ~/ao-worktrees, i.e. one root would authorise a tree that is not under it.
  awk '
    FNR == NR { if (NF) { root[$0] = 1 }; next }
    { for (r in root) if ($0 == r || index($0, r "/") == 1) { print $0; break } }
  ' "$allow" "$wt_paths" > "$owned_wts"
  owned_count="$(wc -l < "$owned_wts" | tr -d ' ')"
  reap_ownership_desc="declared ($REAP_ALLOWLIST); $allow_total declared root(s), $allow_existing existing; $owned_count of $wt_count worktree(s) under one"
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

# --- content equivalence (issue #1265) ---------------------------------------
#
# Measured 2026-09-18 (after #1285 landed): 49 of 88 remaining worktrees were
# kept ONLY because "HEAD is not preserved on origin" — their remote branches
# were deleted by a by-hand sweep after a squash-merge, so `preserved()` above
# can NEVER pass for them even though every one is content-landed on master.
# This delegates to the ONE implementation of that test
# (`governance/isolation/worktree.py::content_landed`), which `--branches`
# below also calls — so the worktree keep-rule and the branch keep-rule share
# one implementation instead of two copies that can drift.
landed_by_content() { # landed_by_content <ref> — is the ref's own change already on origin/master, by content?
  PYTHONPATH="$self_root${PYTHONPATH:+:$PYTHONPATH}" python3 -m governance.isolation.worktree \
    content-landed "$1" --root "$root" >/dev/null 2>&1
}

# Runtime state (gitignored): once a remote branch is gone, "preserved on
# origin" can never be re-derived, so this is the only record a given SHA was
# ever content-landed and reclaimed. Written BEFORE removal.
record_reaped() { # record_reaped <branch> <head_sha> <worktree> <reason>
  PYTHONPATH="$self_root${PYTHONPATH:+:$PYTHONPATH}" python3 - "$root" "$1" "$2" "$3" "$4" <<'PY'
import sys
from governance.isolation.worktree import record_reaped as _record

_record(sys.argv[1], branch=sys.argv[2], head_sha=sys.argv[3], worktree=sys.argv[4], reason=sys.argv[5])
PY
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
# DECIDED BY CONTENT, NEVER BY ANCESTRY (see `landed_by_content` above, which
# this reuses — one implementation, `governance/isolation/worktree.py`).

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
  # A LIVE GATE first (issue #1345). This is the declared signal, and it is
  # checked before the /proc fallback because the fallback is a race the gate's
  # own structure loses between two of its short-lived checks.
  gate_row="$(awk -F'\t' -v want="$path" 'NF && $1 == want { print $2; exit }' "$gate_held")"
  if [ -n "$gate_row" ]; then
    printf '  KEEP   %s — a LIVE GATE holds this venue (%s, #1345)\n' "$path" "$gate_row"
    unsafe=$((unsafe + 1))
    continue
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
  # Ownership (#1337): the LAST keep and the FIRST removal. A tree no lane record
  # claims and no declared root covers is not this tool's to remove, and the
  # refusal is NAMED — a silent keep would be indistinguishable from an honest
  # one, and a live harness session inside that tree would be destroyed unseen.
  # The whole rule is behind its declared signal (see REAP_OWNERSHIP above), so
  # gating it off reproduces the predicate this issue replaces.
  if [ "$REAP_OWNERSHIP" = "declared" ] && ! grep -qxF -- "$path" "$owned_wts"; then
    printf '  KEEP   %s — not-created-by-the-reaper: no lane record and not under a declared reap root (#1337)\n' "$path"
    unsafe=$((unsafe + 1))
    continue
  fi
  content_equiv=0
  if ! preserved "$sha"; then
    if landed_by_content "$sha"; then
      # #1265: HEAD is not preserved by NAME on origin (its remote branch was
      # deleted after a squash-merge), but its own change is fully present on
      # origin/master BY CONTENT — reapable, and recorded before removal since
      # this is the last point the fact is derivable at all. This applies to a
      # detached HEAD exactly as it does to a named branch: `git diff`/
      # `rev-parse <ref>:<path>` both work on a bare SHA, and a detached
      # scratch tree is a large share of the pile (#1265 measured them too).
      content_equiv=1
    else
      printf '  KEEP   %s — HEAD %s is not preserved on origin (unmerged lane work)\n' "$path" "$sha"
      unsafe=$((unsafe + 1))
      continue
    fi
  fi
  if [ "$strict" -eq 1 ] && [ "$content_equiv" -eq 0 ] && ! landed_in_master "$sha" && [ "$ref" != "detached" ]; then
    printf '  PARKED %s — %s holds work preserved only on a remote branch; kept (--strict)\n' "$path" "$ref"
    unsafe=$((unsafe + 1))
    continue
  fi
  stale=$((stale + 1))
  if [ "$content_equiv" -eq 1 ]; then
    reason="worktree-content-landed"
    label="content-landed on origin/master; remote branch $ref is gone"
  else
    reason="worktree-preserved"
    label="preserved on origin"
  fi
  if [ "$apply" -eq 1 ]; then
    [ "$content_equiv" -eq 1 ] && record_reaped "$ref" "$sha" "$path" "$reason"
    git -C "$root" worktree remove --force "$path" >/dev/null 2>&1 \
      && printf '  REMOVED %s (HEAD %s %s)\n' "$path" "$sha" "$label" \
      || printf '  KEEP   %s — removal failed\n' "$path"
  else
    printf '  STALE  %s — removable (HEAD %s %s)\n' "$path" "$sha" "$label"
  fi
done < <(git -C "$root" worktree list --porcelain | awk '
  /^worktree /{ if (p != "") print p, h, (b == "" ? "detached" : b); p=$2; h=""; b=""; next }
  /^HEAD /{ h=$2 }
  /^branch /{ b=$2; sub("^refs/heads/", "", b) }
  END{ if (p != "") print p, h, (b == "" ? "detached" : b) }
' | grep -v "^$current ")

git -C "$root" worktree prune 2>/dev/null || true

# --- venue-invalid: a destroyed venue is NAMED, never silent (#1345) ---------
#
# The other half of #1345 is detectability. A venue whose `.git` file names an
# admin dir that is gone is no longer a git checkout, so EVERY git-dependent check
# inside it returns `rc 2 CANNOT-ASSESS` — and the composite folds `rc 2` into
# `skipped`, so a gate in such a venue publishes "20 of 203 checks failed, 20
# skipped" instead of "this venue is invalid". Twenty independent skips are a
# shape no reader can tell from twenty honest ones.
#
# This tool is where a venue's life cycle lives, so this is where the condition is
# named. (The composite's own half — refusing to report a verdict at all when the
# venue is not a repository — is tracked by #1351.) It is DERIVED, not assumed: the
# `.git` file must actually name a missing admin dir AND `git rev-parse` must
# actually fail inside the directory, so a venue whose linkage is merely unusual is
# not reported.
#
# The roots scanned are the parents of the worktrees git still knows about (plus
# this one), because a dangling venue is BY DEFINITION one git no longer lists —
# enumerating from `git worktree list` alone can never see it, which is why the
# condition was invisible until a gate reported it as 20 skips.
#
# Reported, never removed: a dangling directory is not a worktree, and removing
# it is not this tool's job. It is a FINDING, so `--check` fails on it.
broken=0
{
  git -C "$root" worktree list --porcelain | awk '/^worktree /{ print $2 }'
  printf '%s\n' "$current"
} | while IFS= read -r tree; do
  [ -n "$tree" ] || continue
  dirname -- "$tree"
done | LC_ALL=C sort -u > "$venue_roots"

while IFS= read -r venue_root; do
  [ -d "$venue_root" ] || continue
  for candidate in "$venue_root"/*; do
    [ -d "$candidate" ] || continue
    # Only a LINKED worktree carries a `.git` FILE; a repository has a directory.
    [ -f "$candidate/.git" ] || continue
    admin_dir="$(sed -n 's/^gitdir: //p' "$candidate/.git" 2>/dev/null | awk 'NR == 1')"
    [ -n "$admin_dir" ] || continue
    [ -e "$admin_dir" ] && continue
    if git -C "$candidate" rev-parse HEAD >/dev/null 2>&1; then
      continue
    fi
    printf '  BROKEN %s — venue-invalid: its .git names a missing admin dir %s, so no git command can run inside it; NOT a removal candidate (#1345)\n' \
      "$candidate" "$admin_dir"
    broken=$((broken + 1))
  done
done < "$venue_roots"

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
      branch_sha="$(git -C "$root" rev-parse --verify --quiet "refs/heads/$branch" 2>/dev/null || true)"
      record_reaped "$branch" "$branch_sha" "" "branch-content-landed"
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
# The declared signal, and its size, on every run — so "the reaper was looking at
# the gate store" is a measurement rather than a claim (#1345). A venue named here
# can never appear as STALE above; that pairing is what a reader can check.
printf 'prune-worktrees: gate venues: %s; %s worktree(s) named by a live gate\n' \
  "$gate_signal_desc" "$gate_held_count"
# The declared ownership, and its size, on every run — so "the reaper was looking
# at the ownership declaration" is a measurement rather than a claim, and a tree
# kept for want of one is visible instead of silent (#1337). A declared root that
# does NOT exist yet is named too: it owns nothing, and a root that owns nothing
# while every tree is kept is exactly the state a reader has to be able to see.
printf 'prune-worktrees: reap ownership: %s\n' "$reap_ownership_desc"
if [ -n "$allow_missing" ]; then
  printf 'prune-worktrees: reap ownership: declared root(s) that do not exist yet: %s\n' "${allow_missing# }"
fi
if [ "$broken" -gt 0 ]; then
  printf 'prune-worktrees: venue-invalid: %s dangling venue(s) named above; a gate run inside one reports false CANNOT-ASSESS verdicts folded into skipped (#1345)\n' "$broken"
fi
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
if [ "$check" -eq 1 ] && [ "$broken" -gt 0 ]; then
  echo "prune-worktrees: NOT-OK — $broken venue-invalid venue(s): the git admin dir a gate venue points at is gone, so every check run inside it is a false CANNOT-ASSESS" >&2
  exit 1
fi
if [ "$check" -eq 1 ] && [ "$branches" -eq 1 ] && [ "$branch_stale" -gt 0 ]; then
  echo "prune-worktrees: NOT-OK — $branch_stale landed branch(es) still exist; run with --branches --apply" >&2
  exit 1
fi
exit 0
