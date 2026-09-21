#!/usr/bin/env bash
# check-cpapi-spec-drift.sh — the control-plane API contract must match the code.
#
# THE DEFECT THIS EXISTS FOR (issue #816)
# `identity/cpapi/openapi.yaml` is a published contract -- it describes itself as
# "the contract the generated clients in `clients/` are built against". Measured
# 2026-09-15 the spec and the router AGREED (29 routes / 29 paths, zero drift in
# both directions). And **nothing kept them agreeing**: no gate compared them, so
# a route added, renamed or removed in code would invalidate the contract without
# a single check firing. A contract that has drifted is worse than no contract,
# because a consumer trusts it.
#
# WHY IT IS PROVOKED IN BOTH DIRECTIONS
# Drift has two shapes and they fail differently to the consumer:
#   * a route in the CODE but not the SPEC  -> the contract under-documents: a
#     client cannot call a feature that exists;
#   * a path in the SPEC but not the CODE   -> the contract OVER-promises: a
#     client generates against a route that returns 404.
# A comparator that only ever reported one class would look healthy while the
# other silently rotted. So both are planted, and each must be caught BY NAME.
#
# FAIL-CLOSED, SPECIFICALLY
# Zero extracted routes is not "no drift" -- it is extraction failure, and the
# comparator returns CANNOT-ASSESS (2). The gate below additionally refuses a
# comparator that reports agreement while extracting nothing, because that is
# exactly the fail-open shape this repo keeps removing (#739: an unreadable HEAD
# that read back as `healthy`).
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-cpapi-spec-drift.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

COMPARATOR="scripts/cpapi-spec-drift.py"
ROUTER="identity/cpapi/control.py"
SPEC="identity/cpapi/openapi.yaml"

fail=0

command -v python3 >/dev/null 2>&1 || { echo "check-cpapi-spec-drift: CANNOT-ASSESS — python3 not found" >&2; exit 2; }
[ -f "$COMPARATOR" ] || { echo "check-cpapi-spec-drift: FAIL — comparator missing: $COMPARATOR" >&2; exit 1; }
[ -f "$ROUTER" ]     || { echo "check-cpapi-spec-drift: FAIL — router missing: $ROUTER" >&2; exit 1; }
[ -f "$SPEC" ]       || { echo "check-cpapi-spec-drift: FAIL — spec missing: $SPEC" >&2; exit 1; }

echo "== control-plane API contract vs the router =="

# 1. LIVE — the real artifacts must agree.
python3 "$COMPARATOR" "$ROUTER" "$SPEC" >/tmp/csd-live.log 2>&1
live_rc=$?
sed 's/^/  /' /tmp/csd-live.log
case "$live_rc" in
  0) ;;
  2) echo "check-cpapi-spec-drift: CANNOT-ASSESS — the contract could not be compared (see above)" >&2
     exit 2 ;;
  *) echo "check-cpapi-spec-drift: FAIL — the published contract has DRIFTED from the router" >&2
     echo "                  the spec is what consumers generate clients from; fix the spec or the code" >&2
     fail=1 ;;
esac

# 2. PROVOKED — against fixtures in a temp dir, never the real files, so
#    `make verify` cannot mutate the repository it is checking.
fixture="$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="specdrift-"))')" || exit 2
trap 'rm -rf "$fixture"' EXIT

# A minimal, VALID router and spec that agree, so each provocation differs from
# the baseline by exactly the one thing being planted.
cat > "$fixture/router.py" <<'PY'
ROUTES = (
    Route("GET", "/v1/tenants/{tenantId}", "org:read", "tenant.get"),
    Route("POST", "/v1/agents", "agent:create", "agents.register"),
)
PY
cat > "$fixture/spec.yaml" <<'YAML'
openapi: 3.0.3
paths:
  /tenants/{tenantId}:
    get:
      responses:
        "200":
          description: ok
  /agents:
    post:
      responses:
        "200":
          description: ok
YAML

# 2a. The baseline must AGREE -- otherwise every provocation below proves nothing,
#     because a comparator that always reports drift would "catch" them trivially.
out="$(python3 "$COMPARATOR" "$fixture/router.py" "$fixture/spec.yaml" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "check-cpapi-spec-drift: FAIL — the FIXTURE baseline did not agree (rc=$rc); the provocations below would be vacuous" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  fail=1
else
  echo "  OK  the fixture baseline AGREES (so the provocations below are meaningful)"
fi

# 2b. Direction 1: a route in the CODE that the SPEC does not document.
cp "$fixture/router.py" "$fixture/router_extra.py"
cat >> "$fixture/router_extra.py" <<'PY'
# planted: a route the spec does not document
ROUTES += (Route("GET", "/v1/undeclared", "agent:read", "agents.undeclared"),)
PY
out="$(python3 "$COMPARATOR" "$fixture/router_extra.py" "$fixture/spec.yaml" 2>&1)"
rc=$?
if [ "$rc" -ne 1 ]; then
  echo "check-cpapi-spec-drift: FAIL — a route MISSING FROM THE SPEC was not caught (rc=$rc, expected 1)" >&2
  fail=1
elif ! printf '%s' "$out" | grep -q "/undeclared"; then
  echo "check-cpapi-spec-drift: FAIL — drift was caught but the ROUTE was not named" >&2
  fail=1
else
  echo "  OK  a route in the CODE but not the SPEC is caught, by name:"
  printf '%s\n' "$out" | grep "undeclared" | tail -1 | sed 's/^/      /'
fi

# 2c. Direction 2: a path in the SPEC that the CODE does not implement.
cp "$fixture/spec.yaml" "$fixture/spec_extra.yaml"
cat >> "$fixture/spec_extra.yaml" <<'YAML'
  /over-promised:
    get:
      responses:
        "200":
          description: ok
YAML
out="$(python3 "$COMPARATOR" "$fixture/router.py" "$fixture/spec_extra.yaml" 2>&1)"
rc=$?
if [ "$rc" -ne 1 ]; then
  echo "check-cpapi-spec-drift: FAIL — a path the CODE does not implement was not caught (rc=$rc, expected 1)" >&2
  fail=1
elif ! printf '%s' "$out" | grep -q "/over-promised"; then
  echo "check-cpapi-spec-drift: FAIL — over-promise was caught but the PATH was not named" >&2
  fail=1
else
  echo "  OK  a path in the SPEC the CODE does not implement is caught, by name:"
  printf '%s\n' "$out" | grep "over-promised" | tail -1 | sed 's/^/      /'
fi

# 3. FAIL-CLOSED — an unreadable or empty artifact must be CANNOT-ASSESS (2),
#    never a pass. Both halves, because either alone would fail open.
printf 'openapi: 3.0.3\npaths: {}\n' > "$fixture/spec_empty.yaml"
python3 "$COMPARATOR" "$fixture/router.py" "$fixture/spec_empty.yaml" >/dev/null 2>&1
rc=$?
if [ "$rc" -ne 2 ]; then
  echo "check-cpapi-spec-drift: FAIL — a spec with NO PATHS did not read as CANNOT-ASSESS (rc=$rc, expected 2)" >&2
  fail=1
else
  echo "  OK  a spec declaring no paths is CANNOT-ASSESS, not a pass"
fi

printf 'NOT_A_ROUTER = True\n' > "$fixture/router_empty.py"
python3 "$COMPARATOR" "$fixture/router_empty.py" "$fixture/spec.yaml" >/dev/null 2>&1
rc=$?
if [ "$rc" -ne 2 ]; then
  echo "check-cpapi-spec-drift: FAIL — a router yielding ZERO routes did not read as CANNOT-ASSESS (rc=$rc, expected 2)" >&2
  fail=1
else
  echo "  OK  a router yielding zero routes is CANNOT-ASSESS, not agreement"
fi

[ "$fail" -eq 0 ] || exit 1
echo "check-cpapi-spec-drift: OK — the contract matches the code, and drift is caught in BOTH directions"
exit 0
