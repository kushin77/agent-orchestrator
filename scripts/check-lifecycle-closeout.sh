#!/usr/bin/env bash
# check-lifecycle-closeout.sh — the lane's ONE terminal verb is evidenced, ordered,
# and blocked by name (issue #1301 step 2).
#
# `governance/lifecycle/cli.py close --lane <id>` retires every artifact a lane
# bound at creation — PR merged + trailer clean, issue closed, branch reaped
# (tips recorded to .fleet/reaped-branches.jsonl FIRST, content-landed only),
# worktree removed (machine-managed dirt only), lane archived with its evidence
# bundle — and any step it cannot evidence stays open as `closeout-blocked:<step>`
# with everything after it withheld. This gate proves that against:
#
#   1. an injected port (every refusal, the order, the dry run writes nothing);
#   2. a REAL scratch repository with a real bare origin: a squash-landed lane is
#      reaped for real (local + remote branch gone, worktree gone, SHA recorded,
#      archive written) and an UNLANDED lane's branch survives `--apply` by name;
#   3. the CLI's own tri-state: an unknown lane and a missing selector are
#      CANNOT-ASSESS, never a verdict.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# Usage: bash scripts/check-lifecycle-closeout.sh
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

for tool in python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-lifecycle-closeout: CANNOT-ASSESS — $tool not found" >&2
    exit 2
  fi
done
# shellcheck source=scripts/lib/unset-git-env.sh
source "$root/scripts/lib/unset-git-env.sh"
export GIT_CONFIG_GLOBAL=/dev/null

work="${TMPDIR:-/tmp}/lifecycle-closeout.$$.$(date +%s)"
mkdir -p "$work" || { echo "check-lifecycle-closeout: CANNOT-ASSESS — cannot create a scratch directory" >&2; exit 2; }
trap 'rm -rf "$work"' EXIT

fail=0
