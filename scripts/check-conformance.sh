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

python3 governance/conformance/cli.py check
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
