#!/usr/bin/env bash
# check-reconcile-orphans.sh — every artifact kind has a lane or is an orphan by
# name, reclaimed only with evidence, red above the declared budget (#1301 step 3).
#
# `governance/reconcile/cli.py sweep --orphans` walks the five artifact kinds —
# orphan-worktree / orphan-branch / orphan-pr / orphan-issue-lane /
# orphan-directive — from files, git and the board, and holds the count per
# kind to governance/reconcile/orphan-budget.yaml (measured counts + expiry,
# the ratchet shape of #1335). This gate proves:
#
#   1. the budget is DECLARED and readable, and names every kind;
#   2. every finding fires on an injected port (the suite), including the two
#      load-bearing negatives: an unlanded worktree/branch survives `--apply`,
#      and an unmeasured source is CANNOT-ASSESS, never zero;
#   3. on a REAL scratch repository: a subagent-shaped worktree under
#      `.claude/worktrees/agent-*` with no lane record is named orphan-worktree
#      and NOT removed; the same tree, once its content is on the default
#      branch, is reclaimed under --apply with its tip recorded; a branch
#      with unpushed work is named with its SHA and never deleted;
#  3b. on a REAL scratch repository, IN USE is refused by name (#1440): the tree
#      a live process is sitting in, the tree a venue record names, and the tree
#      git holds its own lock on are each refused under --apply with the
#      offending PROCESS, RECORD or LOCK REASON in the finding — while a tree
#      nothing is using, with exactly the same content-landed proof, is still
#      reclaimed; an unreadable liveness signal is CANNOT-ASSESS, never "not in
#      use"; and the whole section FALSIFIES itself by re-running the same
#      controls against a mutant of orphans.py with the liveness guard removed,
#      which must red on the in-use arms and stay green on the dead one.
#   4. the REAL tree is walked (from the MAIN checkout, wherever this runs) and
#      held to the budget — the walk's own tri-state is honoured, so a box where
#      `gh` cannot be run is CANNOT-ASSESS with the precondition printed.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# Usage: bash scripts/check-reconcile-orphans.sh [--skip-real-tree]
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

skip_real=0
for arg in "$@"; do
  case "$arg" in
    --skip-real-tree) skip_real=1 ;;
    *) echo "check-reconcile-orphans: unknown argument $arg" >&2; exit 2 ;;
  esac
done

for tool in python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-reconcile-orphans: CANNOT-ASSESS — $tool not found" >&2
    exit 2
  fi
done
# shellcheck source=scripts/lib/unset-git-env.sh
source "$root/scripts/lib/unset-git-env.sh"
export GIT_CONFIG_GLOBAL=/dev/null

budget="governance/reconcile/orphan-budget.yaml"
work="${TMPDIR:-/tmp}/reconcile-orphans.$$.$(date +%s)"
mkdir -p "$work" || { echo "check-reconcile-orphans: CANNOT-ASSESS — cannot create a scratch directory" >&2; exit 2; }
trap 'rm -rf "$work"' EXIT

fail=0
