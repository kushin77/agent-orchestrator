#!/usr/bin/env bash
# check-futureproof-e2e.sh — the futureproof capstone gate (issue #1200, #1193).
#
# The capstone (`governance/futureproof/e2e.py`) proves, for each of the ten
# classification mechanisms the operator named — class, pattern, template, rca,
# system, app, env-var, gov, issues, index — the chain
#
#   authority-declared -> gate-wired -> gate-falsifiable -> assesses-real-tree
#
# plus the repository-wide `mechanisms-complete` / `mechanisms-disjoint` halves.
#
# THIS GATE IS AUTO-DISCOVERED into `make verify` by `scripts/discover-checks.sh`
# (#698): its filename is `check-<name>.sh`, so it is wired the moment it lands
# and removing it is a named hole rather than a silent one.
#
# WHY `assesses-real-tree` EXISTS. `scripts/verify.sh` records rc 2
# CANNOT-ASSESS as SKIP and still prints `verify: PASS (... N skipped)`, so a gate
# that is PERMANENTLY CANNOT-ASSESS passes every other link the capstone has: it
# is discovered, executable and carries a negative control, and it assesses
# nothing. The capstone therefore RUNS each mechanism's gate and refuses one that
# does not reach a verdict (rc 0 or 1). Two live witnesses on pristine master:
#
#   scripts/check-paperclip-routines.sh  -> rc 2  could not build the dropped-entry tree
#   scripts/check-dispatch-queue.sh      -> rc 2  could not assess against the current board
#
# A gate that never assesses is a formality that hides in the `skipped` bucket.
#
# The suite half is run HERE rather than declared in scripts/pytest-suites.txt,
# so this gate is the one that exercises it (a declared-but-unrun suite is the
# gap #331 and #525 were both filed for).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-futureproof-e2e.sh
#
# ---knowledge---
# module_id: scripts.check-futureproof-e2e
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#331", "#525", "#698", "#1193"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-futureproof-e2e: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# One global scratch with one EXIT trap (SP-1). The canonical SIX-X template is
# deliberate: a run of exactly three X is the standalone marker `docs-lint`
# refuses (issue #804), so the short form would red the tree this gate guards.
scratch="$(mktemp -d /tmp/check-futureproof.XXXXXX)" || {
  echo "check-futureproof-e2e: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$scratch"' EXIT

failures=0
assessed=0
skipped=0

report() {
  printf '  %-9s %s\n' "$1" "$2"
}

# --- check 1: the capstone ---------------------------------------------------
out="$(env -u AO_FLEET_DIR python3 governance/futureproof/e2e.py 2>&1)"
rc=$?
case "$rc" in
  0)
    assessed=$((assessed + 1))
    report PASS "futureproof-e2e — $(printf '%s' "$out" | tail -1)"
    ;;
  1)
    assessed=$((assessed + 1))
    failures=$((failures + 1))
    report FAIL "futureproof-e2e — a link of a mechanism's chain did not hold"
    printf '%s\n' "$out" | sed 's/^/      /'
    ;;
  *)
    report SKIP "futureproof-e2e — CANNOT-ASSESS (rc $rc)"
    printf '%s\n' "$out" | sed 's/^/      /'
    skipped=$((skipped + 1))
    ;;
esac

# --- check 2: the module's own suite ----------------------------------------
# Inline rather than declared in scripts/pytest-suites.txt on purpose: this gate
# is the one that must exercise it.
if python3 -c 'import pytest' >/dev/null 2>&1; then
  out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
    --rootdir="$root" governance/futureproof/tests 2>&1)"
  rc=$?
  assessed=$((assessed + 1))
  if [ "$rc" -eq 0 ]; then
    report PASS "futureproof-suite — $(printf '%s' "$out" | tail -1)"
  else
    failures=$((failures + 1))
    report FAIL "futureproof-suite — the capstone's suite is red (rc $rc)"
    printf '%s\n' "$out" | tail -25 | sed 's/^/      /'
  fi
else
  report SKIP "futureproof-suite — CANNOT-ASSESS (pytest not installed; never a pass)"
  skipped=$((skipped + 1))
fi

# --- verdict ----------------------------------------------------------------
echo ""
if [ "$failures" -gt 0 ]; then
  echo "check-futureproof-e2e: FAIL ($failures of $assessed check(s) failed)"
  exit 1
fi
if [ "$assessed" -eq 0 ]; then
  echo "check-futureproof-e2e: CANNOT-ASSESS — no check could be assessed (never a pass)"
  exit 2
fi
if [ "$skipped" -gt 0 ]; then
  echo "check-futureproof-e2e: PASS ($assessed check(s) passed, $skipped skipped — skips are named above, never counted as passes)"
  exit 0
fi
echo "check-futureproof-e2e: PASS ($assessed of $assessed checks)"
exit 0
