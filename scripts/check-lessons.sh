#!/usr/bin/env bash
# check-lessons.sh — RCA + lessons enforcement (issue #141).
#
# An incident that nobody analysed and a corrective action that nobody recorded
# are the same failure mode: the organization cannot show it learned anything.
# The gate is the mechanism that makes the record binding (GR-12: a doc-only
# rule is advisory), so this check fails on a malformed ledger, an incident
# with no RCA, an RCA with no traceable origin or corrective action, an action
# that is not in the ledger or has no owner, a closed lesson with no commit
# evidence, or an RCA artifact that is not committed.
#
# Deviations (an incident-labelled issue still open, an action still in flight,
# a review past its cadence) are reported with the issue that carries them and
# do not fail the gate; `--strict` escalates them to errors.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no ledger, no board
# snapshot, not a git work tree).
#
# Usage: bash scripts/check-lessons.sh [--strict]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lessons: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

python3 governance/lessons/cli.py check "$@"
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
