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
# Finally it provokes the defect #517 removed at the boundary an operator actually
# uses — the CLI: a DECLARED companion the policy recognises (pillar/phase) must
# appear in the labels the filing would pass, and a declared label the policy does
# not recognise must be REFUSED by name with a non-zero exit. Before the fix both
# were accepted silently: pillar/phase were dropped and exit was 0.
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

# 3. Negative control (#517) — a declared companion is not silently dropped.
declared="$(python3 governance/conformance/cli.py file --dry-run --title T --body b \
  --class enterprise --declare type=governance --declare priority=P1 \
  --declare area=fleet --declare pillar=autonomous-ops \
  --declare phase=8-autonomous-ops 2>&1)"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "check-conformance: FAIL — the declared-companion filing was refused (rc=$rc)" >&2
  printf '%s\n' "$declared" >&2
  fail=1
elif ! printf '%s' "$declared" | grep -q 'pillar:autonomous-ops'; then
  echo "check-conformance: FAIL — the declared \`pillar:\` was DROPPED from the labels" >&2
  printf '%s\n' "$declared" >&2
  fail=1
elif ! printf '%s' "$declared" | grep -q 'phase:8-autonomous-ops'; then
  echo "check-conformance: FAIL — the declared \`phase:\` was DROPPED from the labels" >&2
  printf '%s\n' "$declared" >&2
  fail=1
fi

# 4. Negative control (#517) — an unrecognised declaration is refused BY NAME, and
#    a check that cannot fail is a formality, so the exit code is required to be
#    non-zero rather than merely "something was printed".
refused="$(python3 governance/conformance/cli.py file --dry-run --title T --body b \
  --class enterprise --declare priorty=P1 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "check-conformance: FAIL — an unrecognised \`--declare priorty=\` was accepted (rc=0)" >&2
  printf '%s\n' "$refused" >&2
  fail=1
elif ! printf '%s' "$refused" | grep -q 'priorty'; then
  echo "check-conformance: FAIL — the refusal does not name the unrecognised field" >&2
  printf '%s\n' "$refused" >&2
  fail=1
fi

exit "$fail"
