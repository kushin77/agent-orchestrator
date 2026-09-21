#!/usr/bin/env bash
# check-gate-coverage-control.sh — provoke the gate-coverage refusal on every `make verify` (#725).
#
# The detector this control guards is scripts/check-gate-coverage.sh (#526,
# provenance #603): it fails BY NAME when a delivered `scripts/check-*.sh` is
# invoked by no gate file. The refusal was proven exactly once, by hand, inside
# #737 (a throwaway scripts/check-zzz-probe.sh, created and deleted inside the
# commit) — so nothing re-derived it, and a refusal nothing re-derives rots into
# a formality (GR-12 / AO-GR-19).
#
# THE CONTROL ITSELF lives in scripts/tests/test_gate_coverage.py: it builds a
# scratch git work tree carrying the shipped detector byte-for-byte and asserts
# the refusal, the acceptance, and the never-grandfathered rule.
#
# WHY THIS SCRIPT EXISTS ON TOP OF THE PYTEST CORPUS
#   `make verify` does NOT run the `scripts` pytest suite. `scripts` is declared
#   in scripts/pytest-suites.txt and accepted in scripts/gate-coverage-baseline.txt
#   as `swept-only` (#524), so its tests run under `make gate`
#   (scripts/run-pytest-suites.sh) and never under the gate of record. A control
#   the gate of record does not provoke is available, not exercised. This script
#   is auto-wired into scripts/verify.sh by the #698 discovery layer the moment it
#   lands, so the refusal path is re-derived on EVERY `make verify` with no
#   hand-edit to the gate.
#
# Exit-code contract (guardrails/honesty tri-state, consumed not redefined): 0/1/2.
#   0  OK             the detector still refuses an unwired check by name
#   1  NOT-OK         the refusal is no longer provoked (a formality)
#   2  CANNOT-ASSESS  the control module is missing
#
# Usage: bash scripts/check-gate-coverage-control.sh [--prove]
#   --prove  re-derive the refusal outside the gate and print the detector's own
#            output verbatim (the one-command demonstration a human would
#            otherwise have to reconstruct from scratch).
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

control="scripts/tests/test_gate_coverage.py"
if [ ! -f "$control" ]; then
  echo "check-gate-coverage-control: CANNOT-ASSESS — $control is missing" >&2
  exit 2
fi

if [ "${1:-}" = "--prove" ]; then
  exec env PYTHONDONTWRITEBYTECODE=1 python3 "$control"
fi

echo "== gate-coverage negative control =="
if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$control"; then
  echo "gate-coverage control: OK — an unwired check is still refused by name"
  exit 0
fi

echo "check-gate-coverage-control: NOT-OK — the gate-coverage refusal is no longer provoked" >&2
exit 1
