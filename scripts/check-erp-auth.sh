#!/usr/bin/env bash
# check-erp-auth.sh — the ERP tenant-access lane's gate (issue #653, EPIC #645).
#
# WHY THIS EXISTS
#   The lane ships the authorization layer every ERP surface must call
#   (`integrations/erp/auth/`). `integrations/erp/auth/tests` passes as a suite
#   and the lane's own `cli.py check` measures its declarations, its golden path
#   and its refusal controls — but a suite that no gate NAMES is decorous and
#   inert (GR-12): it runs when someone remembers. This script is the gate that
#   names it. `scripts/discover-checks.sh` (#698) wires it the moment it lands,
#   with no hand-edit to the `checks=()` array in `scripts/verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `integrations/erp/auth/tests` runs. A red test fails the gate.
#   2. THE LANE'S OWN TRI-STATE CHECK. `python3 -m integrations.erp.auth.cli
#      check` measures what a suite alone would not: that the declarations load
#      through one seam each, that the shipped catalogue files match their FROZEN
#      schemas (so the schemas are used, not decorative), that the golden path is
#      DETERMINISTIC (two runs, one digest), and that all 18 refusals of the
#      closed vocabulary are provoked BY NAME. Its 2 (CANNOT-ASSESS) is treated
#      as a failure here: a lane that cannot read its own declarations cannot
#      report OK.
#   3. THE MUTANT — the step that keeps step 2 from being a formality. The
#      package is copied to a SCRATCH tree (with the contracts it consumes), the
#      tenant gate in `scope.py` is disabled (`if request.tenant !=
#      principal.tenant:` -> `if False:`), and the copied driver is run.
#      It MUST go non-zero and name the cross-tenant refusal as NOT occurring.
#      The tenant gate is the lane's central invariant, so a driver that still
#      reports OK with it disabled proves nothing about anything else it reports.
#
# THE MUTANT CARRIES ITS OWN NEGATIVE CONTROL. Before judging the mutant's
#   output, the script asserts the mutation actually LANDED (the file's sha256
#   changed). A harness that reports "the gate caught it" while the edit never
#   applied is a false green -- and it has happened in this repository.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

#
# ---knowledge---
# module_id: scripts.check-erp-auth
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, schema-validation]
# derives_from: null
# owner_sme: security-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#645", "#653", "#698"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

failures=0
ok()   { printf '  OK    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }

for tool in python3 sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { echo "check-erp-auth: CANNOT-ASSESS - $tool is not on PATH" >&2; exit 2; }
done
[ -d integrations/erp/auth ] || { echo "check-erp-auth: CANNOT-ASSESS - integrations/erp/auth is absent" >&2; exit 2; }

echo "== erp-auth: the suite =="
suite_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/erp/auth/tests 2>&1)"
suite_rc=$?
printf '%s\n' "$suite_out" | tail -3 | sed 's/^/  /'
if [ "$suite_rc" -eq 0 ]; then
  ok "the suite passes"
else
  fail "the suite failed (rc=$suite_rc)"
fi

echo
echo "== erp-auth: the lane's own check =="
check_out="$(python3 -m integrations.erp.auth.cli check 2>&1)"
check_rc=$?
printf '%s\n' "$check_out" | grep -E '^  (OK|FAIL)|^integrations' | sed 's/^/  /'
case "$check_rc" in
  0) ok "the lane's own check reports OK" ;;
  2) fail "the lane's own check is CANNOT-ASSESS - it cannot read its own declarations" ;;
  *) fail "the lane's own check failed (rc=$check_rc)" ;;
esac
case "$check_out" in
  *"18 of 18 declared refusal(s) provoked"*) ok "every declared refusal is provoked, by name" ;;
  *) fail "the refusal coverage line is absent - the controls did not all run" ;;
esac

echo
echo "== erp-auth: the mutant — the tenant gate disabled =="
# A unique scratch dir WITHOUT a trailing run of `X`: this repository's docs-lint
# scans for unfinished markers and a `mktemp` X-suffix is one of them, so this
# follows the convention the other gates use — an explicit name built from a
# timestamp and the pid, with `mkdir` refusing loudly rather than silently
# reusing another run's tree.
work="${TMPDIR:-/tmp}/ao-erp-auth-mutant.$(date +%s%N).$$"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-erp-auth: CANNOT-ASSESS - cannot create a scratch dir at $work" >&2
  exit 2
fi
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# A scratch tree the copied driver runs in: the package under test, plus the
# contracts it consumes, because `contract.REPO_ROOT` is derived from the file's
# own location and would otherwise find nothing.
mkdir -p "$work/integrations/erp" "$work/guardrails/policy"
cp -r integrations/erp/auth "$work/integrations/erp/auth"
cp -r integrations/erp/core "$work/integrations/erp/core"
cp -r identity "$work/identity"
cp guardrails/policy/decision.py "$work/guardrails/policy/decision.py"
find "$work" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

target="$work/integrations/erp/auth/scope.py"
before="$(sha256sum "$target" | cut -d' ' -f1)"

python3 - "$target" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "    if request.tenant != principal.tenant:"
if needle not in source:
    print(f"MUTATION-FAILED: the tenant gate anchor is not in {path}", file=sys.stderr)
    raise SystemExit(1)
mutated = source.replace(needle, "    if False:  # MUTANT: the tenant gate disabled", 1)
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

  mutant_out="$(cd "$work" && env PYTHONDONTWRITEBYTECODE=1 python3 -m integrations.erp.auth.cli check 2>&1)"
  mutant_rc=$?
  printf '%s\n' "$mutant_out" | grep -E '^  FAIL' | head -3 | sed 's/^/  /'

  if [ "$mutant_rc" -eq 0 ]; then
    fail "the mutant driver still reported OK with the tenant gate disabled"
  else
    ok "the mutant driver went non-zero (rc=$mutant_rc)"
  fi
  case "$mutant_out" in
    *"the cross-tenant request was not refused"*)
      ok "the mutant was refused BY NAME (the cross-tenant refusal did not occur)" ;;
    *)
      fail "the mutant failed for the wrong reason - the cross-tenant refusal is not named" ;;
  esac
fi

echo
if [ "$failures" -gt 0 ]; then
  echo "check-erp-auth: FAIL — $failures check(s) failed" >&2
  exit 1
fi
echo "check-erp-auth: OK — the suite, the declarations, the golden path and every refusal hold, and the tenant gate is load-bearing"
exit 0
