#!/usr/bin/env bash
# land-lane.sh — thin entrypoint for the code-native landing driver (issue #764).
#
# `make land ISSUE=<n>` calls this; the ops runner and cron call `make land`, so
# the landing path is a declared make target, never a workflow file (GR-15) and
# never a console click (GR-5).
#
# DRY RUN BY DEFAULT. Without an explicit opt-in the driver reads the lane,
# consults governance/merge's verdict and prints what it *would* do; it changes
# nothing remotely. To land for real:
#
#   AO_LAND_APPLY=1 make land ISSUE=764
#   bash scripts/land-lane.sh --issue 764 --apply
#
# Every other flag of `governance/landing/cli.py land` passes straight through
# (--branch, --base, --attestation, --title, --author, --notes-file, --json, …).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/land-lane.sh --issue <n> [cli flags…]
#
# ---knowledge---
# module_id: scripts.land-lane
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: [tri-state-exit, lane-isolation]
# derives_from: governance/landing/cli.py
# owner_sme: platform-sme
# tier: L0
# interfaces: [exec python3 governance/landing/cli.py]
# invariants: ""
# gotchas: ""
# related: ["#764"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"

issue=""
root_arg=""
passthru=()
while [ $# -gt 0 ]; do
  case "$1" in
    --issue)    issue="${2:-}"; shift 2 ;;
    --issue=*)  issue="${1#*=}"; shift ;;
    --root)     root_arg="${2:-}"; shift 2 ;;
    --root=*)   root_arg="${1#*=}"; shift ;;
    *)          passthru+=("$1"); shift ;;
  esac
done
# `make land ISSUE=<n>` passes the issue through the environment.
if [ -z "$issue" ]; then
  issue="${ISSUE:-}"
fi
if [ -z "$issue" ]; then
  echo "land-lane: usage: bash scripts/land-lane.sh --issue <n> [cli flags…]  (or: make land ISSUE=<n>)" >&2
  exit 2
fi
# Land from this checkout unless the caller named another one (cron lands the
# shared checkout; a lane lands its own worktree).
if [ -z "$root_arg" ]; then
  root_arg="$root"
fi

# The opt-in is explicit in exactly one place: --apply, or AO_LAND_APPLY=1.
if [ "${AO_LAND_APPLY:-0}" = "1" ]; then
  passthru+=(--apply)
fi

exec python3 "$root/governance/landing/cli.py" land --issue "$issue" --root "$root_arg" "${passthru[@]}"
