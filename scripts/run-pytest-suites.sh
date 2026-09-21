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
# WHAT A RED SUITE PUBLISHES (issue #1661)
#   The composite used to print a failing suite's transcript PATH and nothing
#   else -- and the transcript lived in the ephemeral build workspace, which the
#   build destroys. So the failing test was UNNAMEABLE from the venue's own
#   evidence, the red could not be attributed to a lane, and 21 verified lanes
#   could not land on it. A red suite now publishes INLINE, and BOUNDED, what
#   this run retained for it: the `FAILED <test>` ids pytest printed (at most
#   `PYTEST_FAILED_IDS`, default 20) and the last `PYTEST_TAIL_LINES` lines of
#   `.verify/pytest-suites/<suite>.log` (default 25). The transcript key is the
#   same `<suite with / -> _>.log` convention `scripts/check-pytest-suites.sh`
#   retains, so one suite has one transcript whichever runner reached it, and
#   `.verify/` is git-ignored so retaining it cannot dirty the tree judged.
#   A GREEN suite publishes none of that: the echo IS the finding, so printing
#   it on a pass would bury the evidence it exists to surface.
#
# EXIT-CODE CONTRACT (guardrails/honesty tri-state, issue #28):
#   0  OK              every declared suite passed at least one test
#   1  NOT-OK          a declared suite failed, is missing, or none declared
#   2  CANNOT-ASSESS   a suite exceeded its timeout (no verdict) / no pytest /
#                      a suite whose every test SKIPPED. pytest exits 0 there,
#                      so without this arm "nothing was assessed" would read as
#                      PASS -- the false green this contract forbids.
# A suite that cannot even be collected (pytest rc 2/4/5) is NOT-OK: the
# property "all tests pass" is already violated. An empty or absent run is
# never a pass (no-false-green, AO-GR-4).
#
# Usage:
#   bash scripts/run-pytest-suites.sh            # run all declared suites
#   SUITE_TIMEOUT=300 bash scripts/run-pytest-suites.sh
#   PYTEST_TAIL_LINES=80 bash scripts/run-pytest-suites.sh
#   bash scripts/run-pytest-suites.sh --self-test  # prove the echo (no manifest)
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

manifest="scripts/pytest-suites.txt"
suite_timeout="${SUITE_TIMEOUT:-300}"
results_json=".verify/test-results.json"
mkdir -p .verify

# --- the retained transcript, and the bound on what a red one publishes ------
# (issue #1661 — see "WHAT A RED SUITE PUBLISHES" above.)
suite_log_dir=".verify/pytest-suites"
tail_lines="${PYTEST_TAIL_LINES:-25}"
failed_ids="${PYTEST_FAILED_IDS:-20}"
if ! mkdir -p "$suite_log_dir" 2>/dev/null; then
  echo "run-pytest-suites: CANNOT-ASSESS — cannot create the transcript directory $suite_log_dir" >&2
  exit 2
fi

suite_log() { # <suite> -> the transcript this run retains for it
  printf '%s/%s.log\n' "$suite_log_dir" "$(printf '%s' "$1" | tr '/' '_')"
}

# emit_suite_tail — publish WHY a suite is red, from the transcript this run
# retained for it (#1661). Before this, the composite printed the transcript
# PATH and nothing else, and that path was destroyed with the build workspace:
# the failing test was unnameable from the venue's own evidence, so an
# unattributable red could not be fixed by any lane. The echo is BOUNDED on both
# axes — at most $failed_ids id lines, at most $tail_lines transcript lines,
# every line truncated — so neither a huge transcript nor a hostile one can
# flood the gate it reports into. A green suite never reaches this function.
emit_suite_tail() { # <suite> <log>
  local suite="$1" log="$2"
  local ids
  if [ ! -s "$log" ]; then
    printf '    ---- %s: no transcript retained at %s ----\n' "$suite" "$log" >&2
    return 0
  fi
  ids="$(grep -m "$failed_ids" -E '^(FAILED|ERROR) ' "$log" 2>/dev/null | cut -c1-240 || true)"
  if [ -n "$ids" ]; then
    printf '    ---- %s: failing test id(s), first %s ----\n' "$suite" "$failed_ids" >&2
    printf '%s\n' "$ids" >&2
  else
    printf '    ---- %s: pytest printed no FAILED <test> line; the transcript tail follows ----\n' \
      "$suite" >&2
  fi
  printf '    ---- %s: last %s line(s) of %s ----\n' "$suite" "$tail_lines" "$log" >&2
  tail -n "$tail_lines" "$log" 2>/dev/null | cut -c1-240 >&2
  printf '    ---- end %s ----\n' "$suite" >&2
}

# --- self-test (issue #1661) -------------------------------------------------
# WHAT IT PROVES, on a scratch tree whose verdict is known rather than guessed:
#   A1  a declared RED suite makes the run rc 1 — the run is not silently green;
#   A2  the run NAMES the failing test (`FAILED ...::<name>`) on its own output,
#       which is the whole of what #1661 asked for;
#   A3  the transcript is retained at the key this runner and
#       `scripts/check-pytest-suites.sh` share, and carries the same test id, so
#       the published line and the retained log cannot disagree;
#   A4  the echo TRACKS the bound — a 500-failure transcript is echoed within
#       `PYTEST_TAIL_LINES`, and a smaller bound echoes fewer lines from the
#       SAME transcript, so the bound is load-bearing and not a coincidence of a
#       short log (a bound that is never reached proves nothing);
#   A5  a GREEN suite is QUIET: no id block, no transcript tail for it;
#   A6  a suite whose every test SKIPPED is NOT a pass — pytest exits 0 there,
#       so this is the arm that otherwise lets "nothing ran" read as PASS;
#   A7  VACUITY: a run in which nothing failed prints no echo at all.
# The provocation drives a COPY of THIS script through its ORDINARY path, so
# what is proven is the code that shipped, never a reimplementation of it.
#
# EXIT: 0 every arm held; 1 an arm failed; 2 the self-test could not be built.
self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
selftest_scratch=""
cleanup_selftest() { [ -n "$selftest_scratch" ] && rm -rf "$selftest_scratch"; return 0; }

run_self_test() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "run-pytest-suites --self-test: CANNOT-ASSESS — python3 not found" >&2
    return 2
  fi
  local sixx
  sixx="$(printf 'X%.0s' 1 2 3 4 5 6)"
  selftest_scratch="$(mktemp -d "/tmp/ao1661-pytest-tail.${sixx}")"
  if [ -z "$selftest_scratch" ] || [ ! -d "$selftest_scratch" ]; then
    echo "run-pytest-suites --self-test: CANNOT-ASSESS — no scratch dir under /tmp" >&2
    return 2
  fi
  local work="$selftest_scratch"
  if ! mkdir -p "$work/scripts" "$work/demo_red/tests" "$work/demo_loud/tests" \
      "$work/demo_green/tests" "$work/demo_skipped/tests"; then
    echo "run-pytest-suites --self-test: CANNOT-ASSESS — cannot build the scratch tree" >&2
    return 2
  fi
  if ! cp "$self" "$work/scripts/run-pytest-suites.sh"; then
    echo "run-pytest-suites --self-test: CANNOT-ASSESS — cannot stage the runner" >&2
    return 2
  fi

  cat > "$work/demo_red/tests/test_red.py" <<'PYRED'
def test_the_deliberate_red():
    assert False, "the deliberate red this self-test provokes"
PYRED
  # 500 failures: the FAILED ids and the transcript TAIL are both far longer
  # than the bounds, so the bound has to be doing the truncating.
  cat > "$work/demo_loud/tests/test_loud.py" <<'PYLOUD'
import pytest


@pytest.mark.parametrize("n", list(range(500)))
def test_the_loud_red(n):
    assert n < 0, f"the loud deliberate red #{n}"
PYLOUD
  cat > "$work/demo_green/tests/test_green.py" <<'PYGREEN'
def test_the_green_one():
    assert True
PYGREEN
  cat > "$work/demo_skipped/tests/test_skipped.py" <<'PYSKIP'
import pytest


@pytest.mark.skip(reason="this suite assesses nothing")
def test_the_skipped_one():
    assert False
PYSKIP

  local arms=0 failed_arms=0
  ok() { arms=$((arms + 1)); printf '  OK    %s\n' "$1"; }
  bad_arm() { arms=$((arms + 1)); failed_arms=$((failed_arms + 1)); printf '  FAIL  %s\n' "$1" >&2; }

  echo "== run-pytest-suites --self-test (issue #1661) =="
  echo "-- the scratch tree: demo_red (1 failure), demo_loud (500 failures), demo_green, demo_skipped"

  # --- arms A1..A5: one run over red + loud + green --------------------------
  printf 'demo_red\ndemo_loud\ndemo_green\n' > "$work/scripts/pytest-suites.txt"
  local out rc redlog loudlog
  out="$(bash "$work/scripts/run-pytest-suites.sh" 2>&1)"
  rc=$?
  redlog="$work/.verify/pytest-suites/demo_red.log"
  loudlog="$work/.verify/pytest-suites/demo_loud.log"

  if [ "$rc" -eq 1 ]; then
    ok "A1 a red suite makes the run rc 1"
  else
    bad_arm "A1 a red suite makes the run rc 1 (measured rc=$rc)"
  fi

  if printf '%s\n' "$out" | grep -qE 'FAILED .*::test_the_deliberate_red'; then
    ok "A2 the run names the failing test id on stdout"
  else
    bad_arm "A2 the run names the failing test id on stdout (no 'FAILED ...::test_the_deliberate_red')"
  fi

  if [ -s "$redlog" ] && grep -q 'test_the_deliberate_red' "$redlog"; then
    ok "A3 the transcript is retained at .verify/pytest-suites/demo_red.log and carries the same id"
  else
    bad_arm "A3 the transcript is retained at .verify/pytest-suites/demo_red.log (missing or empty)"
  fi

  local loud_block loud_lines loud_log_lines loud_ids bound2 block2 lines2
  loud_block="$(printf '%s\n' "$out" | awk '/^    ---- demo_loud: last /{f=1;next} /^    ---- end demo_loud ----/{f=0} f')"
  loud_lines="$(printf '%s\n' "$loud_block" | wc -l | tr -d ' ')"
  loud_log_lines="$(wc -l < "$loudlog" 2>/dev/null | tr -d ' ')"
  loud_ids="$(printf '%s\n' "$out" | awk '/^    ---- demo_loud: failing test id\(s\)/{f=1;next} /^    ---- demo_loud: last /{f=0} f' | grep -c '^FAILED ' || true)"
  if [ "${loud_lines:-0}" -le 25 ] && [ "${loud_lines:-0}" -gt 0 ] &&
     [ "${loud_log_lines:-0}" -ge 500 ] && [ "${loud_ids:-0}" -eq 20 ]; then
    ok "A4 the echo is bounded: ${loud_ids} id(s) and ${loud_lines} tail line(s) from a ${loud_log_lines}-line transcript (bounds 20 / 25)"
  else
    bad_arm "A4 the echo is bounded (ids=$loud_ids tail=$loud_lines transcript=$loud_log_lines)"
  fi

  # A4b — the SAME transcript, a smaller bound: fewer tail lines. Without this
  # the bound could be met by a short log and prove nothing.
  out2="$(env PYTEST_TAIL_LINES=4 bash "$work/scripts/run-pytest-suites.sh" 2>&1)"
  block2="$(printf '%s\n' "$out2" | awk '/^    ---- demo_loud: last /{f=1;next} /^    ---- end demo_loud ----/{f=0} f')"
  lines2="$(printf '%s\n' "$block2" | wc -l | tr -d ' ')"
  if [ "${lines2:-0}" -le 4 ] && [ "${lines2:-0}" -gt 0 ] && [ "${lines2:-0}" -lt "${loud_lines:-0}" ]; then
    ok "A4b PYTEST_TAIL_LINES=4 echoes ${lines2} line(s) from the same transcript, fewer than the default's ${loud_lines}"
  else
    bad_arm "A4b PYTEST_TAIL_LINES=4 echoes fewer lines (got ${lines2}, default ${loud_lines})"
  fi

  if printf '%s\n' "$out" | grep -qE '^  PASS  demo_green' &&
     ! printf '%s\n' "$out" | grep -qE '^    ---- demo_green' ; then
    ok "A5 a green suite is quiet: PASS with no id block and no tail"
  else
    bad_arm "A5 a green suite is quiet (it printed an echo, or no PASS line)"
  fi

  # --- arm A6: every test skipped is never a pass ----------------------------
  printf 'demo_skipped\n' > "$work/scripts/pytest-suites.txt"
  local out3 rc3
  out3="$(bash "$work/scripts/run-pytest-suites.sh" 2>&1)"
  rc3=$?
  if [ "$rc3" -eq 2 ] && printf '%s\n' "$out3" | grep -qE 'CANNOT-ASSESS  demo_skipped' &&
     ! printf '%s\n' "$out3" | grep -qE '^  PASS  demo_skipped' ; then
    ok "A6 an all-skipped suite is CANNOT-ASSESS (rc $rc3), never PASS"
  else
    bad_arm "A6 an all-skipped suite is CANNOT-ASSESS, never PASS (measured rc=$rc3)"
  fi

  # --- arm A7: vacuity — nothing failed, nothing is echoed -------------------
  printf 'demo_green\n' > "$work/scripts/pytest-suites.txt"
  local out4 rc4
  out4="$(bash "$work/scripts/run-pytest-suites.sh" 2>&1)"
  rc4=$?
  if [ "$rc4" -eq 0 ] && ! printf '%s\n' "$out4" | grep -qE '^    ---- ' &&
     ! printf '%s\n' "$out4" | grep -q 'FAILED ' ; then
    ok "A7 vacuity: a green run is rc 0 and echoes nothing"
  else
    bad_arm "A7 vacuity: a green run is rc $rc4 and echoes nothing (a marker leaked)"
  fi

  echo
  echo "-- what the run published for demo_red, verbatim:"
  printf '%s\n' "$out" | awk '/^  FAIL  demo_red/{f=1} f{print} /^    ---- end demo_red ----$/{f=0}' | sed 's/^/    | /'
  echo
  if [ "$failed_arms" -gt 0 ]; then
    echo "run-pytest-suites --self-test: NOT-OK — $failed_arms of $arms arm(s) failed" >&2
    return 1
  fi
  echo "run-pytest-suites --self-test: OK — $arms of $arms arm(s) held"
  return 0
}

if [ "${1:-}" = "--self-test" ]; then
  trap cleanup_selftest EXIT
  run_self_test
  exit $?
fi

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
  local tdir="$mod/tests" rc summary log npass nskip
  log="$(suite_log "$mod")"
  # The transcript goes to disk rather than into a shell variable: a runaway
  # suite then costs disk, not the gate's memory — and the path it lands on is
  # what a red suite publishes (issue #1661).
  timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 \
    python3 -m pytest -p no:cacheprovider -q "$tdir" > "$log" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    npass="$(grep -oE '[0-9]+ passed' "$log" 2>/dev/null | tail -1 | grep -oE '[0-9]+' || true)"
    [ -n "$npass" ] || npass=0
    if [ "$npass" -eq 0 ]; then
      # pytest exited 0 but NOTHING was assessed: every test skipped, so the
      # suite's property was never measured. "Nothing ran" is not a pass — a
      # permanently-skipped suite reading PASS is the false green AO-GR-4
      # forbids, so this is CANNOT-ASSESS by name, never a quiet pass.
      unknown=$((unknown + 1))
      nskip="$(grep -oE '[0-9]+ skipped' "$log" 2>/dev/null | tail -1 | grep -oE '[0-9]+' || true)"
      summary="$(tail -n 2 "$log" 2>/dev/null | tr '\n' ' ' | cut -c1-180)"
      echo "  CANNOT-ASSESS  ${prefix}$mod  (no test passed — ${nskip:-0} skipped; a suite that assessed nothing is never a pass — $log)" >&2
      record "$mod" "CANNOT-ASSESS" "$rc" "no test passed: $summary"
      emit_suite_tail "$mod" "$log"
    else
      passed=$((passed + 1))
      summary="$(grep -oE '[0-9]+ passed.*' "$log" 2>/dev/null | tail -1)"
      [ -n "$summary" ] || summary="all tests passed"
      echo "  PASS  ${prefix}$mod  ($summary)"
      record "$mod" "OK" "0" "$summary"
    fi
  elif [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    # timed out / killed: no verdict was reached — never a pass (issue #28).
    unknown=$((unknown + 1))
    echo "  CANNOT-ASSESS  ${prefix}$mod  (exceeded ${suite_timeout}s timeout — no verdict — $log)" >&2
    record "$mod" "CANNOT-ASSESS" "$rc" "timeout after ${suite_timeout}s"
    emit_suite_tail "$mod" "$log"
  else
    fail=$((fail + 1))
    summary="$(tail -n 2 "$log" 2>/dev/null | tr '\n' ' ' | cut -c1-180)"
    echo "  FAIL  ${prefix}$mod  (pytest exit $rc — $log)" >&2
    record "$mod" "FAIL" "$rc" "$summary"
    emit_suite_tail "$mod" "$log"
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
