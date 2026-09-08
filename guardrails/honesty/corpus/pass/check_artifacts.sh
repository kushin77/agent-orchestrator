#!/usr/bin/env bash
# corpus/pass -- real honest artifact (issue #28, acceptance criterion 5).
# Source: leaderboard guard code with real counters (the check-formality.sh
# counter heuristic) -- a loop that counts what it examined and fails on
# absence instead of skipping it.  Analyzer must leave it alone.

missing=0
for artifact in "$@"; do
  if [ ! -e "$artifact" ]; then
    missing=$((missing + 1))
  fi
done

if [ "$missing" -gt 0 ]; then
  echo "NOT-OK: missing artifacts"
  exit 1
fi

echo "OK"
exit 0
