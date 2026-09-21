#!/usr/bin/env bash
# run-pytest-suites.sh — run every declared pytest suite IN ISOLATION (issue #29).
#
# WHY PER-SUITE ISOLATION
#   The repo's pytest suites individually pass but cannot run in one combined
#   `pytest` invocation: sibling suites collide on conftest.py sys.path
#   bootstrap (`from conftest import cfg`) and on duplicate test-module
#   basenames (test_budget.py / test_events.py / test_config.py ...) across
#   pillar subdirs. A combined run aborts during collection (observed:
#   27 collection errors). So the gate discovers the suites declared in
#   scripts/pytest-suites.txt and runs EACH one alone, aggregating honestly.
#
# EXIT-CODE CONTRACT (guardrails/honesty tri-state, issue #28):
#   0  OK              every declared suite passed
#   1  NOT-OK          a declared suite failed, is missing, or none declared
#   2  CANNOT-ASSESS   a suite exceeded its timeout (no verdict) / no pytest
# A suite that cannot even be collected (pytest rc 2/4/5) is NOT-OK: the
# property "all tests pass" is already violated. An empty or absent run is
# never a pass (no-false-green, AO-GR-4).
#
# Usage:
#   bash scripts/run-pytest-suites.sh            # run all declared suites
#   SUITE_TIMEOUT=300 bash scripts/run-pytest-suites.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

manifest="scripts/pytest-suites.txt"
suite_timeout="${SUITE_TIMEOUT:-300}"
results_json=".verify/test-results.json"
mkdir -p .verify

# --- manifest ---------------------------------------------------------------
if [ ! -f "$manifest" ]; then
  echo "run-pytest-suites: CANNOT-ASSESS — manifest $manifest is missing (no declared suites)" >&2
  exit 2
fi
mapfile -t suites < <(sed -E 's/[[:space:]]+$//' "$manifest" | grep -vE '^\s*(#|$)' || true)
if [ "${#suites[@]}" -eq 0 ]; then
  echo "run-pytest-suites: FAIL — manifest $manifest declares no suites (an empty run is not a pass)" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "run-pytest-suites: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

sha="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# --- run each suite in isolation -------------------------------------------
fail=0
unknown=0
passed=0
records_file=".verify/.suite-records.tsv"
: > "$records_file"

record() { # <suite> <status> <rc> <detail>
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$records_file"
}

# run_one_suite — run one suite in isolation and record its outcome (issue #29).
# `label` is "" for a declared suite and "auto" for an auto-registered one; it
# only prefixes the log line, never the record key, so a suite's record is
# stable however it was reached.
run_one_suite() { # <mod> <label>
  local mod="$1" label="$2" prefix=""
  [ -n "$label" ] && prefix="[$label] "
  local tdir="$mod/tests" out rc summary
  out="$(timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 \
    python3 -m pytest -p no:cacheprovider -q "$tdir" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    passed=$((passed + 1))
    summary="$(printf '%s\n' "$out" | grep -oE '[0-9]+ passed.*' | tail -1)"
    [ -n "$summary" ] || summary="all tests passed"
    echo "  PASS  ${prefix}$mod  ($summary)"
    record "$mod" "OK" "0" "$summary"
  elif [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    # timed out / killed: no verdict was reached — never a pass (issue #28).
    unknown=$((unknown + 1))
    echo "  CANNOT-ASSESS  ${prefix}$mod  (exceeded ${suite_timeout}s timeout — no verdict)" >&2
    record "$mod" "CANNOT-ASSESS" "$rc" "timeout after ${suite_timeout}s"
  else
    fail=$((fail + 1))
    summary="$(printf '%s\n' "$out" | tail -2 | tr '\n' ' ' | cut -c1-180)"
    echo "  FAIL  ${prefix}$mod  (pytest exit $rc: $summary)" >&2
    record "$mod" "FAIL" "$rc" "$summary"
  fi
}

for mod in "${suites[@]}"; do
  if [ ! -d "$mod/tests" ]; then
    echo "  FAIL  $mod  (declared suite missing: $mod/tests)" >&2
    fail=$((fail + 1))
    record "$mod" "FAIL" "1" "missing $mod/tests"
    continue
  fi
  run_one_suite "$mod" ""
done

# --- auto-register undeclared suites (#698) ----------------------------------
# A NEW `<module>/tests/` suite is picked up WITHOUT editing the manifest: the
# scan below finds every committed `*/tests/` directory (via a tracked
# conftest.py or test_*.py) and runs any the manifest does not already declare,
# in isolation, exactly like a declared suite. The manifest stays authoritative
# for ordering and for suites whose tests/ layout is non-standard; the scan is
# additive and labels an auto-registered suite `[auto]` so it is never mistaken
# for a declared one. This ends the hand-edit that used to be required to run a
# new suite (the #559 sole-writer serialization applied to the suite manifest
# too).
auto_suites=()
if command -v git >/dev/null 2>&1; then
  declared_set=""
  for mod in "${suites[@]}"; do declared_set="${declared_set}|$mod|"; done
  while IFS= read -r cf; do
    tdir="$(dirname "$cf")"          # .../tests
    mod="$(dirname "$tdir")"         # .../<module>
    mod="${mod#./}"
    if printf '%s' "$declared_set" | grep -qF "|$mod|"; then
      continue
    fi
    already=0
    for m in "${auto_suites[@]}"; do
      if [ "$m" = "$mod" ]; then already=1; break; fi
    done
    [ "$already" -eq 1 ] && continue
    auto_suites+=("$mod")
  done < <(git ls-files '*/tests/conftest.py' '*/tests/test_*.py' 2>/dev/null | LC_ALL=C sort)

  for mod in "${auto_suites[@]}"; do
    run_one_suite "$mod" "auto"
  done
fi

# --- summary ---------------------------------------------------------------
echo "run-pytest-suites: $passed passed, $fail failed, $unknown no-verdict (${#suites[@]} declared + ${#auto_suites[@]} auto-registered suite(s), sha ${sha:0:12})"

python3 - "$results_json" "$sha" "$passed" "$fail" "$unknown" "${#suites[@]}" "${#auto_suites[@]}" "$records_file" <<'PY'
import json, os, sys

path, sha, passed, failed, unknown, declared, auto_registered, records_file = sys.argv[1:9]
rows = []
with open(records_file, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        suite, status, rc, detail = line.split("\t", 3)
        rows.append({"suite": suite, "status": status, "rc": int(rc), "detail": detail})
doc = {
    "gate": "pytest-suites",
    "sha": sha,
    "declared": int(declared),
    "auto_registered": int(auto_registered),
    "passed": int(passed),
    "failed": int(failed),
    "no_verdict": int(unknown),
    "suites": rows,
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
print(f"evidence: {path}")
PY

rm -f "$records_file"

if [ "$fail" -gt 0 ]; then
  exit 1
fi
if [ "$unknown" -gt 0 ]; then
  exit 2
fi
exit 0
