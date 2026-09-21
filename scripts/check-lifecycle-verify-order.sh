#!/usr/bin/env bash
# check-lifecycle-verify-order.sh — close-out's verification record survives the lane (#786).
#
# The defect this gate exists for was measured three times on the lanes of epic #616
# (#622, #623, #626) and once more on #793 (#854):
#
#     record-verification measures the lane worktree
#     reclaim-lane removes the lane worktree
#
# Nothing enforced the order between them and it is irreversible. A transient PARK, or a
# genuine failure, left the attestation unrecorded; step 8 then removed the tree; and the
# invariant could never be satisfied again — `close` reported `REMAINS
# VERIFY_EVIDENCE_MISSING` for work whose gate *was* green, with a remediation ("run make
# verify on the branch head") that no longer had a branch to run on. A control that
# cannot succeed is a formality, and this one inverted.
#
# The gate is built like its sibling `check-github-lifecycle.sh`: it drives the **real**
# modules against a **real repository** whose lane is a **real** `git worktree`, removed
# by the **real** reclaim command, and it ships no live GitHub state. The only fiction is
# `make` on `PATH` — the repo's established seam, because running the composite gate
# twice per case is not a thing a gate can do — plus the board read, which a gate may not
# make at all.
#
# What it provokes:
#
#   1. a lane reclaimed before close-out, gate green  -> the record is PRODUCED
#   2. ... the same, gate red                         -> still a named missing verification
#   3. ... the same, commit not in the repository     -> refused BY NAME, naming the order
#   4. a live lane, gate parked                       -> the reclaim is REFUSED, lane kept,
#                                                        and the retry then finishes the job
#   5. a live lane, unrelated step failed             -> the lane IS reclaimed (the guard
#                                                        is the verification, not "any
#                                                        failure keeps every lane")
#   6. MUTANT — the port's re-measurement disabled    -> case 1 goes red (load-bearing)
#   7. MUTANT — the driver's order guard disabled     -> case 4 goes red (load-bearing)
#   8. SQUASH (#1098) — a real squash merge, so the verified head is NOT an ancestor of
#      anything on the default branch:
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
#        a. a lane cut from the default branch after it contains the landing and the
#           landing carries the verified tree         -> the record is PRODUCED, naming the
#                                                         verified commit, the landing and
#                                                         the tree the gate ran in
#        b. a lane of the default branch from BEFORE the landing (it carries none of
#           this item's work)                 -> REFUSED BY NAME
#   9. DRIFTED BRANCH (#1149) — the pull request's branch received commits AFTER the squash,
#      so GitHub's live head is a tree that never landed:
#        a. the item is closed out on the tree that LANDED -> the record is PRODUCED, its
#                                                              subject the landing, with the
#                                                              drifted head disclosed
#        b. the port is handed that drifted subject direct -> REFUSED IN ITS OWN WORDS: it
#                                                              DOES contain the landing, and
#                                                              the landing carries a
#                                                              different tree
#  10. MUTANT — the containment arm removed           -> 8a goes red (load-bearing)
#  11. MUTANT — the tree check removed                -> 9b goes red (load-bearing)
#  12. MUTANT — the subject resolution removed        -> 9a goes red (load-bearing)
#
# Cases 2, 3, 5, 8b, 9b and the five mutants are negative controls: without them the gate
# would pass for a `record_verification` that simply returned success, which is the failure
# mode GR-12 names.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-lifecycle-verify-order.sh
set -uo pipefail

root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lifecycle-verify-order: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

work="/tmp/lifecycle-verify-order.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-lifecycle-verify-order: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

python3 "$root/scripts/lifecycle_verify_order.py" "$root" "$work"
exit $?
