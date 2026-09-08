#!/usr/bin/env bash
# corpus/fail -- real incident artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/guard/check-formality.sh, documented spelling
# #3 -- three blocking gates wrapped so that when every result artifact was
# absent the guard echoed "optional" and still exited 0.  No input could make
# it fail.  Shape: never_fails_script with an absence-gated optional else.

for gate in gate-a gate-b gate-c; do
  if [ -f "$gate/result.txt" ]; then
    grep -q "PASS" "$gate/result.txt" && echo "$gate ok"
  else
    echo "optional: no result for $gate"
  fi
done

exit 0
