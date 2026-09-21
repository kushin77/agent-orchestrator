#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.fail.check_everything_clean
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the never-fails-function formality shape]
# invariants: ""
# gotchas: "the found and not-found paths deliberately return the same code, so the function name is a lie"
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/fail -- real incident artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/guard/check-formality.sh, function rule -- a
# check-named function whose found and not-found paths return the same code,
# 0 (identical-exit-code paths).  A caller sees PASS either way, so the name
# of the function is a lie.  Shape: never_fails_function.

check_everything_clean() {
  if grep -q "mismatch" "$1"; then
    echo "mismatch reported"
    return 0
  else
    echo "no mismatch"
    return 0
  fi
}

check_everything_clean "${1:?usage: check_everything_clean.sh <file>}"
exit 0
