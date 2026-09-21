#!/usr/bin/env bash
# check-authority.sh — authority-matrix gate for issue #150 (repo separation,
# scoped admin rights, separation of duties, end-to-end closure).
#
# Three honest, independent signals over the SHIPPED matrix (no network, no
# writes, stdlib + PyYAML only):
#
#   1. validate   — the matrix is schema-valid and satisfies the semantic
#                   invariants: exactly one cross-repo principal (the enterprise
#                   controller), one repo per fleet, no fleet holding rights its
#                   repo never delegated, no roll-up right outside the cross-repo
#                   actor, merge authority only on a commander, no auditor role
#                   with an executing posture, and every fleet able to satisfy
#                   separation of duties. A defect is NOT-OK (exit 1).
#   2. selfcheck  — every DECLARED behavioral control in
#                   governance/authority/controls.yaml is run against the live
#                   engine and must return the verdict it declares: own-repo
#                   actions allowed, cross-repo actions denied, roll-up and merge
#                   refusals, SoD collisions denied, unassigned duties denied,
#                   closure denied without real evidence of one commit, unknown
#                   principals/repos/actions CANNOT-ASSESS, and a second
#                   cross-repo principal / scoped-two-repo fleet / mis-placed
#                   merge authority NOT-OK. Weakening the scoping rule, the SoD
#                   rules or the closure rule turns this red — the controls, not
#                   the author's confidence, are what make the gate real.
#   3. isolation  — the two-repo scenario is executed and its verdict DERIVED:
#                   own-repo state written and read, the cross-repo write denied
#                   with the target repo's state unchanged, cross-repo reads and
#                   key listings denied, the enterprise controller the only actor
#                   whose allowed actions span both repos, and roll-up refused to
#                   a scoped fleet.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0 OK / 1 NOT-OK /
# 2 CANNOT-ASSESS. A check that cannot run — python3 or PyYAML absent, the matrix
# or the schema missing/unparseable — is CANNOT-ASSESS, never a pass. NOT-OK (a
# real defect) dominates CANNOT-ASSESS in the aggregate so a genuine violation is
# never masked by an unreadable input.
#
# Usage: bash scripts/check-authority.sh
#
# ---knowledge---
# module_id: scripts.check-authority
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, offline-hermetic, declared-authority, schema-validation]
# derives_from: null
# owner_sme: security-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#28", "#150"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

cli="governance/authority/cli.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-authority: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -f "$cli" ]; then
  echo "check-authority: CANNOT-ASSESS — $cli is missing" >&2
  exit 2
fi

worst=0
note() { # note <rc>
  case "$1" in
    0) ;;
    1) [ "$worst" -lt 1 ] && worst=1 ;;
    *) [ "$worst" -lt 2 ] && worst=2 ;;
  esac
}

run() { # run <label> <cli args...>
  local label="$1"
  shift
  echo "== authority: $label =="
  env PYTHONDONTWRITEBYTECODE=1 python3 "$cli" "$@"
  local rc=$?
  case "$rc" in
    0) printf 'authority %s: OK\n' "$label" ;;
    1) printf 'authority %s: NOT-OK (exit 1)\n' "$label" >&2 ;;
    *) printf 'authority %s: CANNOT-ASSESS (exit %s)\n' "$label" "$rc" >&2 ;;
  esac
  note "$rc"
}

run "matrix validity" validate
run "behavioral controls" selfcheck
run "two-repo isolation" isolation

case "$worst" in
  0)
    echo "check-authority: OK — matrix valid, every declared control met, two repos isolated"
    exit 0
    ;;
  1)
    echo "check-authority: FAIL — the authority matrix or a declared control did not hold" >&2
    exit 1
    ;;
  *)
    echo "check-authority: CANNOT-ASSESS — the authority model could not reach a verdict" >&2
    exit 2
    ;;
esac
