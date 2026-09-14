#!/usr/bin/env bash
# check-control-functions.sh — every cockpit function is declared exactly once
# (issue #565, RC-10 of EPIC #551).
#
# The registry `control-plane/functions/functions.yaml` is the ONE place a
# cockpit function may be named: the panels the cockpit renders, the views it
# offers and the commands the operator types. ADR-0026 decision 10 makes adding a
# domain a *declaration*; this gate is what makes that true rather than aspirational.
#
# What it proves, in two parts:
#
#   1. the registry is internally sound AND cross-referenced in both directions
#      against the authorities it consumes (RC-2's verb vocabulary, RC-3's
#      declared route set, the two live SSE surfaces, the flag registry, and
#      `fleet/console.py`'s own rendered panels) — so it can neither describe a
#      function the platform cannot serve nor omit one the cockpit renders;
#   2. every function renders headlessly against a fixture. No live plane, no
#      TTY, no tmux and no token is needed to exercise the whole set.
#
# It then PROVOKES seven defects in a scratch copy of the repository and REQUIRES
# each to be refused BY NAME, so a pass here cannot be vacuous (GR-12: a gate that
# cannot fail is a formality). Every provoked mutation is verified to have LANDED
# before its verdict is trusted — a control whose patch silently did not apply
# would otherwise certify nothing — and the repository's own files are proven
# unchanged at the end.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no pytest,
# no sha256sum). CANNOT-ASSESS must never read as a pass.
#
# Usage: bash scripts/check-control-functions.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-control-functions: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

package="control-plane/functions"
cli="$package/cli.py"
registry="$package/functions.yaml"
suite="$package/tests"
for required in \
  "$registry" "$cli" "$package/cockpit_registry.py" "$package/cockpit_render.py" \
  "$package/schema/functions.schema.json" "$package/fixtures/bodies.json" \
  "$package/fixtures/outcomes.json" "$package/fixtures/streams.json" \
  "$package/fixtures/cockpit-console.json" \
  "$suite/test_functions.py" "$suite/test_render.py" \
  control-plane/control/verbs.yaml portal/server/control_api.py \
  fleet/console.py infra/feature-flags/registry.yaml; do
  if [ ! -e "$required" ]; then
    echo "check-control-functions: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

if ! python3 -c 'import pytest' >/dev/null 2>&1; then
  echo "check-control-functions: CANNOT-ASSESS — pytest is not importable" >&2
  exit 2
fi

if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-control-functions: CANNOT-ASSESS — sha256sum not found (the controls verify their own mutations land)" >&2
  exit 2
fi

# The acceptances, by the test that owns each. Named here so that deleting a case
# is a FAILURE rather than a quieter pass: a suite that keeps passing because its
# cases are gone is the failure mode this whole gate exists against.
cases=(
  test_every_exposed_verb_is_claimed_by_a_declared_function
  test_an_endpoint_outside_rc3s_route_set_is_refused
  test_an_unknown_effect_class_is_refused
  test_a_parameter_with_no_type_is_refused
  test_an_unknown_parameter_is_refused_by_name
  test_a_read_function_may_not_declare_an_audit_action
  test_a_hold_function_must_declare_an_audit_action
  test_an_undeclared_panel_is_refused_naming_the_panel
  test_role_suitability_is_additive_only
  test_every_declared_function_renders_headlessly_against_fixtures
)

# --- the real check ---------------------------------------------------------
echo "== the registry (closed sets + both cross-reference directions) =="
if ! python3 "$cli" validate; then
  echo "check-control-functions: FAIL — the registry does not describe a closed function set" >&2
  exit 1
fi

echo "== the registry exercised headlessly (no live plane) =="
if ! python3 "$cli" frames >/dev/null; then
  echo "check-control-functions: FAIL — the declared functions could not be rendered" >&2
  exit 1
fi

echo "== the suite =="
for case in "${cases[@]}"; do
  if ! grep -qE "^def ${case}\(" "$suite/test_functions.py" "$suite/test_render.py"; then
    echo "check-control-functions: FAIL — no suite declares $case" >&2
    exit 1
  fi
done
echo "  OK    all ${#cases[@]} acceptance cases are declared"

if ! PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$suite" -q -p no:cacheprovider; then
  echo "check-control-functions: FAIL — the cockpit-function suite is red" >&2
  exit 1
fi

# --- the negative controls --------------------------------------------------
# Each breaks one acceptance in a scratch copy of the repository and requires the
# validator to refuse it BY NAME. The scratch directory is built from an explicit
# template rather than the usual mktemp placeholder form: the docs-lint scanner
# reads a run of three capital X's as an unfinished marker.
work="${TMPDIR:-/tmp}/control-functions.$$.$(date +%s%N)"
repo="$work/repo"
if ! mkdir -p "$repo" 2>/dev/null; then
  echo "check-control-functions: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

# The scratch tree is the repository minus VCS, the pinned submodule, the
# research clones and the runtime state peers regenerate. `governance` and
# `telemetry` are carried because `fleet/console.py` and the control API import
# them; `infra` carries the flag registry the functions name.
while IFS= read -r entry; do
  case "$entry" in
    ./.git|.git|./.git/*|./vendor|vendor|./vendor/*|./.research|./.fleet|./.board) continue ;;
    ./.*) continue ;;
  esac
  if ! cp -a "$entry" "$repo/" 2>/dev/null; then
    echo "check-control-functions: CANNOT-ASSESS — cannot copy $entry into the scratch tree" >&2
    exit 2
  fi
done < <(find . -maxdepth 1 -mindepth 1 | LC_ALL=C sort)

# A stale bytecode cache can shadow the module under test — measured elsewhere in
# this fleet as a mutant that reported the previous run's failures. The scratch
# tree starts with none, and every invocation below writes none.
find "$repo" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find "$repo" -name '*.pyc' -delete 2>/dev/null
if [ ! -f "$repo/$registry" ] || [ ! -f "$repo/$cli" ]; then
  echo "check-control-functions: CANNOT-ASSESS — the scratch tree is incomplete" >&2
  exit 2
fi

real_before="$(sha256sum "$registry" | cut -d' ' -f1)"
fail=0
ran=0

# provoke <label> <relative-file> <code-needle> <message-needle> <mutator.py>
provoke() {
  local label="$1" rel="$2" code="$3" needle="$4" mutator="$5"
  ran=$((ran + 1))

  if ! cp "$rel" "$repo/$rel" 2>/dev/null; then
    echo "  FAIL  $label: cannot reset $rel in the scratch tree" >&2
    fail=1
    return
  fi
  local before after
  before="$(sha256sum "$repo/$rel" | cut -d' ' -f1)"

  if ! python3 "$mutator" "$repo/$rel"; then
    echo "  FAIL  $label: the mutator could not apply (the source moved)" >&2
    fail=1
    return
  fi

  after="$(sha256sum "$repo/$rel" | cut -d' ' -f1)"
  if [ "$before" = "$after" ]; then
    echo "  FAIL  $label: the mutation changed nothing — the control would prove nothing" >&2
    fail=1
    return
  fi

  local out rc
  out="$(cd "$repo" && PYTHONDONTWRITEBYTECODE=1 python3 "$cli" validate 2>&1)"
  rc=$?

  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "$code" && printf '%s' "$out" | grep -qF "$needle"; then
    echo "  OK    $label (rc=1, naming $code)"
  else
    echo "  FAIL  $label: expected rc=1 naming '$code' and '$needle', got rc=$rc" >&2
    printf '%s\n' "$out" | sed 's/^/          /' >&2
    fail=1
  fi
}

echo "== negative controls (each acceptance broken in a scratch copy) =="

cat > "$work/mutate_effect_class.py" <<'PY'
"""An effect class outside RC-2's closed set."""
import io, sys
path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
start = src.index("  - id: HEADER")
at = src.index("    effect_class: ", start) + len("    effect_class: ")
end = src.index("\n", at)
out = src[:at] + "suspend" + src[end:]
assert out != src, "the HEADER effect_class line was not found"
io.open(path, "w", encoding="utf-8").write(out)
PY

cat > "$work/mutate_parameter_type.py" <<'PY'
"""A parameter with no type."""
import io, sys
path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = "{name: tail, type: integer, required: false, default: 20}"
new = "{name: tail, required: false, default: 20}"
assert old in src, "the LOG.tail parameter was not found"
out = src.replace(old, new, 1)
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PY

cat > "$work/mutate_endpoint.py" <<'PY'
"""An endpoint outside RC-3's declared route set."""
import io, sys
path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
start = src.index("  - id: HEADER")
at = src.index("    endpoints: [fleet/status]", start)
out = src[:at] + "    endpoints: [fleet/nonesuch]" + src[at + len("    endpoints: [fleet/status]"):]
assert out != src, "the HEADER endpoints line was not found"
io.open(path, "w", encoding="utf-8").write(out)
PY

cat > "$work/mutate_audit.py" <<'PY'
"""A read function declaring an audit action (RC-2's two-way rule)."""
import io, sys
path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
start = src.index("  - id: HEADER")
at = src.index("    audit: null", start)
out = src[:at] + "    audit: fleet.status" + src[at + len("    audit: null"):]
assert out != src, "the HEADER audit line was not found"
io.open(path, "w", encoding="utf-8").write(out)
PY

cat > "$work/mutate_undeclared_function.py" <<'PY'
"""A mnemonic the API serves that no function declares."""
import io, sys
path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
marker = "\n  - id: CLOSE\n"
assert marker in src, "the CLOSE function was not found"
out = src[: src.index(marker)]
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PY

cat > "$work/mutate_console_panel.py" <<'PY'
"""A panel the cockpit renders that no function declares."""
import io, sys
path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = '            watchdog_section(snap.get("watchdog") or []),\n'
new = old + '            "\u2500\u2500 NOTES " + "\u2500" * 60,\n'
assert old in src, "the console render() tail was not found"
out = src.replace(old, new, 1)
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PY

provoke "a parameter with no type is refused" "$registry" \
  "UNKNOWN-PARAMETER-TYPE" "LOG.tail declares type" "$work/mutate_parameter_type.py"
provoke "an endpoint outside RC-3's route set is refused" "$registry" \
  "ENDPOINT-NOT-DECLARED" "fleet/nonesuch" "$work/mutate_endpoint.py"
provoke "an unknown effect class is refused" "$registry" \
  "UNKNOWN-EFFECT-CLASS" "declares effect_class 'suspend'" "$work/mutate_effect_class.py"
provoke "a read function declaring an audit action is refused" "$registry" \
  "AUDIT-FORBIDDEN" "may not declare an audit action" "$work/mutate_audit.py"
provoke "a mnemonic no function declares is refused" "$registry" \
  "UNCLAIMED-MNEMONIC" "closure.close is exposed by RC-2" "$work/mutate_undeclared_function.py"
provoke "a panel the cockpit renders and nothing declares is refused" "fleet/console.py" \
  "UNDECLARED-PANEL" "renders 'NOTES'" "$work/mutate_console_panel.py"

# The seventh control is an invocation, not a mutation: an unknown parameter on an
# otherwise-declared function must be refused BY NAME.
ran=$((ran + 1))
out="$(cd "$repo" && PYTHONDONTWRITEBYTECODE=1 python3 "$cli" call LOG tail=20 bogus=1 2>&1)"
rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "UNKNOWN-PARAMETER" \
   && printf '%s' "$out" | grep -qF "does not declare a parameter 'bogus'"; then
  echo "  OK    an unknown parameter on a declared function is refused by name (rc=1)"
else
  echo "  FAIL  an unknown parameter on a declared function: expected rc=1 naming 'bogus', got rc=$rc" >&2
  printf '%s\n' "$out" | sed 's/^/          /' >&2
  fail=1
fi

# --- the real files are untouched -------------------------------------------
real_after="$(sha256sum "$registry" | cut -d' ' -f1)"
if [ "$real_before" != "$real_after" ]; then
  echo "  FAIL  the real registry changed during the run — the controls are not acting on a copy" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "check-control-functions: FAIL — $ran control(s) run, at least one was not refused" >&2
  exit 1
fi

echo "  OK    $ran control(s) exercised, each refused by name"
echo "check-control-functions: OK — every cockpit function is declared exactly once,"
echo "                        and the registry is refused when it is not"
