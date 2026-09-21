#!/usr/bin/env bash
# check-erp-ops.sh — the ERP-04 procurement + manufacturing lane's gate (issue #649, EPIC #645).
#
# WHY THIS EXISTS
#   The lane ships the buying and production documents and the posting rules
#   under `integrations/erp/ops/`. `integrations/erp/ops/tests` passes as a
#   suite and the lane's own `cli.py check` measures its declarations, its
#   posting rules, the determinism of its golden path and all of its refusals —
#   but a suite that no gate NAMES is decorous and inert (GR-12): it runs when
#   someone remembers. This script is the gate that names it, and
#   `scripts/discover-checks.sh` (#698) wires it the moment it lands, with no
#   hand-edit to the `checks=()` array in `scripts/verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `integrations/erp/ops/tests` runs. A red test fails the gate.
#   2. THE LANE'S OWN TRI-STATE CHECK. `python3 -m integrations.erp.ops.cli
#      check` measures what a suite alone would not: that the declaration set
#      matches its FROZEN schema, that the asset contract holds for BOTH halves
#      of the document model (ERP-02's families and this lane's), that every
#      posting rule balances, that the golden path is DETERMINISTIC (two runs,
#      one digest) and ties out to zero across every ledger account, and that
#      every refusal of the closed vocabulary is provoked BY NAME. Its 2
#      (CANNOT-ASSESS) is treated as a failure here: a lane that cannot read its
#      own declarations cannot report OK.
#      The number of declared refusals is read FROM THE MODEL rather than typed
#      into this file, so the coverage line this script demands cannot drift out
#      of step with the vocabulary it is checking.
#   3. THE MUTANT — the step that keeps step 2 from being a formality. The
#      package is copied to a SCRATCH tree (with the ERP-02 core it consumes and
#      the identity contracts that core imports), the rule that refuses a
#      receipt citing no purchase order is disabled (`if order is None:` ->
#      `if False:`), and the copied driver is run. It MUST go non-zero and name
#      `receipt-without-order` as no longer refused.
#      The receipt-without-order rule is the lane's central buying invariant —
#      goods arriving with no commitment behind them — so a driver that still
#      reported OK with it removed would prove nothing about anything else it
#      reports, and `make verify` would be certifying a lane whose books cannot
#      be trusted.
#
# THE MUTANT CARRIES ITS OWN NEGATIVE CONTROL. Before judging the mutant's
#   output, the script asserts the mutation actually LANDED (the anchor occurs
#   exactly once and the file's sha256 changed). A harness that reports "the
#   gate caught it" while the edit never applied is a false green — and it has
#   happened in this repository.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

#
# ---knowledge---
# module_id: scripts.check-erp-ops
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#645", "#649", "#698"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

name="check-erp-ops"
suite="integrations/erp/ops/tests"
package="integrations/erp/ops"

failures=0
ok()   { printf '  OK    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }

for tool in python3 sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "$name: CANNOT-ASSESS - $tool is not on PATH" >&2
    exit 2
  }
done
if [ ! -d "$suite" ]; then
  echo "$name: CANNOT-ASSESS - $suite is missing" >&2
  exit 2
fi
if [ ! -f "$package/cli.py" ]; then
  echo "$name: CANNOT-ASSESS - $package/cli.py is missing" >&2
  exit 2
fi

# A stray __pycache__ can shadow the code under test and fake a pass (a stale
# .pyc has hidden a mutation in this repository before), so bytecode writing is
# off for every interpreter this check starts.
export PYTHONDONTWRITEBYTECODE=1

echo "== erp-ops: the suite =="
suite_out="$(python3 -m pytest -p no:cacheprovider -q "$suite" 2>&1)"
suite_rc=$?
printf '%s\n' "$suite_out" | tail -3 | sed 's/^/  /'
if [ "$suite_rc" -eq 0 ]; then
  ok "the suite passes"
else
  fail "the suite failed (rc=$suite_rc)"
fi

echo
echo "== erp-ops: the lane's own check =="
check_out="$(python3 -m integrations.erp.ops.cli check 2>&1)"
check_rc=$?
printf '%s\n' "$check_out" | grep -E '^  FAIL|^erp-ops check' | sed 's/^/  /'
case "$check_rc" in
  0) ok "the lane's own check reports OK" ;;
  2) fail "the lane's own check is CANNOT-ASSESS - it cannot read its own declarations" ;;
  *) fail "the lane's own check failed (rc=$check_rc)" ;;
esac

declared="$(python3 -c 'from integrations.erp.ops.model import REFUSALS; print(len(REFUSALS))' 2>/dev/null)"
if [ -z "$declared" ]; then
  fail "the refusal vocabulary could not be read, so coverage cannot be asserted"
else
  case "$check_out" in
    *"$declared of $declared declared refusal(s) provoked"*)
      ok "every declared refusal is provoked, by name ($declared of $declared)" ;;
    *)
      fail "the refusal coverage line is absent - not all $declared controls ran" ;;
  esac
fi

echo
echo "== erp-ops: the mutant — the receipt-without-order rule disabled =="
# A unique scratch dir WITHOUT a trailing run of `X`: this repository's docs-lint
# scans for unfinished markers and a `mktemp` X-suffix is one of them, so this
# follows the convention the other gates use — an explicit name built from a
# timestamp and the pid, with `mkdir` refusing loudly rather than silently
# reusing another run's tree.
work="${TMPDIR:-/tmp}/ao-erp-ops-mutant.$(date +%s%N).$$"
if ! mkdir "$work" 2>/dev/null; then
  echo "$name: CANNOT-ASSESS - cannot create a scratch dir at $work" >&2
  exit 2
fi
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# A scratch tree the copied driver runs in: the package under test, the ERP-02
# core it consumes (whose model validates the families this lane drives, and
# whose loader the lane's own families are loaded by), and the identity
# contracts core's error taxonomy imports.
mkdir -p "$work/integrations/erp"
cp -r "$package" "$work/integrations/erp/ops"
cp -r integrations/erp/core "$work/integrations/erp/core"
cp -r identity "$work/identity"
find "$work" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

target="$work/$package/procurement.py"
before="$(sha256sum "$target" | cut -d' ' -f1)"

python3 - "$target" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "    if order is None:"
occurrences = source.count(needle)
if occurrences != 1:
    print(
        f"MUTATION-FAILED: the receipt-without-order anchor appears "
        f"{occurrences} time(s) in {path}; the harness must mutate exactly one",
        file=sys.stderr,
    )
    raise SystemExit(1)
mutated = source.replace(
    needle, "    if False:  # MUTANT: the receipt-without-order rule disabled", 1
)
if mutated == source:
    print("MUTATION-FAILED: the replacement produced identical source", file=sys.stderr)
    raise SystemExit(1)
path.write_text(mutated, encoding="utf-8")
PY
mutation_rc=$?
after="$(sha256sum "$target" | cut -d' ' -f1)"

if [ "$mutation_rc" -ne 0 ] || [ "$before" = "$after" ]; then
  fail "the mutation did not land (rc=$mutation_rc, sha unchanged) - the mutant proves nothing"
else
  ok "the mutation landed (sha ${before:0:12} -> ${after:0:12})"

  mutant_out="$(cd "$work" && env PYTHONDONTWRITEBYTECODE=1 python3 -m integrations.erp.ops.negative_control 2>&1)"
  mutant_rc=$?
  printf '%s\n' "$mutant_out" | grep -E '^  (NOT-REFUSED|WRONG-CODE|UNNAMED)' | head -3 | sed 's/^/  /'

  if [ "$mutant_rc" -eq 0 ]; then
    fail "the mutant driver still reported OK with the receipt rule removed"
  else
    ok "the mutant driver went non-zero (rc=$mutant_rc)"
  fi
  case "$mutant_out" in
    *"receipt-without-order"*)
      ok "the removed rule is named as no longer refused (receipt-without-order)" ;;
    *)
      fail "the mutant failed for the wrong reason - receipt-without-order is not named" ;;
  esac
fi

echo
echo "== erp-ops: the check is wired, not a formality =="
if grep -qF 'scripts/discover-checks.sh' scripts/verify.sh; then
  ok "scripts/verify.sh sources scripts/discover-checks.sh, which discovers this check"
else
  fail "scripts/verify.sh does not source scripts/discover-checks.sh, so a new check is inert"
fi
if [ -f scripts/check-denylist.txt ] && grep -qx 'erp-ops' scripts/check-denylist.txt; then
  fail "erp-ops is disabled by name in scripts/check-denylist.txt"
else
  ok "erp-ops is not denylisted"
fi

echo ""
if [ "$failures" -gt 0 ]; then
  echo "$name: FAIL — $failures check(s) failed" >&2
  exit 1
fi
echo "$name: OK — suite, lane check, mutant and wiring all measured"
exit 0
