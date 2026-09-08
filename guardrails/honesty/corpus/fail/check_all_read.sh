#!/usr/bin/env bash
# corpus/fail -- real incident artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/guard/check-formality.sh, documented spelling
# #1 -- `[ -e "$f" ] || continue` skipped unresolvable entries without
# counting, so a tool that visited ZERO entries reported "already matches"
# and exited 0.  Shape: uncounted_skip / never_fails_script.

for entry in "$@"; do
  [ -e "$entry" ] || continue
  grep -q "$NEEDLE" "$entry" && echo "already matches: $entry"
done

echo "report: scan complete"
exit 0
