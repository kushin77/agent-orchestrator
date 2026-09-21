#!/usr/bin/env bash
# peer-check.sh — the A2A peer-check standard, runnable (issue #1549, EPIC #1510).
#
# WHAT IT IS
#   The entry point for the standard: enumerate the live sibling lanes, judge
#   each one's files against YOUR claimed files, and REFUSE BY NAME when a
#   sibling already holds a file you are about to touch. The rule and the engine
#   live in ONE place — `governance/dispatch/peers.py` — and this script is the
#   invocation a lane actually types. `--standard` prints the rule itself, so the
#   standard has exactly one copy in the tree.
#
# WHY IT REFUSES RATHER THAN WARNS
#   Two lanes editing one file is the measured failure this exists for (the wave
#   planner's sibling, issue #740: 27 source-file collisions across 14 lanes).
#   A warning that a lane may ignore is a formality; exit 1 stops a lane that
#   checks its own exit status, which is what the dispatcher and the fleet loop
#   do. The overlap decision is `model.file_claims_conflict`, imported and never
#   re-implemented, so this cannot drift from the claim path (#702).
#
# WHERE THE LIVE LEDGER LIVES
#   `.board/claims/` is UNTRACKED, so a lane worktree does not carry it — the
#   shared (main) worktree does. The module resolves the main worktree itself and
#   NAMES every location it checked, so "no siblings" is never a silent artifact
#   of reading the wrong directory. Override with `--ledger` or `$AO_CLAIMS_DIR`.
#
# EXIT CONTRACT (guardrails/honesty tri-state, consumed never redefined)
#   0  disjoint (or every sibling unverifiable but named) — proceed
#   1  OVERLAP — refused, naming the sibling, its channel and the exact paths
#   2  CANNOT-ASSESS — no caller identity, no ledger, or an unreadable store
#
# Offline by default: the git file source reads LOCAL refs only (no fetch). If
# the ledger lives somewhere else entirely, pass --ledger and nothing else needs
# the network.
#
# Usage:
#   bash scripts/peer-check.sh --agent ao-sub-1549 --issue 1549 --files a.sh,b.py
#   bash scripts/peer-check.sh --standard
#   bash scripts/peer-check.sh --json
#
# ---knowledge---
# module_id: scripts.peer-check
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [self-proving-gate, declared-authority]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [--agent, --issue, --files, --standard, --json]
# invariants: "--ledger lets the caller point at a ledger elsewhere without touching the network"
# gotchas: ""
# related: ["#1549"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
root="$(cd "$self_dir/.." && pwd -P)" || exit 2
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "peer-check: CANNOT-ASSESS — python3 is not installed" >&2
  exit 2
fi

engine="$root/governance/dispatch/peers.py"
if [ ! -f "$engine" ]; then
  echo "peer-check: CANNOT-ASSESS — the engine $engine is missing" >&2
  exit 2
fi

# The caller's identity defaults to the session identity rule 15 mints, so a lane
# that forgot to pass --agent is not silently anonymous (the module refuses an
# empty caller rather than judging every lane against nobody's files).
caller_agent="${AO_AGENT_ID:-${AO_SESSION_ID:-}}"
caller_issue="${AO_ISSUE:-}"
ledger_arg="${AO_CLAIMS_DIR:-}"

args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent|--caller-agent)
      caller_agent="${2:-}"
      shift 2
      ;;
    --issue|--caller-issue)
      caller_issue="${2:-}"
      shift 2
      ;;
    --ledger)
      ledger_arg="${2:-}"
      shift 2
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

cmd=(python3 "$engine" --root "$root")
if [ -n "$caller_agent" ]; then
  cmd+=(--caller-agent "$caller_agent")
fi
if [ -n "$caller_issue" ]; then
  cmd+=(--caller-issue "$caller_issue")
fi
if [ -n "$ledger_arg" ]; then
  cmd+=(--ledger "$ledger_arg")
fi
if [ "${#args[@]}" -gt 0 ]; then
  cmd+=("${args[@]}")
fi

"${cmd[@]}"
rc=$?
exit "$rc"
