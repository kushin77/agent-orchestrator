#!/usr/bin/env bash
# check-erp-api.sh — the gate for the ERP REST surface (issue #651, EPIC #645).
#
# WHY THIS EXISTS
#   `integrations/erp/api/` ships the module's REST contract: an OpenAPI document
#   emitted from ERP-02's schemas, a transport-free surface over the document
#   store, and an authorization path that is *delegated* to ERP-08. The suite
#   passes and `cli.py check` measures the lane's own invariants — but a suite no
#   gate NAMES runs only when someone remembers (GR-12). This script is the gate
#   that names it, and `scripts/discover-checks.sh` (issue #698) wires it the
#   moment it lands, with no hand-edit to the `checks=()` array in `verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `integrations/erp/api/tests` runs. A red test fails the gate.
#   2. THE LANE'S OWN TRI-STATE CHECK. `python3 -m integrations.erp.api.cli check`
#      measures what a suite alone would not: that the committed `openapi.json` is
#      a fresh emission byte for byte, that every component still matches the
#      ERP-02 schema it came from, that every `$ref` resolves, that the field rules
#      govern fields the family schemas declare, that the role map covers every
#      kind the model has, that the offline corpus validates, that the health read
#      is honest, and that the golden path is deterministic. Its 2
#      (CANNOT-ASSESS) is treated as a failure here: a lane that cannot read its
#      model cannot report OK.
#   3. THE ARTIFACT, INDEPENDENTLY OF THE CHECKER. The committed `openapi.json` is
#      compared to a fresh emission with `cmp`. If step 2's checker were weakened
#      to always pass, this step would still catch a hand-edited document.
#   4. THE MUTANTS — the steps that keep 2 and 3 from being formalities. The tree
#      is copied to scratch twice, the copy is FIRST shown to drive the controls
#      green (so a failure later cannot be blamed on a broken copy), and then:
#        (a) the model's validator is removed, and the driver MUST go red naming
#            `schema_violation` — i.e. the surface really does depend on ERP-02
#            rather than on a second opinion of its own;
#        (b) the authorizer is replaced with one that always allows, and the driver
#            MUST go red naming `authorization-denied` — i.e. the surface really
#            does delegate, and there is no path that skips the decision.
#      A driver that still reports OK with its validation or its authorization
#      neutered proves nothing about the refusals it claims to run.
#   5. THE WIRING. The check asserts its own wiring: `scripts/verify.sh` sources
#      the discovery helper, `erp-api` is not disabled by name, and — the
#      structural half of "no back door" — `surface.py` calls the authorization
#      layer from exactly one place.
#
# Offline, deterministic, stdlib only: no network, no containers, no vendor
# submodule. Scratch paths use `tempfile.mkdtemp` (never a shell template) and are
# removed on exit.
#
# Exit-code contract (repository convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-erp-api.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

name="check-erp-api"
suite="integrations/erp/api/tests"
package="integrations/erp/api"

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
if [ ! -f "$package/openapi.json" ]; then
  echo "$name: FAIL — $package/openapi.json is not committed (the emitted document is missing)" >&2
  exit 1
fi

# A stray __pycache__ can shadow the code under test and fake a pass (a stale
# .pyc has hidden a mutation in this repository before), so bytecode writing is
# off for every interpreter this check starts.
export PYTHONDONTWRITEBYTECODE=1

scratch="$(python3 -c 'import tempfile,sys; sys.stdout.write(tempfile.mkdtemp(prefix="ao-erp-api-"))')"
if [ -z "$scratch" ] || [ ! -d "$scratch" ]; then
  echo "$name: CANNOT-ASSESS — no scratch directory could be created" >&2
  exit 2
fi
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT INT TERM

fail=0
report() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

echo "== 1. the REST surface suite =="
if python3 -m pytest -p no:cacheprovider -q "$suite"; then
  echo "  ok    $suite passed"
else
  report "$suite did not pass"
fi

echo "== 2. the lane's tri-state check (document, artifact, declarations, controls) =="
cli_log="$scratch/cli-check.log"
python3 -m integrations.erp.api.cli check >"$cli_log" 2>&1
cli_rc=$?
case "$cli_rc" in
  0)
    grep -E '^  OK|^erp-api check: OK' "$cli_log" | sed 's/^/  /'
    ;;
  2)
    report "the lane cannot assess its own document (CANNOT-ASSESS)"
    sed 's/^/        /' "$cli_log" >&2
    ;;
  *)
    report "integrations/erp/api/cli.py check reported NOT-OK"
    sed 's/^/        /' "$cli_log" >&2
    ;;
esac

echo "== 3. the committed document is a fresh emission (independently of the checker) =="
if python3 "$package/cli.py" emit >"$scratch/fresh.json" 2>"$scratch/emit.err"; then
  if cmp -s "$scratch/fresh.json" "$package/openapi.json"; then
    echo "  ok    $package/openapi.json is byte-identical to a fresh emission ($(wc -c < "$package/openapi.json") bytes)"
  else
    report "$package/openapi.json is stale or hand-edited (it differs from a fresh emission)"
  fi
else
  report "the document could not be emitted"
  sed 's/^/        /' "$scratch/emit.err" >&2
fi

echo "== 4. the mutants: a neutered validator and a neutered authorizer must both turn the driver red =="
# A copied tree must be able to import and drive the controls BEFORE the mutation
# is applied, or a failure afterwards could be blamed on the copy rather than on
# the mutation. The copy carries the packages the surface reaches: the ERP module,
# the identity contracts it consumes, and the guardrails decision vocabulary.
mutant_root="$scratch/mutant"
mkdir -p "$mutant_root/integrations" || report "could not lay out the mutant tree"
cp -r integrations/erp "$mutant_root/integrations/erp" || report "could not copy integrations/erp"
cp -r identity "$mutant_root/identity" || report "could not copy identity/"
cp -r guardrails "$mutant_root/guardrails" || report "could not copy guardrails/"
find "$mutant_root" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

baseline_log="$scratch/mutant-baseline.log"
if ( cd "$mutant_root" && python3 -m integrations.erp.api.negative_control ) >"$baseline_log" 2>&1; then
  echo "  ok    the copied tree drives every control green before any mutation"
else
  report "the copied tree could not drive its own controls (the copy is broken, so the mutants prove nothing)"
  tail -n 8 "$baseline_log" | sed 's/^/        /' >&2
fi

# (a) the model's validator removed. The surface must depend on ERP-02, not on a
#     second opinion of it: with nothing validating, `schema_violation` cannot be
#     provoked, and the driver has to say so.
validator_mutant="$scratch/mutant-validator"
cp -r "$mutant_root" "$validator_mutant" || report "could not copy the mutant tree"
cat >>"$validator_mutant/integrations/erp/core/validators.py" <<'PY'

# MUTANT (check-erp-api.sh): the model's validator removed, so the driver must notice.
DocumentModel.validate_document = lambda self, kind, document: dict(document)
PY
if ! grep -q 'MUTANT (check-erp-api.sh): the model' "$validator_mutant/integrations/erp/core/validators.py"; then
  report "the validator mutation did not land"
fi
validator_log="$scratch/mutant-validator.log"
if ( cd "$validator_mutant" && python3 -m integrations.erp.api.negative_control ) >"$validator_log" 2>&1; then
  report "the validator mutant PASSED — the driver cannot fail, so it proves nothing"
else
  if grep -F 'was NOT refused' "$validator_log" >/dev/null 2>&1 &&
     grep -F 'model-schema_violation' "$validator_log" >/dev/null 2>&1; then
    echo "  ok    the neutered validator turned the driver red, naming model-schema_violation"
  else
    report "the validator mutant failed for the wrong reason (schema_violation was not named)"
    tail -n 6 "$validator_log" | sed 's/^/        /' >&2
  fi
fi

# (b) the authorizer replaced by one that always allows. This is the "no back
#     door" mutant: if the surface stopped delegating, the refusals it claims to
#     produce would evaporate — and the driver must notice.
auth_mutant="$scratch/mutant-auth"
cp -r "$mutant_root" "$auth_mutant" || report "could not copy the mutant tree"
cat >>"$auth_mutant/integrations/erp/auth/scope.py" <<'PY'

# MUTANT (check-erp-api.sh): the decision removed, so every request is allowed.
def authorize(role_map, policy_set, store, principal, request):
    """MUTANT: the authorization decision replaced by an unconditional allow."""
    return Decision(allowed=True, reason="mutant")
PY
if ! grep -q 'MUTANT (check-erp-api.sh): the decision' "$auth_mutant/integrations/erp/auth/scope.py"; then
  report "the authorization mutation did not land"
fi
auth_log="$scratch/mutant-auth.log"
if ( cd "$auth_mutant" && python3 -m integrations.erp.api.negative_control ) >"$auth_log" 2>&1; then
  report "the authorization mutant PASSED — the driver cannot see a removed decision, so it proves nothing"
else
  if grep -F 'was NOT refused' "$auth_log" >/dev/null 2>&1 &&
     grep -F 'authorization-denied' "$auth_log" >/dev/null 2>&1; then
    echo "  ok    the always-allow authorizer turned the driver red, naming authorization-denied"
  else
    report "the authorization mutant failed for the wrong reason (authorization-denied was not named)"
    tail -n 6 "$auth_log" | sed 's/^/        /' >&2
  fi
fi

echo "== 5. the check is wired, and the surface has one way to a decision =="
if grep -qF 'scripts/discover-checks.sh' scripts/verify.sh; then
  echo "  ok    scripts/verify.sh sources scripts/discover-checks.sh, which discovers this check"
else
  report "scripts/verify.sh does not source scripts/discover-checks.sh, so a new check is inert"
fi
if [ -f scripts/check-denylist.txt ] && grep -qx 'erp-api' scripts/check-denylist.txt; then
  report "erp-api is disabled by name in scripts/check-denylist.txt"
else
  echo "  ok    erp-api is not denylisted"
fi
# The structural half of "no back door": one call site into the authorization
# layer. Counted in the source rather than trusted, so adding a second path to a
# decision fails here.
call_sites="$(grep -c 'auth_scope\.authorize(' "$package/surface.py")"
if [ "$call_sites" -eq 1 ]; then
  echo "  ok    $package/surface.py reaches the authorization layer from exactly one call site"
else
  report "$package/surface.py has $call_sites authorization call site(s); exactly one is what makes 'no back door' a property"
fi

echo ""
if [ "$fail" -ne 0 ]; then
  echo "$name: NOT-OK — $fail problem(s)" >&2
  exit 1
fi
echo "$name: OK — suite, tri-state check, fresh emission, both mutants and the wiring all measured"
exit 0
