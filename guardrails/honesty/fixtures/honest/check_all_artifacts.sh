#!/usr/bin/env bash
# check_all_artifacts.sh -- honest tri-state guard (issue #28 fixture).
#
# Usage: check_all_artifacts.sh <required-file>...
#   0 (OK)             -- every required artifact is present
#   1 (NOT-OK)         -- at least one required artifact is missing
#   2 (CANNOT-ASSESS)  -- no artifacts were supplied
#
# Unlike a formality, this guard COUNTS what it examines and exits nonzero
# when an artifact is missing -- the loop never swallows an absence.
set -u

if [ "$#" -eq 0 ]; then
  echo "CANNOT-ASSESS: no required artifacts supplied" >&2
  exit 2
fi

missing=0
for artifact in "$@"; do
  if [ ! -e "$artifact" ]; then
    echo "missing artifact: $artifact"
    missing=$((missing + 1))
  fi
done

if [ "$missing" -gt 0 ]; then
  echo "NOT-OK: $missing of $# required artifacts missing"
  exit 1
fi

echo "OK: all $# required artifacts present"
exit 0
