#!/usr/bin/env bash
# check_tool_present.sh -- honest tri-state guard (issue #28 fixture).
#
# Usage: check_tool_present.sh <tool>
#   0 (OK)             -- tool is present on PATH
#   1 (NOT-OK)         -- tool is absent (a required tool missing is a FAIL,
#                         never a SKIP that reads as a pass)
#   2 (CANNOT-ASSESS)  -- no tool name supplied
set -u

tool="${1:-}"
if [ -z "$tool" ]; then
  echo "CANNOT-ASSESS: no tool supplied" >&2
  exit 2
fi

if ! command -v "$tool" >/dev/null 2>&1; then
  echo "NOT-OK: required tool '$tool' is not on PATH"
  exit 1
fi

echo "OK: tool '$tool' is present"
exit 0
