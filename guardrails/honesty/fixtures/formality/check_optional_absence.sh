#!/usr/bin/env bash
# FORMALITY FIXTURE (issue #28): an absence-gated check.
#
# Real incident shape: the blocking gate only runs when a result file exists;
# when every artifact is absent the else branch reports "optional" and the
# guard still exits 0.  No input can make it fail.

if [ -f "$RESULT_FILE" ]; then
  grep -q "PASS" "$RESULT_FILE" && echo "result ok"
else
  echo "optional: no result file to check"
fi

exit 0
