#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.fail.check_results_present
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the absence-gated-optional formality shape]
# invariants: ""
# gotchas: "when every result artifact is absent the guard deliberately says optional and still exits 0, so no input can make it fail"
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/fail -- real incident artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/guard/check-formality.sh, documented spelling
# #3 -- three blocking gates wrapped so that when every result artifact was
# absent the guard echoed "optional" and still exited 0.  No input could make
# it fail.  Shape: never_fails_script with an absence-gated optional else.

for gate in gate-a gate-b gate-c; do
  if [ -f "$gate/result.txt" ]; then
    grep -q "PASS" "$gate/result.txt" && echo "$gate ok"
  else
    echo "optional: no result for $gate"
  fi
done

exit 0
