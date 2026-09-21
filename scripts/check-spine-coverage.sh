#!/usr/bin/env bash
# check-spine-coverage.sh — full-spine rule-to-control coverage gate
# (issue #890, lane L11 of EPIC #878).
#
# THE DEFECT THIS EXISTS FOR
#   scripts/check-control-coverage.sh (#874) validates only Part B
#   (AO-GR-12..20) of docs/GOLDEN-RULES.md against scripts/control-coverage.tsv.
#   Nothing validated Part A (AO-GR-1..11), Part C (AO-GR-21..27), or the hub
#   (kushin77/CMR) rules this repo's spine cites by number — so a reviewer had
#   no machine-checked path from those rule ids to the controls that enforce
#   them, and a control could be silently dropped from the map without any
#   gate noticing.
#
# WHAT IT DOES
#   Runs governance/controls/check_spine_coverage.py against
#   governance/controls/spine-coverage.yaml, which parses every rule id out of
#   AGENTS.md and docs/GOLDEN-RULES.md and asserts: every rule has a row;
#   every gate file a covered row names exists and is executable; every
#   suite:<dir> gate is declared in scripts/pytest-suites.txt; no row claims
#   `covered: true` without a real gate; GAP rows are reported by name, never
#   rounded up.
#
# HOW IT PROVES ITSELF
#   Delegates to the checker's own --self-test, which validates the real map
#   (must PASS) and then a mutated copy whose gate is repointed at a
#   nonexistent script (must FAIL, naming the mutated rule and the phantom
#   path). A gate that cannot fail is a formality (AO-GR-4).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-spine-coverage.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

checker="governance/controls/check_spine_coverage.py"
map="governance/controls/spine-coverage.yaml"

if [ ! -f "$checker" ]; then
  printf 'check-spine-coverage: missing %s\n' "$checker" >&2
  exit 2
fi
if [ ! -f "$map" ]; then
  printf 'check-spine-coverage: missing %s\n' "$map" >&2
  exit 2
fi

python3="$(command -v python3 || true)"
if [ -z "$python3" ]; then
  printf 'check-spine-coverage: python3 not found\n' >&2
  exit 2
fi

"$python3" "$checker" --self-test
rc=$?

if [ "$rc" -eq 0 ]; then
  printf 'check-spine-coverage: OK\n'
elif [ "$rc" -eq 1 ]; then
  printf 'check-spine-coverage: NOT-OK\n' >&2
else
  printf 'check-spine-coverage: CANNOT-ASSESS\n' >&2
fi

exit "$rc"
