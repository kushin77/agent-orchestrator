#!/usr/bin/env bash
# check-erp-tx.sh — the transactional-spine lane's gate (issue #648, EPIC #645).
#
# WHY THIS EXISTS
#   The lane ships the closed selling -> stock -> accounting loop under
#   `integrations/erp/tx/`. `integrations/erp/tx/tests` passes as a suite and the
#   lane's own `cli.py check` measures its resolution, its golden path and its
#   refusal controls — but a suite that no gate NAMES is decorous and inert
#   (GR-12): it runs when someone remembers. This script is the gate that names
#   it, and `scripts/discover-checks.sh` (issue #698) wires it the moment it
#   lands, with no hand-edit to the `checks=()` array in `scripts/verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `integrations/erp/tx/tests` runs. A red test fails the gate.
#   2. THE LANE'S OWN TRI-STATE CHECK. `python3 -m integrations.erp.tx.cli check`
#      measures what a suite alone would not: that the lane's documents, its
#      cycle, its stock family and its ledger family are *resolved* (from the
#      indexer and the ERP-02 schemas) rather than declared; that the golden path
#      is DETERMINISTIC (two runs, one audit head and one set of balances); that
#      the cancellation path returns BOTH ledgers to their pre-cycle balances
#      while still carrying the entries; and that every refusal the lane can raise
#      is provoked by name. Its 2 (CANNOT-ASSESS) is treated as a failure here: a
#      lane that cannot read the indexer or ERP-02 cannot report OK.
#   3. THE MUTANT — the step that keeps step 2 from being a formality. The package
#      is copied to a scratch tree, the reversal of a stock movement is neutered
#      (the copied `Movement.inverted` returns the movement unchanged), and the
#      copied negative-control driver is run. It MUST go non-zero and name the
#      cancellation failure. A cancellation check that still reports OK with its
#      reversal disabled proves nothing about criterion 2, which is exactly the
#      reading the lane's own report warns about: "the net is zero" is true by
#      construction, so the invariant that has to be measured is the ledger
#      returning to its pre-cycle balances.
#   4. THE WIRING. The check asserts its own wiring: `scripts/verify.sh` sources
#      the discovery helper, and no `scripts/check-denylist.txt` entry disables
#      this check by name. An unwired gate is a formality, so the gate checks that
#      it is not one.
#
# Offline, deterministic, stdlib only: no network, no containers, no vendor
# submodule. Scratch paths are created with `tempfile.mkdtemp` (never a shell
# template) and removed on exit.
#
# Exit-code contract (repository convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-erp-tx.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

name="check-erp-tx"
suite="integrations/erp/tx/tests"
package="integrations/erp/tx"

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
if [ ! -f "$package/negative_control.py" ]; then
  echo "$name: CANNOT-ASSESS — $package/negative_control.py is missing" >&2
  exit 2
fi

# A stray __pycache__ can shadow the code under test and fake a pass (a stale
# .pyc has hidden a mutation in this repository before), so bytecode writing is
# off for every interpreter this check starts.
export PYTHONDONTWRITEBYTECODE=1

scratch="$(python3 -c 'import tempfile,sys; sys.stdout.write(tempfile.mkdtemp(prefix="ao-erp-tx-"))')"
if [ -z "$scratch" ] || [ ! -d "$scratch" ]; then
  echo "$name: CANNOT-ASSESS — no scratch directory could be created" >&2
  exit 2
fi
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT INT TERM

fail=0
report() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

echo "== 1. the transactional-spine suite =="
if python3 -m pytest -p no:cacheprovider -q "$suite"; then
  echo "  ok    $suite passed"
else
  report "$suite did not pass"
fi

echo "== 2. the lane's tri-state check (resolution, golden path, cancellation, controls) =="
cli_log="$scratch/cli-check.log"
python3 -m integrations.erp.tx.cli check >"$cli_log" 2>&1
cli_rc=$?
case "$cli_rc" in
  0)
    grep -E '^  OK|^  NOTE|^erp-tx check: OK' "$cli_log" | sed 's/^/  /'
    ;;
  2)
    report "the lane cannot assess its own definitions (CANNOT-ASSESS)"
    sed 's/^/        /' "$cli_log" >&2
    ;;
  *)
    report "integrations/erp/tx/cli.py check reported NOT-OK"
    sed 's/^/        /' "$cli_log" >&2
    ;;
esac

echo "== 3. the mutant: a neutered reversal must turn the driver red =="
mutant_root="$scratch/mutant"
mkdir -p "$mutant_root/integrations/erp" || report "could not lay out the mutant tree"
# Only the LANE'S package is copied. Everything it reads is linked to the real tree
# — ERP-02's schemas and workflows, the identity taxonomy the refusals ride on, and
# the generated indexer catalogue — so the mutation is the only difference between
# the mutant and the tree under test. `integrations/` and `integrations/erp/` carry
# no `__init__.py` in this repository, so the linked directories resolve as
# namespace packages exactly as they do in the real checkout.
for linked in identity governance integrations/erp/core integrations/erp/catalog; do
  mkdir -p "$mutant_root/$(dirname "$linked")"
  if ! ln -s "$root/$linked" "$mutant_root/$linked" 2>/dev/null || [ ! -e "$mutant_root/$linked" ]; then
    report "could not link $linked into the mutant tree"
  fi
done
cp -r "$package" "$mutant_root/integrations/erp/tx" || report "could not copy the package"
# A copied tree must not carry the original's bytecode: a stale .pyc would let
# the mutant be invisible.
find "$mutant_root" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

python3 - "$mutant_root" <<'PY'
"""Neuter the reversal of a stock movement in the copied tree, in place."""
import pathlib
import sys

target = pathlib.Path(sys.argv[1]) / "integrations" / "erp" / "tx" / "stock.py"
mutant = '''

def _mutant_inverted(self, *, at):
    """MUTANT: the reversal removed, so the driver must notice."""
    return self


Movement.inverted = _mutant_inverted
'''
target.write_text(target.read_text(encoding="utf-8") + mutant, encoding="utf-8")
PY
if ! grep -q 'MUTANT: the reversal removed' "$mutant_root/$package/stock.py"; then
  report "the mutant mutation did not land"
fi

mutant_log="$scratch/mutant.log"
if ( cd "$mutant_root" && python3 -m integrations.erp.tx.negative_control ) >"$mutant_log" 2>&1; then
  report "the mutant PASSED — the negative-control driver cannot fail, so it proves nothing"
else
  if grep -F 'after cancellation' "$mutant_log" >/dev/null 2>&1; then
    echo "  ok    the neutered reversal turned the driver red, naming the cancellation invariant"
  else
    report "the mutant failed for the wrong reason (the cancellation invariant was not named)"
    tail -n 5 "$mutant_log" | sed 's/^/        /' >&2
  fi
fi

echo "== 4. the check is wired, not a formality =="
if grep -qF 'scripts/discover-checks.sh' scripts/verify.sh; then
  echo "  ok    scripts/verify.sh sources scripts/discover-checks.sh, which discovers this check"
else
  report "scripts/verify.sh does not source scripts/discover-checks.sh, so a new check is inert"
fi
if [ -f scripts/check-denylist.txt ] && grep -qx 'erp-tx' scripts/check-denylist.txt; then
  report "erp-tx is disabled by name in scripts/check-denylist.txt"
else
  echo "  ok    erp-tx is not denylisted"
fi

echo ""
if [ "$fail" -ne 0 ]; then
  echo "$name: NOT-OK — $fail problem(s)" >&2
  exit 1
fi
echo "$name: OK — suite, resolution check, cancellation mutant and wiring all measured"
exit 0
