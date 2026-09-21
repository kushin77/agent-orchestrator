#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.pass.check_fail_closed
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture, fail-closed]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the fail-closed positive control]
# invariants: "an unreadable input is CANNOT-ASSESS with exit 2 and a violation is a hard failure with exit 1, so neither reads as a pass"
# gotchas: ""
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/pass -- real honest artifact (issue #28, acceptance criterion 5).
# Source: leaderboard tests/blockproof + verify-negative-controls.sh
# fail-closed doctrine -- an unresolvable input is CANNOT-ASSESS (exit 2),
# never a silent pass, and a violation is a hard FAIL (exit 1).

input="${1:?usage: check_fail_closed.sh <file>}"

if [ ! -r "$input" ]; then
  echo "CANNOT-ASSESS: cannot read input" >&2
  exit 2
fi

if grep -q "violation-marker-zeta" "$input"; then
  echo "NOT-OK: violation found"
  exit 1
fi

echo "OK"
exit 0
