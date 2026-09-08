#!/usr/bin/env bash
# FORMALITY FIXTURE (issue #28): an uncounted skip.
#
# Real incident shape: entries that cannot be resolved are skipped with
# `continue`, and nothing in this file tallies what was actually visited, so a
# run that visits ZERO entries still reports success.

for entry in "${ENTRIES[@]}"; do
  [ -e "$entry" ] || continue
  grep -q "$TOKEN" "$entry" && echo "match: $entry"
done

echo "scan complete"
exit 0
