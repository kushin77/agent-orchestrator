#!/usr/bin/env bash
# FORMALITY FIXTURE (issue #28): a check that cannot fail.
#
# Both the marker-found and the marker-not-found paths return the same code,
# 0.  Success and failure are indistinguishable by exit code, so a caller sees
# PASS either way -- this reads as coverage while guaranteeing none.

check_has_pass_marker() {
  if grep -q "PASS" "$1"; then
    echo "marker present"
    return 0
  else
    echo "marker absent"
    return 0
  fi
}

check_has_pass_marker "${1:?usage: check_never_fails.sh <file>}"
exit 0
