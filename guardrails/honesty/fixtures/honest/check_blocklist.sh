#!/usr/bin/env bash
# check_blocklist.sh -- honest tri-state guard (issue #28 fixture).
#
# Usage: check_blocklist.sh <file>
#   0 (OK)             -- file is present and contains no forbidden token
#   1 (NOT-OK)         -- file contains a forbidden token
#   2 (CANNOT-ASSESS)  -- file is missing or unreadable
#
# This guard is intentionally honest: it has a real failing path, it
# distinguishes "cannot assess" from "clean", and its negative control
# (feeding it a file with a planted forbidden token) must fail it.
set -u

file="${1:?usage: check_blocklist.sh <file>}"

if [ ! -r "$file" ]; then
  echo "CANNOT-ASSESS: cannot read $file" >&2
  exit 2
fi

if grep -qE "forbidden-alpha|forbidden-beta" "$file"; then
  echo "NOT-OK: forbidden token present in $file"
  exit 1
fi

echo "OK: $file is clean"
exit 0
