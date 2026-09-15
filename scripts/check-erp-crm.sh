#!/usr/bin/env bash
# check-erp-crm.sh — the CRM-family lane's gate (issue #650, EPIC #645).
#
# WHY THIS EXISTS
#   The lane ships a document surface and four flows under
#   `integrations/erp/crm/`. `integrations/erp/crm/tests` passes as a suite and
#   the lane's own `cli.py check` measures its declarations, its golden path and
#   its refusal controls — but a suite that no gate NAMES is decorous and inert
#   (GR-12): it runs when someone remembers. This script is the gate that names
#   it, and `scripts/discover-checks.sh` (issue #698) wires it the moment it
#   lands, with no hand-edit to the `checks=()` array in `scripts/verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `integrations/erp/crm/tests` runs. A red test fails the gate.
#   2. THE LANE'S OWN TRI-STATE CHECK. `python3 -m integrations.erp.crm.cli
#      check` measures what a suite alone would not: that the shipped
#      declaration set covers every kind this module owns, that the inspection
#      outcome vocabulary the code carries is the one the declaration declares,
#      that the golden path is DETERMINISTIC (two runs, one audit head), and
#      that every refusal the module can raise is provoked and refused by name.
#      Its 2 (CANNOT-ASSESS) is treated as a failure here: a lane that cannot
#      read its own declarations cannot report OK.
#   3. THE MUTANT — the step that keeps step 2 from being a formality. The
#      package is copied to a scratch tree, the legality check in
#      `workflow.py` is removed (the mutant redefines `assert_transition` to
#      return without refusing), and the copied driver is run. It MUST go
#      non-zero and name `illegal-transition` as NOT refused. A driver that
#      still reports OK with its state machine neutered proves nothing about
#      the controls it claims to run, so the mutant failing is what makes the
#      rest of the output evidence.
#   4. THE WIRING. The check asserts its own wiring: `scripts/verify.sh` sources
#      the discovery helper, and no `scripts/check-denylist.txt` entry disables
#      this check by name. An unwired gate is a formality, so the gate checks
#      that it is not one.
#
# Offline, deterministic, stdlib only: no network, no containers, no vendor
# submodule. Scratch paths are created with `tempfile.mkdtemp` (never a shell
# template) and removed on exit.
#
# Exit-code contract (repository convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-erp-crm.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

name="check-erp-crm"
suite="integrations/erp/crm/tests"
package="integrations/erp/crm"

if ! command -v python3 >/dev/null 2>&1; then
  echo "$name: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -d "$suite" ]; then
  echo "$name: CANNOT-ASSESS — $suite is missing" >&2
  exit 2
fi
if [ ! -f "$package/cli.py" ]; then
  echo "$name: CANNOT-ASSESS — $package/cli.py is missing" >&2
  exit 2
fi

# A stray __pycache__ can shadow the code under test and fake a pass (a stale
# .pyc has hidden a mutation in this repository before), so bytecode writing is
# off for every interpreter this check starts.
export PYTHONDONTWRITEBYTECODE=1

scratch="$(python3 -c 'import tempfile,sys; sys.stdout.write(tempfile.mkdtemp(prefix="ao-erp-crm-"))')"
if [ -z "$scratch" ] || [ ! -d "$scratch" ]; then
  echo "$name: CANNOT-ASSESS — no scratch directory could be created" >&2
  exit 2
fi
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT INT TERM

fail=0
report() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

echo "== 1. the CRM-family suite =="
if python3 -m pytest -p no:cacheprovider -q "$suite"; then
  echo "  ok    $suite passed"
else
  report "$suite did not pass"
fi

echo "== 2. the lane's tri-state check (declarations, golden path, controls) =="
cli_log="$scratch/cli-check.log"
python3 -m integrations.erp.crm.cli check >"$cli_log" 2>&1
cli_rc=$?
case "$cli_rc" in
  0)
    grep -E '^  OK|^erp-crm check: OK' "$cli_log" | sed 's/^/  /'
    ;;
  2)
    report "the lane cannot assess its own declarations (CANNOT-ASSESS)"
    sed 's/^/        /' "$cli_log" >&2
    ;;
  *)
    report "integrations/erp/crm/cli.py check reported NOT-OK"
    sed 's/^/        /' "$cli_log" >&2
    ;;
esac

echo "== 3. the mutant: a neutered state machine must turn the driver red =="
mutant_root="$scratch/mutant"
mkdir -p "$mutant_root/integrations/erp" || report "could not lay out the mutant tree"
cp -r "$package" "$mutant_root/integrations/erp/crm" || report "could not copy the package"
# A copied tree must not carry the original's bytecode: a stale .pyc would let
# the mutant be invisible.
find "$mutant_root" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

python3 - "$mutant_root" <<'PY'
"""Neuter the legality check in the copied tree, in place."""
import pathlib
import sys

target = pathlib.Path(sys.argv[1]) / "integrations" / "erp" / "crm" / "workflow.py"
mutant = '''

def assert_transition(document, target, definitions):
    """MUTANT: the legality check removed, so the driver must notice."""
    return None
'''
target.write_text(target.read_text(encoding="utf-8") + mutant, encoding="utf-8")
PY
if ! grep -q 'MUTANT: the legality check removed' "$mutant_root/$package/workflow.py"; then
  report "the mutant mutation did not land"
fi

mutant_log="$scratch/mutant.log"
if ( cd "$mutant_root" && python3 -m integrations.erp.crm.negative_control ) >"$mutant_log" 2>&1; then
  report "the mutant PASSED — the negative-control driver cannot fail, so it proves nothing"
else
  if grep -F 'illegal-transition' "$mutant_log" >/dev/null 2>&1; then
    echo "  ok    the neutered machine turned the driver red, naming illegal-transition"
  else
    report "the mutant failed for the wrong reason (illegal-transition was not named)"
    tail -n 5 "$mutant_log" | sed 's/^/        /' >&2
  fi
fi

echo "== 4. the check is wired, not a formality =="
if grep -qF 'scripts/discover-checks.sh' scripts/verify.sh; then
  echo "  ok    scripts/verify.sh sources scripts/discover-checks.sh, which discovers this check"
else
  report "scripts/verify.sh does not source scripts/discover-checks.sh, so a new check is inert"
fi
if [ -f scripts/check-denylist.txt ] && grep -qx 'erp-crm' scripts/check-denylist.txt; then
  report "erp-crm is disabled by name in scripts/check-denylist.txt"
else
  echo "  ok    erp-crm is not denylisted"
fi

echo ""
if [ "$fail" -ne 0 ]; then
  echo "$name: NOT-OK — $fail problem(s)" >&2
  exit 1
fi
echo "$name: OK — suite, declaration check, mutant and wiring all measured"
exit 0
