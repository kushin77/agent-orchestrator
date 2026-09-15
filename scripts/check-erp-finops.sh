#!/usr/bin/env bash
# check-erp-finops.sh — the ERP FinOps lane's gate (issue #654, EPIC #645).
#
# WHY THIS EXISTS
#   The lane ships a billable surface under `integrations/erp/finops/`: every ERP
#   document create and transition is metered onto the platform audit chain and
#   usage feed, rolled up per tenant with a cost, and stopped by name when a
#   tenant reaches its budget. `integrations/erp/finops/tests` passes as a suite
#   and the lane's own `cli.py check` measures its declarations, its cost
#   arithmetic, the determinism of its hard stop and the digest of the telemetry
#   tree it consumes — but a suite that no gate NAMES is decorous and inert
#   (GR-12): it runs when someone remembers. This script is the gate that names
#   it, and `scripts/discover-checks.sh` (issue #698) wires it the moment it
#   lands, with no hand-edit to the `checks=()` array in `scripts/verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `integrations/erp/finops/tests` runs. A red test fails the gate.
#      Two of those tests are themselves controls: one requires the negative
#      control to go red when the budget guard is neutered, and one is the suite's
#      copy of the tripwire below.
#   2. THE LANE'S OWN TRI-STATE CHECK. `python3 -m integrations.erp.finops.cli
#      check` measures what a suite cannot: that the rate card covers the LIVE
#      core document model, that a create for every kind and a transition for
#      every declared move landed one ledger record each (compared against the
#      ledger, not an in-memory list), that the roll-up's figure is the rate
#      card's own sum, that the hard stop is deterministic and left nothing
#      behind, and that the content digest of every tracked file under
#      `telemetry/` is unchanged by a full metered run (acceptance criterion 3).
#      Its 2 (CANNOT-ASSESS) is treated as a failure here: a lane that cannot read
#      its own declarations cannot report OK.
#   3. THE TRIPWIRE — the step that keeps step 2 from being a formality. The
#      lane's package is copied to a scratch tree (with the `telemetry/` and
#      `identity/` pillars it consumes), the copy is proved GREEN as a control,
#      the hard stop is then removed from `budget.py` in that copy, and the copied
#      negative-control driver must go RED naming `budget-exhausted`. The green
#      copy is what makes the red mutant mean the mutation rather than a broken
#      copy — and a driver that still reports OK with its hard stop removed proves
#      nothing about the controls it claims to run.
#   4. ACCEPTANCE CRITERION 3, INDEPENDENTLY. Step 2 hashes the telemetry tree
#      from inside the lane; this step asks git the same question from outside
#      (`git status --porcelain --untracked-files=no telemetry`). Two different
#      measurements of the same criterion, so a bug in the digest helper cannot
#      hide a real edit.
#   5. THE WIRING. The check asserts its own wiring: `scripts/verify.sh` sources
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
# Usage: bash scripts/check-erp-finops.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

name="check-erp-finops"
suite="integrations/erp/finops/tests"
package="integrations/erp/finops"

if ! command -v python3 >/dev/null 2>&1; then
  echo "$name: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -d "$suite" ]; then
  echo "$name: CANNOT-ASSESS — $suite is missing" >&2
  exit 2
fi
if [ ! -f "$package/cli.py" ] || [ ! -f "$package/negative_control.py" ]; then
  echo "$name: CANNOT-ASSESS — $package/cli.py or negative_control.py is missing" >&2
  exit 2
fi

# A stray __pycache__ can shadow the code under test and fake a pass (a stale
# .pyc has hidden a mutation in this repository before), so bytecode writing is
# off for every interpreter this check starts.
export PYTHONDONTWRITEBYTECODE=1

scratch="$(python3 -c 'import tempfile,sys; sys.stdout.write(tempfile.mkdtemp(prefix="ao-erp-finops-"))')"
if [ -z "$scratch" ] || [ ! -d "$scratch" ]; then
  echo "$name: CANNOT-ASSESS — no scratch directory could be created" >&2
  exit 2
fi
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT INT TERM

fail=0
report() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

echo "== 1. the ERP FinOps suite =="
if python3 -m pytest -p no:cacheprovider -q "$suite"; then
  echo "  ok    $suite passed"
else
  report "$suite did not pass"
fi

echo "== 2. the lane's tri-state check (coverage, chain, cost, stop, isolation) =="
cli_log="$scratch/cli-check.log"
python3 -m integrations.erp.finops.cli check >"$cli_log" 2>&1
cli_rc=$?
case "$cli_rc" in
  0)
    grep -E '^  OK' "$cli_log" | sed 's/^/  /'
    ;;
  2)
    report "the lane cannot assess its own declarations (CANNOT-ASSESS)"
    sed 's/^/        /' "$cli_log" >&2
    ;;
  *)
    report "integrations.erp.finops.cli check reported NOT-OK"
    sed 's/^/        /' "$cli_log" >&2
    ;;
esac

echo "== 3. the tripwire: a removed hard stop must turn the driver red =="
mutant_root="$scratch/mutant"
mkdir -p "$mutant_root/integrations" || report "could not lay out the mutant tree"
cp -r "integrations/erp" "$mutant_root/integrations/erp" || report "could not copy the ERP packages"
# The lane consumes two pillars and the control-plane error taxonomy through
# their public APIs; the copy must carry them or the mutant would fail to
# import and the tripwire would prove an ImportError instead of a dead control.
cp -r "telemetry" "$mutant_root/telemetry" || report "could not copy telemetry/"
cp -r "identity" "$mutant_root/identity" || report "could not copy identity/"
# A copied tree must not carry the original's bytecode: a stale .pyc would let
# the mutant be invisible.
find "$mutant_root" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

# The control: the UNMUTATED copy must be green, so a red mutant is the mutation
# and not a broken copy.
control_log="$scratch/control.log"
if ( cd "$mutant_root" && python3 -m integrations.erp.finops.negative_control ) >"$control_log" 2>&1; then
  echo "  ok    the copied tree (unmutated) provokes every refusal, so the copy is sound"
else
  report "the copied tree is red BEFORE the mutation, so the tripwire would prove nothing"
  tail -n 5 "$control_log" | sed 's/^/        /' >&2
fi

python3 - "$mutant_root" <<'PY'
"""Remove the ERP budget hard stop in the copied tree, in place."""
import pathlib
import sys

target = pathlib.Path(sys.argv[1]) / "integrations" / "erp" / "finops" / "budget.py"
mutant = '''

def _unused_guard(self, tenant, *, requested_cost_usd=0.0, month=None, where=None):
    """MUTANT: the pre-operation budget check removed, so nothing stops."""
    return None


ErpBudgetGuard.guard = _unused_guard
'''
target.write_text(target.read_text(encoding="utf-8") + mutant, encoding="utf-8")
PY
if ! grep -q 'MUTANT: the pre-operation budget check removed' "$mutant_root/$package/budget.py"; then
  report "the mutant mutation did not land"
fi

mutant_log="$scratch/mutant.log"
if ( cd "$mutant_root" && python3 -m integrations.erp.finops.negative_control ) >"$mutant_log" 2>&1; then
  report "the mutant PASSED — the negative-control driver cannot fail, so it proves nothing"
else
  if grep -F 'budget-exhausted' "$mutant_log" >/dev/null 2>&1; then
    echo "  ok    the removed hard stop turned the driver red, naming budget-exhausted"
  else
    report "the mutant failed for the wrong reason (budget-exhausted was not named)"
    tail -n 5 "$mutant_log" | sed 's/^/        /' >&2
  fi
fi

echo "== 4. acceptance criterion 3, asked independently of the lane's own digest =="
telemetry_dirty="$(git status --porcelain --untracked-files=no -- telemetry 2>/dev/null || true)"
if [ -n "$telemetry_dirty" ]; then
  report "a tracked file under telemetry/ is modified by this lane:"
  printf '%s\n' "$telemetry_dirty" | sed 's/^/        /' >&2
else
  echo "  ok    git reports no tracked change under telemetry/ (the pillar is consumed, not edited)"
fi

echo "== 5. the check is wired, not a formality =="
if grep -qF 'scripts/discover-checks.sh' scripts/verify.sh; then
  echo "  ok    scripts/verify.sh sources scripts/discover-checks.sh, which discovers this check"
else
  report "scripts/verify.sh does not source scripts/discover-checks.sh, so a new check is inert"
fi
if [ -f scripts/check-denylist.txt ] && grep -qx 'erp-finops' scripts/check-denylist.txt; then
  report "erp-finops is disabled by name in scripts/check-denylist.txt"
else
  echo "  ok    erp-finops is not denylisted"
fi

echo ""
if [ "$fail" -ne 0 ]; then
  echo "$name: NOT-OK — $fail problem(s)" >&2
  exit 1
fi
echo "$name: OK — suite, lane check, tripwire, telemetry isolation and wiring all measured"
exit 0
