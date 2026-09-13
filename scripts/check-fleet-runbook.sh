#!/usr/bin/env bash
# check-fleet-runbook.sh — the session-fleet bootstrap runbook gate (M26, #166).
#
# The bootstrap claim (#166) is: the only human action in the whole model is
# switching the sister session's model to DSv4FNone; everything else — channel
# init, mailbox setup, dispatcher start, claim of the next issue, verification,
# merge, and recovery from every named failure mode — is code. That claim is
# only true if a gate fails when the runbook regresses (no-false-green
# doctrine, GR-12). This check pins fleet/README.md:
#
#   * the three-step bootstrap (start sister session, set the model, sister
#     runs the loop) and the "ONLY human step" framing;
#   * the standing directive fleet/directive.json is the artifact read on
#     startup — the file must exist and be non-empty;
#   * the Recovery section: sister-dies/claim-TTL take-over, mailbox backlog,
#     and dispatcher failure (rc=2 -> brain alerted via fleet/health.py).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-runbook.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

readme="fleet/README.md"
directive="fleet/directive.json"
health_py="fleet/health.py"

for required_file in "$readme" "$directive" "$health_py"; do
  if [ ! -f "$required_file" ]; then
    echo "check-fleet-runbook: FAIL — $required_file is missing" >&2
    exit 1
  fi
done

if [ ! -s "$directive" ]; then
  echo "check-fleet-runbook: FAIL — $directive is empty (sister has no standing order to read)" >&2
  exit 1
fi

# Distinctive text the runbook must keep declaring. Removing a concept (or
# renaming it out of existence) fails this gate by name, not silently.
declare -a required_markers=(
  "the ONLY human step"
  "## Recovery"
  "ttl_hours"
  "reaped_agent"
  ".fleet/done"
  "2 failing"
  "1 degraded"
  "fleet/health.py check"
)

fail=0
for marker in "${required_markers[@]}"; do
  if ! grep -qF -- "$marker" "$readme"; then
    printf '  FAIL  %s (missing runbook text: %s)\n' "$readme" "$marker" >&2
    fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "  OK    $readme declares the human-free bootstrap and the recovery paths"
else
  fail=1
fi

# Non-vacuous: the check must detect a mutated runbook with recovery stripped.
mutant="$(mktemp)"
trap 'rm -f "$mutant"' EXIT
grep -v "^## Recovery" "$readme" | grep -vF "ttl_hours" | grep -vF "reaped_agent" > "$mutant"

mutant_fail=0
for marker in "${required_markers[@]}"; do
  if ! grep -qF -- "$marker" "$mutant"; then
    mutant_fail=1
    break
  fi
done
if [ "$mutant_fail" -ne 1 ]; then
  echo "check-fleet-runbook: FAIL — the mutant (recovery section stripped) was not detected; gate is vacuous" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "check-fleet-runbook: NOT-OK" >&2
  exit 1
fi

echo "check-fleet-runbook: OK"
exit 0
