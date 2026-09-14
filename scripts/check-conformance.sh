#!/usr/bin/env bash
# check-conformance.sh — CMR class/pattern/template conformance (issue #140).
#
# Every item of in-scope work must be classified against the CMR ladder, and the
# class it declares must hold: a milestoned issue that declares no class cannot be
# held to any standard, and one that declares more than it meets is a
# declared-vs-actual mismatch. Both are errors and fail the gate.
#
# Reported but not fatal: the per-class expectations that the existing board only
# partially satisfies (pillar, gdc) are surfaced as deviations with remediation, and
# the un-milestoned backlog is counted rather than failed. `--strict` escalates the
# deviations when a milestone has caught up.
#
# The gate then EXERCISES the filing path itself (issue #320): detecting an
# unclassified issue after it was filed leaves the board wrong until someone
# notices, so `filing-check` feeds the filing path an underivable filing and
# requires it to be REFUSED, requires the derived labels to reach `gh issue create`,
# and requires the fleet's filing path to delegate to the seam. Either half failing
# fails the gate.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no policy, no snapshot).
#
# Usage: bash scripts/check-conformance.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-conformance: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

# 1. The board: is in-scope work classified, and does the declared class hold?
python3 governance/conformance/cli.py check
rc=$?
case "$rc" in
  0) ;;
  2) exit 2 ;;
  *) fail=1 ;;
esac

# 2. The filing path: can an unclassified issue still be filed? (issue #320)
python3 governance/conformance/cli.py filing-check
rc=$?
case "$rc" in
  0) ;;
  2) exit 2 ;;
  *) fail=1 ;;
esac

exit "$fail"
