#!/usr/bin/env bash
# check-board-gate.sh — governance board enforcement gate (issue #143).
#
# The board does not add a new standard; it re-runs the required CMR gates
# (#139 knowledge-index, #140 conformance, #141 lessons, #142 remediation)
# for real and refuses to report ok unless every one of them does. A
# board-approved, timeboxed exception (governance/board/exceptions.yaml) can
# downgrade one named check's failure to a visible, non-fatal "excepted"
# state; an expired exception stops applying automatically and the failure
# counts in full. Three or more failures of the same check escalate.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-board-gate.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-board-gate: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

python3 governance/board/cli.py check
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
