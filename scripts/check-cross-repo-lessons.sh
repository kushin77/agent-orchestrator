#!/usr/bin/env bash
# check-cross-repo-lessons.sh — the cross-repo lessons-sync CONTRACT gate (#424).
#
# There is a lessons loop on each side of the repo boundary: kushin77/deepseek
# builds one (deepseek#84, #79) and this repository builds one (#402/#403, on
# top of governance/lessons/). #181's gap register named the missing
# relationship as gap 6. This gate checks the *contract* between the two loops,
# never their prose:
#
#   * one authoritative ledger and one derived view — two symmetric stores are
#     refused;
#   * the deepseek ledger reference resolves;
#   * a lesson recorded on one side is discoverable from the other, and a lesson
#     whose peer-side counterpart is missing is reported BY ID;
#   * a dispatch hint carries provenance (issue ref + commit), never a bare
#     string;
#   * a reference that would close a peer issue from here is refused
#     (cross-repo Closes is not used).
#
# The pass is governance/lessons-sync/lessons_sync.py: stdlib-only, one read per
# pinned input, no network and no `gh` in the default mode, no wall clock — so
# two runs over the same pinned inputs produce byte-identical reports. The
# report is written to a file, never streamed into the shared shell.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0 OK / 1 NOT-OK
# / 2 CANNOT-ASSESS. CANNOT-ASSESS is never a pass — a missing or unreadable
# pinned input exits 2, not 0.
#
# Usage: bash scripts/check-cross-repo-lessons.sh [extra lessons_sync.py args]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cross-repo-lessons: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

scratch="/tmp/ao-cross-repo-lessons.$(date +%s%N)"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-cross-repo-lessons: CANNOT-ASSESS — cannot create $scratch" >&2
  exit 2
fi
report="$scratch/report.json"

python3 governance/lessons-sync/lessons_sync.py --root "$root" --report "$report" "$@"
rc=$?

case "$rc" in
  0)
    echo "check-cross-repo-lessons: OK — lessons-sync contract clean (report: $report)"
    ;;
  1)
    echo "check-cross-repo-lessons: FAIL — lessons-sync contract has findings (report: $report)" >&2
    ;;
  2)
    echo "check-cross-repo-lessons: CANNOT-ASSESS — a pinned input is missing or unreadable" >&2
    ;;
  *)
    echo "check-cross-repo-lessons: CANNOT-ASSESS — unexpected exit $rc" >&2
    rc=2
    ;;
esac
exit "$rc"
