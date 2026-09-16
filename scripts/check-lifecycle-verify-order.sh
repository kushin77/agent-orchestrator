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
#
# Cases 2, 3, 5 and the two mutants are negative controls: without them the gate would
# pass for a `record_verification` that simply returned success, which is the failure
# mode GR-12 names.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-lifecycle-verify-order.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
