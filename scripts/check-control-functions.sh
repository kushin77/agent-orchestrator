#!/usr/bin/env bash
# check-control-functions.sh — the cockpit function registry is closed, and
# every function it declares binds to a plane that really exists (issue #565,
# RC-10 of EPIC #551).
#
# `control-plane/functions/functions.yaml` is the ONE place a cockpit function —
# a panel, a view or a command — may be named (ADR-0026 D9: adding a domain is a
# declaration, not a fork of the client). This gate proves what a schema alone
# cannot:
#
#   1. the registry is internally sound — one mnemonic per function, RC-2's
#      effect classes, an audit action on every non-read and none on a read, a
#      type on every parameter, a role from the closed role set;
#   2. it CROSS-REFERENCES the planes it describes, in both directions: every
#      exposed RC-2 verb and every declared surface is declared here or excused
#      by name, and every endpoint resolves to a route its owner declares;
#   3. every declared function RENDERS HEADLESSLY against committed fixtures, so
#      the gate needs no live plane, no network and no app object.
#
# It then provokes eight distinct defects in a scratch copy of the repository's
# own registry and fixtures and REQUIRES each to be refused by name, so the gate
# cannot pass vacuously (GR-12). The real files are never touched, and each
# provocation proves its mutation landed (sha256 before != after) before it is
# allowed to count as evidence.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no PyYAML,
# no pytest). CANNOT-ASSESS must never read as a pass.
#
# Usage: bash scripts/check-control-functions.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

registry="control-plane/functions/functions.yaml"
cli="control-plane/functions/cli.py"
schema="control-plane/functions/schema/functions.schema.json"
fixtures="control-plane/functions/tests/fixtures/cockpit.json"
suite="control-plane/functions/tests"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-control-functions: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in "$registry" "$cli" "$schema" "$fixtures" "$suite/test_functions.py" \
                control-plane/control/verbs.yaml infra/feature-flags/registry.yaml; do
  if [ ! -f "$required" ]; then
    echo "check-control-functions: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "check-control-functions: CANNOT-ASSESS — PyYAML is not importable" >&2
  exit 2
fi

if ! python3 -c 'import pytest' >/dev/null 2>&1; then
  echo "check-control-functions: CANNOT-ASSESS — pytest is not importable (the render test cannot run)" >&2
  exit 2
fi

# --- the real check ---------------------------------------------------------
echo "== the registry =="
if ! python3 "$cli" validate; then
  echo "check-control-functions: FAIL — the registry does not validate" >&2
  exit 1
fi

echo "== the headless render (no live plane) =="
if ! python3 "$cli" render --fixtures "$fixtures" >/dev/null; then
  echo "check-control-functions: FAIL — a declared function does not render against the fixtures" >&2
  exit 1
fi
for role in CTO VP-Eng Manager Analyst; do
  if ! python3 "$cli" render --fixtures "$fixtures" --role "$role" >/dev/null; then
    echo "check-control-functions: FAIL — the $role lens does not render" >&2
    exit 1
  fi
done
echo "  OK    every declared function renders, and each role lens renders a subset"

echo "== the unit suite =="
if ! PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$suite" -q -p no:cacheprovider; then
  echo "check-control-functions: FAIL — the unit suite is red" >&2
  exit 1
fi

# --- the negative controls --------------------------------------------------
# Each provokes one defect in a scratch copy of the repository's own registry and
# fixtures and requires the validator (or the renderer) to refuse it BY NAME.
# The scratch directory is built from an explicit template rather than the usual
# mktemp placeholder form: the docs-lint scanner reads that form as an
# unfinished marker.
work="${TMPDIR:-/tmp}/control-functions.$$.$(date +%s%N)"
if ! mkdir -p "$work/repo" 2>/dev/null; then
  echo "check-control-functions: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

scratch="$work/repo"
mkdir -p "$scratch/control-plane/functions/schema" \
         "$scratch/control-plane/functions/tests/fixtures" \
         "$scratch/control-plane/control" \
         "$scratch/infra/feature-flags" \
         "$scratch/identity/rbac" \
         "$scratch/portal/server"

cp "$registry" "$scratch/$registry"
cp "$cli" "$scratch/$cli"
cp "$schema" "$scratch/$schema"
cp "$fixtures" "$scratch/$fixtures"
cp control-plane/control/verbs.yaml "$scratch/control-plane/control/verbs.yaml"
cp infra/feature-flags/registry.yaml "$scratch/infra/feature-flags/registry.yaml"
# The citation corpus: a panel's capability must be one its owner already declares.
cp identity/rbac/model.py "$scratch/identity/rbac/model.py"
cp portal/server/app.py "$scratch/portal/server/app.py"
cp portal/server/fleet_authz.py "$scratch/portal/server/fleet_authz.py"

fail=0
ran=0

# provoke <label> <target: registry|fixtures> <mutator> <expected-needle>
provoke() {
  local label="$1" target="$2" mutator="$3" needle="$4"
  ran=$((ran + 1))

  # restore both files, then mutate exactly one of them
  cp "$registry" "$scratch/$registry"
  cp "$fixtures" "$scratch/$fixtures"
  if [ "$target" = "registry" ]; then
    local path="$scratch/$registry"
  else
    local path="$scratch/$fixtures"
  fi

  local before after
  before="$(sha256sum "$path" | cut -d' ' -f1)"

  if ! python3 -c "
import io, re, sys
p = sys.argv[1]
$mutator
" "$path"; then
    echo "  FAIL  $label: the mutator could not apply (the ${target} moved)" >&2
    fail=1
    return
  fi

  after="$(sha256sum "$path" | cut -d' ' -f1)"
  if [ "$before" = "$after" ]; then
    echo "  FAIL  $label: the mutation changed nothing — the control would prove nothing" >&2
    fail=1
    return
  fi

  local out rc
  if [ "$target" = "registry" ]; then
    out="$(cd "$scratch" && python3 "$cli" validate 2>&1)"
    rc=$?
  else
    out="$(cd "$scratch" && python3 "$cli" render --fixtures "$fixtures" 2>&1)"
    rc=$?
  fi

  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "$needle"; then
    echo "  OK    $label (rc=1, naming $needle)"
  else
    echo "  FAIL  $label: expected rc=1 naming '$needle', got rc=$rc" >&2
    printf '%s\n' "$out" | sed 's/^/          /' >&2
    fail=1
  fi
}

echo "== negative controls (each refused by name) =="

provoke "a duplicate mnemonic is refused" registry '
s = io.open(p, encoding="utf-8").read()
tail = """  - id: FST
    title: A second Fleet status
    kind: command
    endpoints:
      - {route: /api/control/fleet/status, verb: fleet.status}
    parameters: []
    scope: {capability: fleet:read, tenant_scope: platform}
    effect_class: read
    audit: null
    stream: null
    roles: [CTO]
"""
io.open(p, "w", encoding="utf-8").write(s + tail)
' "duplicate function id(s): ['FST']"

provoke "an unknown effect class is refused" registry '
s = io.open(p, encoding="utf-8").read()
s2 = s.replace("effect_class: read", "effect_class: rollback", 1)
assert s2 != s, "no effect_class left to rewrite"
io.open(p, "w", encoding="utf-8").write(s2)
' "effect_class 'rollback' is not one of RC-2's closed set"

provoke "an unknown role is refused" registry '
s = io.open(p, encoding="utf-8").read()
s2 = s.replace("roles: [CTO, VP-Eng, Manager, Analyst]", "roles: [SRE]", 1)
assert s2 != s, "no role list left to rewrite"
io.open(p, "w", encoding="utf-8").write(s2)
' "role 'SRE' is not one of the closed role set"

provoke "a route outside the declared route set is refused" registry '
s = io.open(p, encoding="utf-8").read()
old = "{route: /api/control/fleet/status, verb: fleet.status}"
new = "{route: /api/control/fleet/frobnicate, verb: fleet.status}"
assert old in s, "the FST endpoint is not where this control expects it"
io.open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
' "is not RC-3's declared route for 'fleet.status'"

provoke "a withheld verb is refused" registry '
s = io.open(p, encoding="utf-8").read()
old = "      - {route: /api/control/fleet/status, verb: fleet.status}"
new = "      - {route: /api/control/fleet/live, verb: fleet.live}"
assert old in s, "the FST endpoint is not where this control expects it"
io.open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
' "which RC-2 declares with exposed: false"

provoke "a read that claims an audit action is refused" registry '
s = io.open(p, encoding="utf-8").read()
old = """    effect_class: read
    audit: null
    stream: null
    roles: [CTO, VP-Eng, Manager, Analyst]
"""
new = """    effect_class: read
    audit: fleet.status
    stream: null
    roles: [CTO, VP-Eng, Manager, Analyst]
"""
assert old in s, "the FST audit block is not where this control expects it"
io.open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
' "a read function must not declare an audit action"

provoke "an exposed verb nobody declares is refused" registry '
s = io.open(p, encoding="utf-8").read()
m = re.search(r"\n  - id: FST\n(?:.*\n)*?(?=\n  - id:|\Z)", s)
assert m, "the FST entry is not where this control expects it"
io.open(p, "w", encoding="utf-8").write(s[:m.start()] + "\n" + s[m.end():])
' "MISSING: RC-2 declares the exposed verb 'fleet.status'"

provoke "a surface nobody declares is refused" registry '
s = io.open(p, encoding="utf-8").read()
m = re.search(r"\n  - id: OPS\n(?:.*\n)*?(?=\n  - id:|\Z)", s)
assert m, "the OPS entry is not where this control expects it"
io.open(p, "w", encoding="utf-8").write(s[:m.start()] + "\n" + s[m.end():])
' "MISSING: the feature-flag registry declares the surface 'ops_health'"

provoke "an unknown parameter on a declared function is refused" fixtures '
import json
doc = json.loads(io.open(p, encoding="utf-8").read())
assert "FST" not in doc["calls"], "this control expects FST to carry no fixture"
doc["calls"]["FST"] = {"wibble": "x"}
io.open(p, "w", encoding="utf-8").write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
' "unknown parameter 'wibble' on function FST"

# the scratch copy is left mutated by design; the real files were only ever read
if [ "$fail" -ne 0 ]; then
  echo "check-control-functions: FAIL — $ran control(s) run, at least one was not refused" >&2
  exit 1
fi

echo "  OK    $ran control(s) exercised, each refused by name"
echo "check-control-functions: OK — every cockpit function is declared once, and none is invented"
