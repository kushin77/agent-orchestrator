#!/usr/bin/env bash
# ---knowledge---
# module_id: guardrails.honesty.corpus.fail.check_provider_health
# system: guardrails
# app: honesty
# solution_class: template
# patterns: [negative-control, offline-fixture]
# derives_from: null
# owner_sme: qa-sme
# tier: L0
# interfaces: [the skip-counted-as-pass formality shape]
# invariants: ""
# gotchas: "a provider that was never configured deliberately reports SKIP with exit 0, which reads as healthy"
# related: ["#28"]
# do_not_duplicate: null
# ---knowledge---
# corpus/fail -- real incident artifact (issue #28, acceptance criterion 5).
# Source: leaderboard scripts/guard/check-formality.sh, documented spelling
# #4 -- a SKIP counted as a pass in a health check reporting UP for a provider
# that had never been configured.  On the authoritative surface a provider
# that must be checked but was skipped is a failure, not a healthy skip.
# Shape: skip_counted_as_pass / never_fails_script.

if [ -z "${PROVIDER_STATUS_FILE:-}" ]; then
  echo "SKIP: provider never configured"
  exit 0
fi

if grep -q "UP" "$PROVIDER_STATUS_FILE"; then
  echo "provider UP"
  exit 0
fi

exit 0
