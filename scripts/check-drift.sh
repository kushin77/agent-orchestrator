#!/usr/bin/env bash
# check-drift.sh — gate-contract drift detector (issue #29).
#
# The gate's coverage claim is only as truthful as the manifest that declares
# it. This check detects when the tree has drifted from what the gate believes
# it is testing (leaderboard "manifest drift after PR merges" shape):
#
#   * the gate is not inside a git worktree        -> CANNOT-ASSESS (exit 2)
#   * scripts/pytest-suites.txt is missing/empty   -> NOT-OK     (exit 1)
#   * a declared suite directory vanished           -> NOT-OK     (exit 1)
#     (coverage silently lost — a suite disappearing is the false-green risk)
#   * a committed suite that no gate runs            -> NOT-OK     (exit 1)
#     (a suite nothing collects is a coverage loss, and the verdict is RELAYED
#      from scripts/check-ungated-suites.sh — see the note above the relay, and
#      #891 for the measurement: a stderr WARN under RC 0 was a gate control
#      that could not fail anything)
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0/1/2.
#
# Usage: bash scripts/check-drift.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

manifest="scripts/pytest-suites.txt"

if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "check-drift: CANNOT-ASSESS — not inside a git worktree (cannot attest a commit)" >&2
  exit 2
fi

if [ ! -f "$manifest" ]; then
  echo "check-drift: FAIL — manifest $manifest is missing (gate has no declared suites)" >&2
  exit 1
fi

mapfile -t suites < <(sed -E 's/[[:space:]]+$//' "$manifest" | grep -vE '^\s*(#|$)' || true)
if [ "${#suites[@]}" -eq 0 ]; then
  echo "check-drift: FAIL — manifest $manifest declares no suites (empty coverage claim)" >&2
  exit 1
fi

fail=0
for mod in "${suites[@]}"; do
  if [ ! -d "$mod/tests" ]; then
    echo "check-drift: FAIL — declared suite '$mod' has no tests/ dir (coverage silently lost)" >&2
    fail=$((fail + 1))
  fi
done

# A committed suite that no gate runs is a REFUSAL, not a warning (#891), and it
# is discovered across the WHOLE tree rather than the six-prefix slice this block
# used to scan.
#
# The rule itself lives in exactly ONE place — scripts/check-ungated-suites.sh,
# which the gate of record (`make verify`) also runs — and this file RELAYS its
# verdict rather than keeping a second, weaker copy that drifts from it. The copy
# that lived here could not be trusted in either direction: it examined only
# `guardrails/*|gateway/*|registry/*|identity/*|engine/*|telemetry/*`, so
# `control-plane/instructions/csuite` and `governance/waves` were never seen at
# all; it required a `conftest.py`, so a suite without one was undiscoverable by
# the very mechanism meant to report it; and it kept the escape hatch invisible —
# 8 of the 11 undeclared suites are run by a dedicated check (`check-erp-*.sh`,
# `check-landing.sh`, `check-shared-frontend-onboarding.sh`), so a rule that read
# "undeclared" as "ungated" would have been wrong about 8 of them.
uncovered="$(bash "$root/scripts/check-ungated-suites.sh" --list-uncovered 2>/dev/null)"
uncovered_rc=$?
case "$uncovered_rc" in
  0) : ;;
  1)
    while IFS= read -r mod; do
      [ -n "$mod" ] || continue
      echo "check-drift: FAIL — '$mod' has a committed tests/ dir but no gate runs it: it is not declared in $manifest and no gate-invocation file names it (register it, name it from a check, or drop it)" >&2
      fail=$((fail + 1))
    done < <(printf '%s\n' "$uncovered") ;;
  *)
    # A definite defect above is never masked by an inassessment here.
    if [ "$fail" -gt 0 ]; then
      echo "check-drift: NOTE — the ungated-suite question could not be answered (rc $uncovered_rc), but this run is already NOT-OK" >&2
    else
      echo "check-drift: CANNOT-ASSESS — scripts/check-ungated-suites.sh could not answer the ungated-suite question (rc $uncovered_rc); this is not a pass" >&2
      exit 2
    fi ;;
esac

if [ "$fail" -gt 0 ]; then
  echo "check-drift: FAIL ($fail declared suite(s) missing)" >&2
  exit 1
fi
echo "check-drift: OK — ${#suites[@]} declared suite(s), manifest coherent with the tree"
exit 0

