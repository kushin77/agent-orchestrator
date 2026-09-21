#!/usr/bin/env bash
# check-policy-schema.sh — policy schema validation signal for the gate (issue #29).
#
# Consumes the guardrails/policy module (issue #26) read-only: every shipped
# policy bundle plus the controls registry is validated against its JSON
# schemas via the policy CLI's startup-validation gate. An invalid/unsafe
# policy must fail the deploy, never at runtime — so an invalid policy here
# fails the gate (NOT-OK).
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0/1/2.
#   * "valid: yes"          -> OK           (exit 0)
#   * "valid: NO"           -> NOT-OK       (exit 1)  — a real validation defect
#   * cannot run/assess     -> CANNOT-ASSESS(exit 2)  — module error / unknown
#
# Usage: bash scripts/check-policy-schema.sh
#
# ---knowledge---
# module_id: scripts.check-policy-schema
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#26", "#28", "#29"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

out="$(python3 guardrails/policy/cli.py validate 2>&1)"
rc=$?

echo "$out"

if [ "$rc" -eq 0 ]; then
  echo "check-policy-schema: OK — all policy bundles valid"
  exit 0
fi
if printf '%s' "$out" | grep -q 'valid: NO'; then
  echo "check-policy-schema: FAIL — one or more policies failed schema/semantic validation" >&2
  exit 1
fi
echo "check-policy-schema: CANNOT-ASSESS — validation could not run (exit $rc)" >&2
exit 2
