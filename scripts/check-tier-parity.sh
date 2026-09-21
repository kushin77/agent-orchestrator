#!/usr/bin/env bash
# check-tier-parity.sh — one gateway/finops/tiers.yaml judges every runtime's
# spawn (issue #1274): the fleet, a Claude subagent dispatch, and a hermes
# persona are all judged by governance/spawn/tiering.py against the SAME
# tiers.yaml window. This gate proves the judge actually refuses out-of-window
# spawns for every one of those runtimes, by name (FINOPS-ROLE-NOT-ALLOWED),
# and that the vocabulary is not duplicated (a scratch second copy of the
# ladder is refused).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# Usage: bash scripts/check-tier-parity.sh [--self-test]
#
# ---knowledge---
# module_id: scripts.check-tier-parity
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, named-refusal]
# derives_from: null
# owner_sme: cfo
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#1274"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-tier-parity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -f governance/spawn/tiering.py ]; then
  echo "check-tier-parity: CANNOT-ASSESS — governance/spawn/tiering.py missing" >&2
  exit 2
fi
if [ ! -f gateway/finops/tiers.yaml ]; then
  echo "check-tier-parity: CANNOT-ASSESS — gateway/finops/tiers.yaml missing" >&2
  exit 2
fi

judge="python3 -m governance.spawn.tiering judge"
fail=0

expect_ok() { # expect_ok <name> <cmd...>
  local name="$1"; shift
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  OK    ALLOWED $name"
  else
    echo "  FAIL  $name was refused (rc=$rc) — over-strict" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_refused() { # expect_refused <name> <finding> <cmd...>
  local name="$1" want="$2"; shift 2
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 1 ] && [[ "$out" == *"$want"* ]]; then
    echo "  OK    REFUSED $name — $want"
  elif [ "$rc" -eq 0 ]; then
    echo "  FAIL  $name was ACCEPTED (a judge every role can slip past is a formality)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  else
    echo "  FAIL  $name refused (rc=$rc) but not with $want" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

echo "== real tree: allowed spawns, one per role =="
expect_ok "fleet at code-author default (L0)" \
  $judge --role fleet --class code-author --tier L0
expect_ok "claude-subagent at code-author default (L0)" \
  $judge --role claude-subagent --class code-author --tier L0
expect_ok "hermes-persona at code-review default (L1)" \
  $judge --role hermes-persona --class code-review --tier L1

echo "== real tree: refused mutants, one per role =="
expect_refused "fleet spawn at a forbidden tier (below security floor)" FINOPS-ROLE-NOT-ALLOWED \
  $judge --role fleet --class security-review --tier L0
expect_refused "claude-subagent at opus-equivalent (L2) for an L0-capped class" FINOPS-ROLE-NOT-ALLOWED \
  $judge --role claude-subagent --class code-author --tier L2
expect_refused "hermes-persona above its floor (memory-ops capped at L0)" FINOPS-ROLE-NOT-ALLOWED \
  $judge --role hermes-persona --class memory-ops --tier L1

echo "== named preconditions =="
expect_refused "unknown role" FINOPS-UNKNOWN-ROLE \
  $judge --role rogue-runtime --class code-author --tier L0
expect_refused "unknown task class" FINOPS-UNKNOWN-TASK-CLASS \
  $judge --role fleet --class not-a-real-class --tier L0
expect_refused "unknown tier" FINOPS-UNKNOWN-TIER \
  $judge --role fleet --class code-author --tier L9

work="/tmp/ao1274-tier-parity.$$.$(date +%s)"
mkdir -p "$work" || exit 2
trap 'rm -rf "$work"' EXIT

echo "== dupcheck: no second hardcoded copy of the ladder in the judge =="
# governance/spawn/tiering.py must derive its tier vocabulary from
# gateway/finops/tiers.yaml at load time (via loader.load_tier_table), never
# from a second, hand-maintained L0/L1/L2 literal. A literal ("L0", "L1", "L2")
# tuple/list anywhere in the module IS that second copy — refuse it.
if grep -nE '\("L0"[, ]+"L1"[, ]+"L2"|\[.?"L0".?,.?"L1".?,.?"L2"' governance/spawn/tiering.py; then
  echo "  FAIL  governance/spawn/tiering.py hardcodes an L0/L1/L2 literal — a scratch second copy of the ladder" >&2
  fail=$((fail + 1))
else
  echo "  OK    no hardcoded ladder literal; tiering.py reads tiers.yaml as the single source"
fi
if grep -q "load_tier_table" governance/spawn/tiering.py; then
  echo "  OK    tiering.py loads the ladder from gateway/finops/loader.load_tier_table"
else
  echo "  FAIL  governance/spawn/tiering.py does not load gateway/finops/loader.load_tier_table" >&2
  fail=$((fail + 1))
fi

echo "== missing table is CANNOT-ASSESS, never a pass =="
out="$($judge --role fleet --class code-author --tier L0 --tiers-path "$work/no-such-file.yaml" 2>&1)"
rc=$?
if [ "$rc" -eq 2 ] && [[ "$out" == *"CANNOT-ASSESS"* ]]; then
  echo "  OK    CANNOT-ASSESS on a missing tiers table (rc=2)"
else
  echo "  FAIL  missing tiers table did not yield CANNOT-ASSESS (rc=$rc)" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-tier-parity: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-tier-parity: OK — fleet, claude-subagent and hermes-persona all judged against one gateway/finops/tiers.yaml, mutants refused by name"
exit 0
