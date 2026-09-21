#!/usr/bin/env bash
# check-cross-repo-sync.sh — the scheduled peer-board triage gate (issue #427).
#
# This is the machine surface for cross-repo sync ownership. It runs the
# deterministic, offline pass in governance/sync/peer_triage.py against the
# pinned peer-board snapshot and fails honestly when the pass cannot hold the
# contract:
#
#   * every item the pass surfaces (a peer issue that names kushin77/
#     agent-orchestrator, or that maps onto a lane this repo owns) carries an
#     explicit disposition — track-here / direction / no-action; an item with
#     no disposition is a finding, never silence;
#   * anything adopted from a peer carries provenance (repo + issue + sha);
#   * a run that would close a peer issue from here is refused (cross-repo
#     Closes is not used — the boundary hands off, it does not resolve);
#   * a peer reference is never adopted without a link back to the local issue
#     that consumes it.
#
# The pass is dirt-cheap by construction: stdlib only, one read per pinned
# input, no network, no `gh`, no wall clock — so two runs over the same pinned
# inputs produce byte-identical reports. The report is written to a file, never
# streamed into the shared shell.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0 OK / 1 NOT-OK
# / 2 CANNOT-ASSESS. CANNOT-ASSESS is never a pass — a missing or unreadable
# pinned input exits 2, not 0.
#
# Usage: bash scripts/check-cross-repo-sync.sh [extra peer_triage.py args]
#
# ---knowledge---
# module_id: scripts.check-cross-repo-sync
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, offline-hermetic, deterministic]
# derives_from: null
# owner_sme: sync-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#28", "#427"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cross-repo-sync: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

scratch="/tmp/ao-cross-repo-sync.$(date +%s%N)"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-cross-repo-sync: CANNOT-ASSESS — cannot create $scratch" >&2
  exit 2
fi
report="$scratch/report.json"

python3 governance/sync/peer_triage.py --report "$report" "$@"
rc=$?

case "$rc" in
  0)
    echo "check-cross-repo-sync: OK — peer-board triage clean (report: $report)"
    ;;
  1)
    echo "check-cross-repo-sync: FAIL — peer-board triage has findings (report: $report)" >&2
    ;;
  2)
    echo "check-cross-repo-sync: CANNOT-ASSESS — a pinned input is missing or unreadable" >&2
    ;;
  *)
    echo "check-cross-repo-sync: CANNOT-ASSESS — unexpected exit $rc" >&2
    rc=2
    ;;
esac
exit "$rc"
