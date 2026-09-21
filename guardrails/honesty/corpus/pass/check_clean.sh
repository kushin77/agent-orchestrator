#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.pass.check_clean
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture, fail-closed]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the honest-failing-exit positive control]
# invariants: ""
# gotchas: "it ships a real failing exit and a CANNOT-ASSESS path, so it is not a formality and the analyzer must leave it alone"
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/pass -- real honest artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/qa/verify-negative-controls.sh methodology --
# verification guards that ship a should-fail path.  This guard has a real
# failing exit and a CANNOT-ASSESS path, so the analyzer must leave it alone.

file="${1:?usage: check_clean.sh <file>}"

if [ ! -r "$file" ]; then
  echo "CANNOT-ASSESS: cannot read input" >&2
  exit 2
fi

if grep -qE "forbidden-alpha|forbidden-beta" "$file"; then
  echo "violation present"
  exit 1
fi

echo "clean"
exit 0
