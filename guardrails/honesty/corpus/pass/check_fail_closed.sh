#!/usr/bin/env bash
# corpus/pass -- real honest artifact (issue #28, acceptance criterion 5).
# Source: leaderboard tests/blockproof + verify-negative-controls.sh
# fail-closed doctrine -- an unresolvable input is CANNOT-ASSESS (exit 2),
# never a silent pass, and a violation is a hard FAIL (exit 1).

input="${1:?usage: check_fail_closed.sh <file>}"

if [ ! -r "$input" ]; then
  echo "CANNOT-ASSESS: cannot read input" >&2
  exit 2
fi

if grep -q "violation-marker-zeta" "$input"; then
  echo "NOT-OK: violation found"
  exit 1
fi

echo "OK"
exit 0
