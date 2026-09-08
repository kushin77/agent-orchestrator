#!/usr/bin/env bash
# corpus/pass -- real honest artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/qa/verify-negative-controls.sh methodology --
# verification guards that ship a should-fail path.  This guard has a real
# failing exit and a CANNOT-ASSESS path, so the analyzer must leave it alone.

file="${1:?usage: check_clean.sh <file>}"

if [ ! -r "$file" ]; then
  echo "CANNOT-ASSESS: cannot read input" >&2
  exit 2
fi

if grep -qE "forbidden-alpha|forbidden-beta" "$file"; then
  echo "violation present"
  exit 1
fi

echo "clean"
exit 0
