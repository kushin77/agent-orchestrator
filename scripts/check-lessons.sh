#!/usr/bin/env bash
# check-lessons.sh — RCA + lessons enforcement (issue #141).
#
# Every failure, breach, defect, operational incident and preventable drift is
# analysed and recorded, so the organization can show it learned something. The
# gate is the mechanism that makes the record binding (GR-12: a doc-only rule is
# advisory), so this check fails on a malformed ledger, an incident with no RCA,
# an RCA with no traceable origin or corrective action, an action that is not in
# the ledger or has no owner, a closed lesson with no commit evidence, or an RCA
# artifact that is not committed.
#
# Board scope is a RECORD label (issue #766), never an area one: the gate fails
# an issue that carries `incident` when no incident record in the ledger names
# it, and refuses both an `area:` label in that position and any `board.`
# `exemptions` list. See governance/lessons/policy.yaml.
#
# PART 2 (issue #766) is the provoked negative control:
# governance/lessons/negative_control.py plants one fact per probe and requires
# the named verdict — an area label producing no incident, a record label with
# no ledger record still refused, both hand-edit routes refused by name, and a
# mutated copy of the checker in which the refusal must be observed to
# disappear. A probe that cannot fail proves nothing.
#
# Deviations (an issue that records an incident still open, an action still in
# flight, a review past its cadence) are reported with the issue that carries
# them and do not fail the gate; `--strict` escalates them to errors.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no ledger, no board
# snapshot, not a git work tree). NOT-OK outranks CANNOT-ASSESS: a definite
# defect is never softened into "could not assess".
#
# Usage: bash scripts/check-lessons.sh [--strict]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lessons: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# Keep the strongest signal: NOT-OK (1) outranks CANNOT-ASSESS (2) outranks OK.
status=0
bump() {
  case "$1" in
    1) status=1 ;;
    2) if [ "$status" -ne 1 ]; then status=2; fi ;;
  esac
}

# --- part 1: the enforcement rules (issue #141) ------------------------------
python3 governance/lessons/cli.py check "$@"
bump "$?"

# --- part 2: the provoked negative control (issue #766) ----------------------
python3 governance/lessons/negative_control.py
bump "$?"

exit "$status"
