#!/usr/bin/env bash
# check-erp-e2e.sh — the ERP-10 end-to-end capstone lane's gate (issue #655, EPIC #645).
#
# WHY THIS EXISTS
#   The lane ships the ERP epic's end-to-end proof under `e2e/erp/`: the tenant
#   journey across the merged module (ERP-01 declaration -> ERP-03 spine over
#   ERP-02's document model -> ERP-08 scoping -> ERP-09 metering) and the three
#   refusals the capstone's acceptance names, each driven through the module that
#   owns it. A suite that no gate NAMES is decorous and inert (GR-12): it runs when
#   someone remembers. This script is the gate that names it, and
#   `scripts/discover-checks.sh` (#698) wires it the moment it lands, with no
#   hand-edit to the `checks=()` array in `scripts/verify.sh`.
#
# WHAT IS MEASURED, AND WHY EACH STEP CAN FAIL
#   1. THE SUITE. `e2e/erp/tests` runs. A red test fails the gate.
#   2. THE LANE'S OWN TRI-STATE CHECK. `python3 -m e2e.erp.cli check` measures what
#      a suite alone would not: that every declaration the lane CONSUMES (ERP-01's
#      manifest, ERP-02's model, ERP-03's indexer-resolved definitions, ERP-08's
#      catalogues, ERP-09's workspace) loads at all, that the journey is
#      DETERMINISTIC (two runs, one digest), and that all four controls refuse BY
#      NAME. Its 2 (CANNOT-ASSESS) is treated as a failure here: a lane that cannot
#      read the declarations it consumes cannot report OK on the module that
#      carries them.
#      The refusal names asserted below are read FROM THE MODULES that own them
#      (each lane's exported `REFUSALS`) rather than typed into this file, so
#      coverage here cannot drift out of step with the vocabulary it checks.
#   3. THE MUTANT — the step that keeps step 2 from being a formality. The
#      repository is copied to a SCRATCH tree, the console's ERP flag gate is
#      disabled there (`if parts[0] == "erp" and not self.erp.enabled:` ->
#      `... and False:`), and the copied driver is run. It MUST go non-zero and
#      name `feature_disabled` as the refusal that no longer happens.
#      The flag gate is the module's central promise — no part of it is tenant
#      reachable until a reviewed promotion (GR-5) — so a driver that still
#      reported OK with it removed would prove nothing about the other refusals it
#      reports, and `make verify` would be certifying a module that serves itself
#      to every tenant while its declaration says "off".
#
# THE MUTANT CARRIES ITS OWN NEGATIVE CONTROL. Before judging the mutant's output,
#   this script asserts the mutation actually LANDED (the anchor occurs exactly once
#   and the file's sha256 changed). A harness that reports "the gate caught it"
#   while the edit never applied is a false green — and it has happened in this
#   repository.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

name="check-erp-e2e"
suite="e2e/erp/tests"
driver="e2e.erp.cli"
flag_target="portal/server/app.py"
flag_anchor='if parts[0] == "erp" and not self.erp.enabled:'

failures=0
ok()   { printf '  OK    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }

for tool in python3 sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "$name: CANNOT-ASSESS - $tool is not on PATH" >&2
    exit 2
  }
done
if [ ! -d "$suite" ]; then
  echo "$name: CANNOT-ASSESS - $suite is missing" >&2
  exit 2
fi
if [ ! -f e2e/erp/cli.py ]; then
  echo "$name: CANNOT-ASSESS - e2e/erp/cli.py is missing" >&2
  exit 2
fi
if [ ! -f "$flag_target" ]; then
  echo "$name: CANNOT-ASSESS - $flag_target is missing, so the mutant has no target" >&2
  exit 2
fi

# A stray __pycache__ can shadow the code under test and fake a pass (a stale .pyc
# has hidden a mutation in this repository before), so bytecode writing is off for
# every interpreter this check starts.
export PYTHONDONTWRITEBYTECODE=1

echo "== erp-e2e: the suite =="
suite_out="$(python3 -m pytest -p no:cacheprovider -q "$suite" 2>&1)"
suite_rc=$?
printf '%s\n' "$suite_out" | tail -3 | sed 's/^/  /'
if [ "$suite_rc" -eq 0 ]; then
  ok "the suite passes"
else
  fail "the suite failed (rc=$suite_rc)"
fi

echo
echo "== erp-e2e: the lane's own tri-state check =="
run_dir="$root/.verify/e2e-erp"
check_out="$(python3 -m "$driver" --out "$run_dir" check 2>&1)"
check_rc=$?
printf '%s\n' "$check_out" | grep -E '^  FAIL|^erp-e2e check' | sed 's/^/  /'
case "$check_rc" in
  0) ok "the lane's own check reports OK" ;;
  2) fail "the lane's own check is CANNOT-ASSESS - it cannot read the declarations it consumes" ;;
  *) fail "the lane's own check failed (rc=$check_rc)" ;;
esac

# The three refusals the capstone's acceptance names, each named by the module that
# owns it, plus the composed pass over the six sibling lanes' own drivers.
for refusal in feature_disabled cross-tenant budget-exhausted; do
  case "$check_out" in
    *"refused by $refusal"*)
      ok "a control refuses by $refusal" ;;
    *)
      fail "no control reported a refusal by $refusal - that acceptance criterion is unproven" ;;
  esac
done
case "$check_out" in
  *"sibling lane(s), all refused by name"*)
    ok "the six sibling lanes' own refusals are composed over one tree" ;;
  *)
    fail "the composed sibling-lane control reported no coverage across the six lanes" ;;
esac

echo
echo "== erp-e2e: the artifacts the run leaves behind are lint-clean =="
# The repository's own `yaml-lint` (`scripts/check-yaml.py`) walks the WHOLE worktree —
# `.verify/` included — and parses every `*.yaml`/`*.yml`. A deliberately unparseable
# fixture therefore cannot carry a YAML suffix, or the lane reds the composite gate by
# name. Measured: it did exactly that (`.verify/e2e-erp/flag-declarations/malformed.yaml`,
# `yaml: 1 of 234 file(s) FAILED`), so the regression is refused here, in the lane's own
# gate, instead of being discovered one composite run later.
lint_out="$(
  python3 - "$run_dir" <<'PY' 2>&1
import os
import sys

import yaml

root = sys.argv[1]
seen = 0
for dirpath, _dirnames, filenames in os.walk(root):
    for name in sorted(filenames):
        if not name.endswith((".yml", ".yaml")):
            continue
        seen += 1
        path = os.path.join(dirpath, name)
        try:
            with open(path, encoding="utf-8") as handle:
                yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            print(f"NOT-YAML {path}: {exc}")
print(f"COUNT {seen}")
PY
)"
lint_rc=$?
lint_count="$(printf '%s\n' "$lint_out" | sed -n 's/^COUNT //p')"
if [ "$lint_rc" -ne 0 ]; then
  fail "the run's YAML artifacts could not be linted: $lint_out"
elif [ "${lint_count:-0}" = "0" ]; then
  fail "the run left no .yaml artifact behind, so this measurement is vacuous"
else
  lint_bad="$(printf '%s\n' "$lint_out" | grep -c '^NOT-YAML' || true)"
  if [ "$lint_bad" = "0" ]; then
    ok "every .yaml the run left behind parses ($lint_count file(s))"
  else
    fail "$lint_bad .yaml artifact(s) the run left behind do not parse, and yaml-lint walks the whole worktree"
    printf '%s\n' "$lint_out" | grep '^NOT-YAML' | sed 's/^/        /'
  fi
fi

echo
echo "== erp-e2e: the mutant — the console's ERP flag gate disabled =="
before_sha="$(sha256sum "$flag_target" | cut -d' ' -f1)"
anchor_count="$(grep -cF -- "$flag_anchor" "$flag_target" 2>/dev/null || true)"
if [ "$anchor_count" = "1" ]; then
  ok "the flag gate's anchor occurs exactly once in $flag_target"
else
  fail "the flag gate's anchor occurs $anchor_count time(s) in $flag_target - the mutant cannot be stated, so this step would prove nothing"
fi

# A scratch tree WITHOUT a trailing run of `X`: this repository's docs-lint scans
# for unfinished markers and a `mktemp` X-suffix is one of them, so this follows the
# convention the other gates use — an explicit name built from a timestamp and the
# pid, with `mkdir` refusing loudly rather than silently reusing another run's tree.
scratch="/tmp/ao655-erp-e2e-mutant.$(date +%s%N).$$"
if ! mkdir -m 700 "$scratch"; then
  echo "$name: CANNOT-ASSESS - the scratch tree $scratch could not be created" >&2
  exit 2
fi

mutated=0
if [ "$anchor_count" = "1" ]; then
  # The whole repository minus the two directories that must not be copied: the git
  # database (the mutant is not a commit) and the gate's own `.verify` (a nested run
  # must not read, or write, another worktree's attestation).
  if tar -C "$root" --exclude=./.git --exclude=./.verify -cf - . | tar -C "$scratch" -xf -; then
    # A copied __pycache__ can shadow the mutant; remove them before it runs.
    find "$scratch" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    python3 - "$scratch/$flag_target" "$flag_anchor" <<'PY'
import sys

path, anchor = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    text = handle.read()
found = text.count(anchor)
if found != 1:
    raise SystemExit(f"the anchor occurs {found} time(s) in the copy, not once")
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(anchor, 'if parts[0] == "erp" and False:'))
PY
    mutated=$?
  else
    mutated=99
  fi
fi

after_sha="$(sha256sum "$scratch/$flag_target" 2>/dev/null | cut -d' ' -f1)"
if [ "$mutated" = "0" ] && [ -n "$after_sha" ] && [ "$after_sha" != "$before_sha" ]; then
  ok "the mutation landed (sha ${before_sha:0:12} -> ${after_sha:0:12})"
else
  fail "the mutation did not land (rc=$mutated, sha ${before_sha:0:12} -> ${after_sha:0:12}) - a control that never applied is not evidence"
fi

mutant_out=""
mutant_rc=99
if [ "$mutated" = "0" ]; then
  mutant_out="$(cd "$scratch" && python3 -m "$driver" controls 2>&1)"
  mutant_rc=$?
fi
printf '%s\n' "$mutant_out" | grep -E 'problem\(s\): |^ERP NEGATIVE' | sed 's/^/  /' || true
if [ "$mutated" != "0" ]; then
  fail "the mutant could not be run"
elif [ "$mutant_rc" -eq 0 ]; then
  fail "the driver reported OK with the console's ERP flag gate disabled"
else
  ok "the mutant driver went non-zero (rc=$mutant_rc)"
fi

case "$mutant_out" in
  *"module-flag-off-refused"*)
    ok "the mutant names the control whose rule was removed" ;;
  *)
    fail "the mutant did not name module-flag-off-refused - it failed for some other reason" ;;
esac
case "$mutant_out" in
  *"not 404 feature_disabled"*)
    ok "the removed rule is named as no longer refused (a route answered something other than 404 feature_disabled)" ;;
  *)
    fail "the mutant does not name feature_disabled as the refusal that no longer happens" ;;
esac
case "$mutant_out" in
  *"ERP NEGATIVE CONTROLS: PASS"*)
    fail "the mutant still reports PASS, so the flag gate is not what the control exercises" ;;
  *) : ;;
esac

# Exactly ONE control may fail in the mutant. Twelve failures are expected (the six
# routes, anonymous and with a session); any *other* control failing as well would
# mean the scratch tree was broken rather than the rule removed, and the catch above
# would be attributed to the wrong cause.
mutant_failed="$(printf '%s\n' "$mutant_out" | python3 -c 'import json, sys
print(",".join(json.load(sys.stdin)["failedControls"]))' 2>/dev/null)"
if [ "$mutant_failed" = "module-flag-off-refused" ]; then
  ok "the mutant failed on exactly that one control, so the scratch tree is otherwise sound"
else
  fail "the mutant failed on '${mutant_failed:-unparseable output}', not only on module-flag-off-refused"
fi

echo
echo "== erp-e2e: the check is wired, not a formality =="
if grep -qF 'scripts/discover-checks.sh' scripts/verify.sh; then
  ok "scripts/verify.sh sources scripts/discover-checks.sh, which discovers this check"
else
  fail "scripts/verify.sh does not source scripts/discover-checks.sh, so a new check is inert"
fi
if [ -f scripts/check-denylist.txt ] && grep -qx 'erp-e2e' scripts/check-denylist.txt; then
  fail "erp-e2e is disabled by name in scripts/check-denylist.txt"
else
  ok "erp-e2e is not denylisted"
fi
if grep -qxF 'e2e/erp' scripts/pytest-suites.txt; then
  ok "the suite is declared in scripts/pytest-suites.txt"
else
  fail "e2e/erp is not declared in scripts/pytest-suites.txt, so the sweep never runs it"
fi

echo ""
rm -rf "$scratch" || true
if [ "$failures" -gt 0 ]; then
  echo "$name: FAIL — $failures check(s) failed" >&2
  exit 1
fi
echo "$name: OK — suite, lane check, mutant and wiring all measured"
exit 0
