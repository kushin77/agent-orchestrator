#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.pass.check_artifacts
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the honest-counter positive control]
# invariants: ""
# gotchas: "it counts what it examined and fails on absence instead of skipping it, so the analyzer must leave it alone"
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/pass -- real honest artifact (issue #28, acceptance criterion 5).
# Source: leaderboard guard code with real counters (the check-formality.sh
# counter heuristic) -- a loop that counts what it examined and fails on
# absence instead of skipping it.  Analyzer must leave it alone.

missing=0
for artifact in "$@"; do
  if [ ! -e "$artifact" ]; then
    missing=$((missing + 1))
  fi
done

if [ "$missing" -gt 0 ]; then
  echo "NOT-OK: missing artifacts"
  exit 1
fi

echo "OK"
exit 0
