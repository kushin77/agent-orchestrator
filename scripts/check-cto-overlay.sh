#!/usr/bin/env bash
# check-cto-overlay.sh — CTO overlay gate for `make verify` (issue #147).
#
# The overlay (governance/cto-overlay/) is this repo's four-layer governance
# artifact: executive / engineering / devops / support, each declaring its
# checks and whether it blocks, plus four non-negotiable signals that always
# run. This gate proves the artifact is intact AND that its verdict machinery
# can fail — a governance layer that cannot report failure is a formality
# (GR-12, no-false-green doctrine).
#
# What it does, in order:
#   1. the artifacts exist (schema, config, engine, tests, docs);
#   2. the shipped config passes schema validation on THIS checkout;
#   3. a standard-tier run is green here, otherwise the repo is the problem;
#   4. three independent probes against a scratch checkout prove the engine
#      discriminates: clean -> 0, planted defect -> non-zero naming the file,
#      unreadable config -> 2. These probes are driven by this script's own
#      fixture, so a defect in the engine's built-in self-test cannot hide;
#   5. the engine's built-in negative control (`self-test`) runs its six
#      controls, including the config-contradiction and schema-keyword cases.
#
# It is NOT wired into scripts/verify.sh by this issue: that file is shared,
# and the orchestrator wires the gate after merge.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-cto-overlay.sh
set -uo pipefail

# Every "does this report contain this string?" test below is bash-native (#852).
# `printf '%s' "$out" | grep -qF -- "$s"` is NOT the same test: `grep -q` exits on
# its first match, SIGPIPE then kills the producer, and `set -o pipefail` promotes
# that 141 to the status of the whole pipeline — so a *large* report reports
# ABSENT for text that is PRESENT. Negated, that is a false red; positive, the
# control silently stops controlling and the check fails OPEN.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

overlay_dir="governance/cto-overlay"
engine="$overlay_dir/overlay.py"

fail=0
cannot=0

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cto-overlay: CANNOT-ASSESS — python3 is not on PATH" >&2
  exit 2
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "check-cto-overlay: CANNOT-ASSESS — PyYAML is not importable" >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "check-cto-overlay: CANNOT-ASSESS — git is not on PATH" >&2
  exit 2
fi

# --- 1. artifacts -----------------------------------------------------------
echo "== overlay artifacts =="
for artifact in \
  "$overlay_dir/schema.yaml" \
  "$overlay_dir/config.yaml" \
  "$engine" \
  "$overlay_dir/README.md" \
  "docs/CTO-OVERLAY.md"
do
  if [ -f "$artifact" ]; then
    echo "  OK    $artifact"
  else
    echo "  FAIL  $artifact is missing" >&2
    fail=$((fail + 1))
  fi
done
test_files="$(find "$overlay_dir/tests" -maxdepth 1 -name 'test_*.py' 2>/dev/null | wc -l)"
if [ "$test_files" -gt 0 ]; then
  echo "  OK    $overlay_dir/tests carries $test_files test file(s)"
else
  echo "  FAIL  $overlay_dir/tests carries no test_*.py" >&2
  fail=$((fail + 1))
fi
if [ "$fail" -gt 0 ]; then
  echo "check-cto-overlay: NOT-OK — the overlay is incomplete" >&2
  exit 1
fi

# --- 2. schema validation on this checkout ----------------------------------
echo "== schema validation =="
validate_out="$(python3 "$engine" validate --root "$root" 2>&1)"
validate_rc=$?
case "$validate_rc" in
  0)
    echo "  OK    config validates"
    printf '%s\n' "$validate_out" | sed 's/^/        /'
    ;;
  2)
    echo "  CANNOT-ASSESS  the shipped config did not validate" >&2
    printf '%s\n' "$validate_out" | sed 's/^/        /' >&2
    cannot=$((cannot + 1))
    ;;
  *)
    echo "  FAIL  config validation exited $validate_rc" >&2
    printf '%s\n' "$validate_out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
    ;;
esac

# --- 3. a standard-tier run on this checkout --------------------------------
echo "== standard-tier run on this checkout =="
run_out="$(python3 "$engine" run --root "$root" --tier standard 2>&1)"
run_rc=$?
printf '%s\n' "$run_out" | tail -n 6 | sed 's/^/        /'
case "$run_rc" in
  0)
    echo "  OK    the overlay passes at tier standard"
    ;;
  2)
    echo "  CANNOT-ASSESS  the overlay could not assess this checkout" >&2
    cannot=$((cannot + 1))
    ;;
  *)
    echo "  FAIL  the overlay reports a blocking failure on this checkout" >&2
    printf '%s\n' "$run_out" | grep -E '  (FAIL|INDET)' | sed 's/^/        /' >&2
    fail=$((fail + 1))
    ;;
esac

# --- 4. independent probes: prove the verdict can go red --------------------
echo "== probes (scratch checkout) =="
# A unique scratch dir WITHOUT a trailing run of `X`: docs-lint scans for
# unfinished markers and a `mktemp` X-suffix is one, so this follows the
# convention the other gates use — an explicit /tmp name, with mkdir refusing
# loudly rather than silently reusing another run's tree.
scratch="/tmp/cto-overlay-gate.$(date +%s%N).$$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-cto-overlay: CANNOT-ASSESS — cannot create a scratch dir at $scratch" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

mkdir -p "$scratch/fixture"
python3 "$engine" apply --target "$scratch/fixture" >/dev/null 2>&1
apply_rc=$?
if [ "$apply_rc" -ne 0 ]; then
  echo "  CANNOT-ASSESS  the overlay could not be applied to a scratch target" >&2
  cannot=$((cannot + 1))
fi

fixture="$scratch/fixture"
mkdir -p "$fixture/docs/decision-records" "$fixture/scripts" "$fixture/tests" \
  "$fixture/sample/tests" "$fixture/telemetry" "$fixture/governance/reconcile"
printf '# ADR-0001\n' > "$fixture/docs/decision-records/ADR-0001-scratch.md"
printf '# rules\n' > "$fixture/docs/GOLDEN-RULES.md"
printf '# agents\n' > "$fixture/AGENTS.md"
printf 'verify:\n\t@true\n' > "$fixture/Makefile"
printf '#!/usr/bin/env bash\nset -u\ntrue\n' > "$fixture/scripts/verify.sh"
printf '#!/usr/bin/env bash\nset -u\ntrue\n' > "$fixture/scripts/check-reconcile.sh"
printf 'sample\n' > "$fixture/scripts/pytest-suites.txt"
printf 'def test_sample():\n    assert True\n' > "$fixture/sample/tests/test_sample.py"
printf 'TELEMETRY = True\n' > "$fixture/telemetry/collect.py"
printf 'RECONCILE = True\n' > "$fixture/governance/reconcile/cli.py"
(
  cd "$fixture" || exit 9
  git init -q
  git add -A
  git -c user.email=gate@example.invalid -c user.name=gate commit -q -m fixture
)

clean_out="$(python3 "$engine" run --root "$fixture" --tier standard 2>&1)"
clean_rc=$?
if [ "$clean_rc" -eq 0 ]; then
  echo "  OK    probe 1 (positive control): a clean checkout exits 0"
else
  echo "  FAIL  probe 1 (positive control): a clean checkout exited $clean_rc" >&2
  printf '%s\n' "$clean_out" | tail -n 8 | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

printf '#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n  echo "unterminated\n' > "$fixture/scripts/PROBE-BROKEN.sh"
(
  cd "$fixture" || exit 9
  git add -A
)
broken_out="$(python3 "$engine" run --root "$fixture" --tier standard 2>&1)"
broken_rc=$?
if [ "$broken_rc" -ne 0 ] && contains "$broken_out" 'PROBE-BROKEN.sh'; then
  echo "  OK    probe 2 (negative control): a planted defect exits $broken_rc and names the file"
else
  echo "  FAIL  probe 2 (negative control): planted defect exited $broken_rc" >&2
  printf '%s\n' "$broken_out" | tail -n 8 | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi
rm -f "$fixture/scripts/PROBE-BROKEN.sh"

python3 - "$fixture/governance/cto-overlay/config.yaml" <<'PY'
import sys
import yaml

path = sys.argv[1]
document = yaml.safe_load(open(path, encoding="utf-8"))
document["non_negotiable"] = [name for name in document["non_negotiable"] if name != "secret_scan"]
open(path, "w", encoding="utf-8").write(yaml.safe_dump(document, sort_keys=False))
PY
config_out="$(python3 "$engine" run --root "$fixture" --tier standard 2>&1)"
config_rc=$?
if [ "$config_rc" -eq 2 ] && contains "$config_out" 'CANNOT-ASSESS'; then
  echo "  OK    probe 3 (schema control): a dropped non-negotiable signal exits 2"
else
  echo "  FAIL  probe 3 (schema control): expected exit 2, observed $config_rc" >&2
  printf '%s\n' "$config_out" | tail -n 6 | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

# --- 5. the engine's built-in negative control ------------------------------
echo "== engine self-test =="
self_out="$(python3 "$engine" self-test 2>&1)"
self_rc=$?
printf '%s\n' "$self_out" | sed 's/^/        /'
if [ "$self_rc" -ne 0 ]; then
  echo "  FAIL  the engine's own controls did not all behave" >&2
  fail=$((fail + 1))
fi

# --- summary ----------------------------------------------------------------
if [ "$fail" -gt 0 ]; then
  echo "check-cto-overlay: NOT-OK — $fail finding(s)" >&2
  exit 1
fi
if [ "$cannot" -gt 0 ]; then
  echo "check-cto-overlay: CANNOT-ASSESS — $cannot check(s) could not be run" >&2
  exit 2
fi
echo "check-cto-overlay: OK"
exit 0
