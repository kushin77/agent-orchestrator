#!/usr/bin/env bash
# scripts/gate-lock.sh — the gate admission-control entrypoint (issue #724).
#
# `scripts/verify.sh` calls `acquire` before it runs a single check, and
# `release` from its EXIT trap, so at most ONE composite gate runs per worktree
# and the box-wide permit bound is never exceeded. A gate that cannot get a
# permit is PARKED: it runs no check and overwrites no previous attestation.
#
#   bash "$root/scripts/gate-lock.sh" acquire --worktree "$root" --owner-pid $$
#   trap 'bash "$root/scripts/gate-lock.sh" release --worktree "$root"' EXIT
#
# Exit codes (the full contract is in fleet/gatelock.py):
#   0   ADMITTED — the gate may run
#   10  REFUSED  — another gate already holds this worktree (PARKED, not started)
#   11  PARKED   — every box-wide permit slot is taken (PARKED, not started)
#   12  CANNOT-ASSESS — the permit store cannot be trusted
#   13  HEALTH-ATTENTION — `doctor` found a leftover lock (alert-only, see below)
#
# `doctor` proactively sweeps every recorded worktree lock for a zero-byte or
# owner-less leftover (RCA-0007, the #948 follow-up) instead of waiting for a
# human to trace box-wide contention back to one file. It is read-only —
# alert, never auto-reap — and is meant to run from cron/the watchdog:
#   bash "$root/scripts/gate-lock.sh" doctor
#
# The permit store lives outside every workspace, and every knob is overridable
# so a test or a sibling gate never has to touch the box's real store:
#   AO_GATE_LOCK_ROOT      default ${XDG_RUNTIME_DIR:-/tmp}/agent-orchestrator-gates
#   AO_GATE_MAX_CONCURRENT default 4
#   AO_GATE_LOCK_TTL       default 900 seconds
#
# Usage: bash scripts/gate-lock.sh acquire|release|status [options]
set -uo pipefail

# A gate must not leave bytecode caches in the tree it is judging.
export PYTHONDONTWRITEBYTECODE=1

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "gate-lock: CANNOT-ASSESS — python3 not found" >&2
  exit 12
fi

if [ "$#" -eq 0 ]; then
  echo "usage: bash scripts/gate-lock.sh acquire|release|status [options]" >&2
  exit 12
fi

exec python3 "$root/fleet/gatelock.py" "$@"
