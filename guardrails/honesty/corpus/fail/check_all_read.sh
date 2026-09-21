#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.fail.check_all_read
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the uncounted-skip formality shape]
# invariants: ""
# gotchas: "this fixture is deliberately a formality: an unresolvable entry is skipped without being counted, so a scan of zero entries exits 0"
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/fail -- real incident artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/guard/check-formality.sh, documented spelling
# #1 -- `[ -e "$f" ] || continue` skipped unresolvable entries without
# counting, so a tool that visited ZERO entries reported "already matches"
# and exited 0.  Shape: uncounted_skip / never_fails_script.

for entry in "$@"; do
  [ -e "$entry" ] || continue
  grep -q "$NEEDLE" "$entry" && echo "already matches: $entry"
done

echo "report: scan complete"
exit 0
