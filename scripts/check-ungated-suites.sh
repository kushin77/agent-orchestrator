#!/usr/bin/env bash
# check-ungated-suites.sh — refuse, BY NAME, a committed test suite that no gate
# runs (issue #891).
#
# THE DEFECT THIS EXISTS FOR
#   162 passing tests were committed in three suites that NO gate ran, and
#   neither mechanism that should have caught it could:
#
#     * `scripts/run-pytest-suites.sh` is reached by `make gate` /
#       `scripts/merge-gate.sh`, NOT by the gate of record — so a `make verify`
#       run exercised none of them. Its #698 auto-register block does sweep
#       undeclared suites, but only where the gate that runs it is reached, and
#       it leaves the suite looking ungated to every other gate;
#     * `scripts/check-drift.sh` could not see two of the three AT ALL: it
#       discovers suites via `git ls-files '*/tests/conftest.py'` and then keeps
#       only a hardcoded six-prefix slice of the tree
#       (`guardrails/*|gateway/*|registry/*|identity/*|engine/*|telemetry/*`),
#       so `control-plane/*` and `governance/*` were never examined — and its
#       only signal on the whole class was a stderr WARN under RC 0, i.e. a gate
#       that cannot fail (GR-12).
#
#   The manifest cannot cover a NESTED suite even when its parent is declared:
#   `scripts/run-pytest-suites.sh` runs `"$mod/tests"` — path-exact — so
#   `control-plane/instructions/csuite` is invisible while
#   `control-plane/instructions` is declared, which is worse than being plainly
#   absent: a reviewer who asks "is `control-plane/instructions` declared?" gets
#   yes, and stops.
#
# WHAT IS MEASURED (one rule, two arms)
#
#   1. COVERAGE — every committed `<module>/tests/` holding a `test_*.py` must be
#      either DECLARED in `scripts/pytest-suites.txt` or NAMED by a
#      gate-invocation file (`scripts/verify.sh`, `Makefile`, `scripts/gate.sh`,
#      `scripts/merge-gate.sh`, `scripts/qa-loop.sh`, or any `scripts/check-*.sh`).
#      A suite that is neither is refused by name, rc 1.
#        * the candidate list comes from `git ls-files` — the WHOLE tree, never a
#          prefix list — and the discovery does NOT require a `conftest.py`: a
#          suite without one is not merely undeclared, it is undiscoverable by
#          `check-drift.sh`, which is exactly how it stays ungated;
#        * the named-by-a-check escape hatch is the documented one, and it is
#          load-bearing: 8 of the 11 suites this rule found the first time it
#          ran are run by a dedicated check (`scripts/check-erp-*.sh`,
#          `check-landing.sh`, `check-shared-frontend-onboarding.sh`) and stay
#          deliberately undeclared — a rule that ignored the hatch would be red
#          on master, and a gate that is red on master is ignored;
#        * a reference counts only on a NON-COMMENT line, so prose cannot buy
#          coverage;
#        * note the asymmetry, deliberately: the STRONGER form — a DECLARED suite
#          must be named as a PYTEST TARGET — is already enforced, with its own
#          provenanced baseline, by `scripts/check-gate-coverage.sh`. This arm
#          closes the hole that one structurally cannot see: a suite that is not
#          declared is never examined by it at all.
#
#   2. EXECUTION — the three suites this issue is about are RUN here, each in
#      isolation: `control-plane/instructions/csuite` (93 tests),
#      `engine/core/tickets` (39) and `governance/waves` (30). They are declared
#      AND named from inside this check, which `scripts/discover-checks.sh`
#      auto-discovers into `make verify` (#698) — so the gate of record exercises
#      them with no hand-edit to `scripts/verify.sh`.
#        * each target is written out LITERALLY on the pytest line that runs it,
#          never through a loop variable: `scripts/check-gate-coverage.sh` reads a
#          declared suite as wired only when a gate file or an invoked check names
#          it as a pytest target, so `... -q "$var/tests"` would leave the suite
#          looking ungated to that gate while this check ran it;
#        * the suites cannot share one pytest invocation (sibling conftest
#          sys.path bootstrap + duplicate test-module basenames — see
#          `scripts/run-pytest-suites.sh`), so each runs alone.
#
# EXIT CONTRACT (guardrails/honesty tri-state, #28): 0 = OK; 1 = NOT-OK (a
# committed suite is ungated, or one of the three is red); 2 = CANNOT-ASSESS (no
# git worktree, no manifest, no python3, or a suite with no verdict). An empty
# run is never a pass.
#
# Usage:
#   bash scripts/check-ungated-suites.sh                   # assert, then run
#   bash scripts/check-ungated-suites.sh --list-uncovered  # the ungated suites,
#                                                          # one per line; rc 1
#                                                          # when the list is
#                                                          # non-empty, rc 0
#                                                          # when it is empty,
#                                                          # rc 2 when the
#                                                          # question cannot be
#                                                          # answered. This is
#                                                          # the listing mode
#                                                          # scripts/check-drift.sh
#                                                          # relays into its own
#                                                          # refusal, so the rule
#                                                          # has ONE
#                                                          # implementation.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

manifest="scripts/pytest-suites.txt"
suite_timeout="${SUITE_TIMEOUT:-300}"

mode="check"
case "${1:-}" in
  "") : ;;
  --list-uncovered) mode="list" ;;
  *)
    printf 'check-ungated-suites: CANNOT-ASSESS — unknown argument %s (usage: bash scripts/check-ungated-suites.sh [--list-uncovered])\n' "'$1'" >&2
    exit 2 ;;
esac

cannot_assess() {
  printf 'check-ungated-suites: CANNOT-ASSESS — %s\n' "$1" >&2
  exit 2
}

if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
  cannot_assess "not inside a git worktree — the committed suites cannot be read, and a working-tree scan would count a peer lane's scratch copy"
fi

if [ ! -f "$manifest" ]; then
  cannot_assess "suite manifest $manifest is missing — a declared suite cannot be told from an undeclared one (scripts/check-gate-coverage.sh refuses this same tree)"
fi

mapfile -t declared < <(sed -E 's/[[:space:]]+$//' "$manifest" | grep -vE '^[[:space:]]*(#|$)' || true)
if [ "${#declared[@]}" -eq 0 ]; then
  cannot_assess "suite manifest $manifest declares no suites — an empty coverage claim is not a pass"
fi

# `|a|b|c|` — the bash-native containment test (a piped quiet grep can be
# SIGPIPE'd into the wrong branch, and a new script carries no allowance for it).
declared_set="|"
for entry in "${declared[@]}"; do
  declared_set="${declared_set}${entry}|"
done

# --- the committed suite corpus ---------------------------------------------
# Every tracked `*/tests/` holding a `test_*.py`. `%/tests/*` strips the
# SHORTEST suffix, i.e. the INNERMOST tests/ dir, so a suite nested inside
# another suite's tests/ names itself, not its ancestor.
suites=()
while IFS= read -r file; do
  [ -n "$file" ] || continue
  suites+=("${file%/tests/*}")
done < <(git ls-files '*/tests/test_*.py' 2>/dev/null)

if [ "${#suites[@]}" -eq 0 ]; then
  cannot_assess "no tracked '*_/tests/test_*.py' found — the corpus is unreadable (an empty run is not a pass)"
fi
if [ "${#suites[@]}" -gt 1 ]; then
  mapfile -t suites < <(printf '%s\n' "${suites[@]}" | LC_ALL=C sort -u)
fi

# --- the gate-invocation surface --------------------------------------------
# The five gate files are literal, exactly as in scripts/check-gate-coverage.sh:
# a glob (`scripts/*.sh`) would let the universe grow silently. The check scripts
# are added because the documented escape hatch is "a dedicated check runs it".
candidates=()
for file in scripts/verify.sh Makefile scripts/gate.sh scripts/merge-gate.sh scripts/qa-loop.sh scripts/check-*.sh; do
  [ -f "$file" ] || continue
  candidates+=("$file")
done

surface=""
if [ "${#candidates[@]}" -gt 0 ]; then
  # Whole-line comments are dropped (a comment cannot run), then leading
  # whitespace, so a line is tested as a human reads it.
  surface="$(sed -E '/^[[:space:]]*#/d; s/^[[:space:]]+//' "${candidates[@]}" 2>/dev/null)"
fi

uncovered=()
for suite in "${suites[@]}"; do
  covered=0
  case "$declared_set" in
    *"|$suite|"*) covered=1 ;;
  esac
  if [ "$covered" -eq 0 ]; then
    case "$surface" in
      *"$suite"*) covered=1 ;;
    esac
  fi
  if [ "$covered" -eq 0 ]; then
    uncovered+=("$suite")
  fi
done

if [ "$mode" = "list" ]; then
  for suite in "${uncovered[@]}"; do
    printf '%s\n' "$suite"
  done
  if [ "${#uncovered[@]}" -gt 0 ]; then
    exit 1
  fi
  exit 0
fi

echo "check-ungated-suites: ${#suites[@]} committed suite(s) discovered across the tree, ${#declared[@]} declared, ${#uncovered[@]} run by no gate"

fail=0
if [ "${#uncovered[@]}" -gt 0 ]; then
  for suite in "${uncovered[@]}"; do
    printf '  FAIL  %s  (committed suite: not declared in %s and no gate-invocation file names it — register it, name it from a check, or drop it)\n' \
      "$suite" "$manifest" >&2
  done
  fail=$((fail + 1))
fi

# --- arm 2: run the three suites the issue names -----------------------------
# Literal targets, one pytest invocation each, in isolation.
csuite="control-plane/instructions/csuite"
tickets="engine/core/tickets"
waves="governance/waves"

passed=0
red=0
unknown=0

report_suite() { # <suite> <rc> <output>
  local suite="$1" rc="$2" out="$3" summary
  if [ "$rc" -eq 0 ]; then
    summary="$(printf '%s\n' "$out" | grep -oE '[0-9]+ passed.*' | tail -1)"
    [ -n "$summary" ] || summary="all tests passed"
    passed=$((passed + 1))
    echo "  PASS  $suite  ($summary)"
  elif [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    # timed out / killed: no verdict was reached — never a pass.
    unknown=$((unknown + 1))
    printf '  CANNOT-ASSESS  %s  (exceeded %ss, or was killed: no verdict)\n' "$suite" "$suite_timeout" >&2
  else
    red=$((red + 1))
    summary="$(printf '%s\n' "$out" | tail -1 | cut -c1-160)"
    printf '  FAIL  %s  (exit %s: %s)\n' "$suite" "$rc" "$summary" >&2
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-ungated-suites: CANNOT-ASSESS — python3 is unavailable; the three suites could not run" >&2
  if [ "$fail" -gt 0 ]; then
    echo "check-ungated-suites: NOT-OK — $fail coverage finding(s) (the run was not attempted)" >&2
    exit 1
  fi
  exit 2
fi

out="$(timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$csuite/tests" 2>&1)"
report_suite "$csuite" "$?" "$out"

out="$(timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$tickets/tests" 2>&1)"
report_suite "$tickets" "$?" "$out"

out="$(timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$waves/tests" 2>&1)"
report_suite "$waves" "$?" "$out"

sha="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
echo "check-ungated-suites: $passed passed, $red red, $unknown no-verdict (sha ${sha:0:12})"

if [ "$fail" -gt 0 ] || [ "$red" -gt 0 ]; then
  printf 'check-ungated-suites: NOT-OK — %s coverage finding(s), %s red suite(s)\n' "$fail" "$red" >&2
  exit 1
fi
if [ "$unknown" -gt 0 ]; then
  printf 'check-ungated-suites: CANNOT-ASSESS — %s suite(s) reached no verdict\n' "$unknown" >&2
  exit 2
fi
echo "check-ungated-suites: OK — every committed suite is declared or named by a gate, and all 3 suites this check runs are green"
exit 0
