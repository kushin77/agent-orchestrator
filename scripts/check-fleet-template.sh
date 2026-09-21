#!/usr/bin/env bash
# check-fleet-template.sh — per-repo agent fleet template gate (issue #146).
#
# The template is only a template if a committed instance cannot drift away from
# it. This gate renders every pilot from its params and refuses when a committed
# instance disagrees (drift), when a declared invariant is broken, or when two
# repositories' fleets claim the same identity or state.
#
# Contract: the template and the rendered instances are declared data validated
# against control-plane/fleet-template/schema.yaml; drift is NOT-OK; an input
# that cannot be assessed (missing/unparseable/schema-invalid) is
# CANNOT-ASSESS and NEVER reads as a pass (no-false-green, AO-GR-4).
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0/1/2.
#   * every pilot renders and matches its params      -> OK            (exit 0)
#   * drift, invariant violation, missing pilot       -> NOT-OK        (exit 1)
#   * missing/unparseable/schema-invalid inputs       -> CANNOT-ASSESS (exit 2)
#
# Usage:
#   bash scripts/check-fleet-template.sh
#   bash scripts/check-fleet-template.sh --root <dir>   # gate self-control only
#
# `--root` exists so this gate's own failure paths are testable from a scratch
# copy of the lane; the orchestrator wires the bare form into the gate of
# record.
#
# ---knowledge---
# module_id: scripts.check-fleet-template
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, no-false-green, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#28", "#146"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
lane_root="$root/control-plane/fleet-template"

while [ $# -gt 0 ]; do
  case "$1" in
    --root)
      [ $# -ge 2 ] || { echo "check-fleet-template: CANNOT-ASSESS — --root needs a directory" >&2; exit 2; }
      lane_root="$2"
      shift 2
      ;;
    -h|--help)
      echo "usage: bash scripts/check-fleet-template.sh [--root <fleet-template dir>]"
      exit 0
      ;;
    *)
      echo "check-fleet-template: CANNOT-ASSESS — unknown argument $1" >&2
      exit 2
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-template: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

module="$lane_root/render.py"
if [ ! -f "$module" ]; then
  echo "check-fleet-template: CANNOT-ASSESS — $module is missing (nothing to run)" >&2
  exit 2
fi

out="$(python3 "$module" --root "$lane_root" check 2>&1)"
rc=$?
printf '%s\n' "$out"

case "$rc" in
  0) echo "check-fleet-template: OK — every pilot renders and matches its params; no drift, no isolation finding"; exit 0 ;;
  1) echo "check-fleet-template: NOT-OK — fleet-template drift or invariant finding(s) above" >&2; exit 1 ;;
  2) echo "check-fleet-template: CANNOT-ASSESS — the fleet contract could not be assessed (see above)" >&2; exit 2 ;;
  *) echo "check-fleet-template: CANNOT-ASSESS — unexpected module exit code $rc" >&2; exit 2 ;;
esac
