#!/usr/bin/env bash
# check-remediation.sh — auto-generate violator remediation issues (issue #142).
#
# The "on changes" hook for the remediation scanner: runs the conformance
# board check, turns every finding into a remediation-issue payload (title,
# labels, owner lane, corrective steps, policy reference, evidence), dedups
# repeats, and writes .verify/remediation-report.json as evidence. Offline —
# no GitHub I/O here (that is `make remediation-dispatch`, network-only).
#
# This check does not fail the gate merely because remediation issues were
# generated (the board carries known, calibrated deviations today — see
# governance/conformance/policy.yaml's calibration note — and turning every
# reported deviation into a gate failure would make `make verify` red for
# reasons no lane can fix by working its own issue, the same reasoning
# `check-conformance.sh` already applies). It still genuinely fails when the
# scan itself cannot be assessed (no policy, no snapshot) — a check whose
# pass/fail paths collapse into the same exit code is a formality
# (no-false-green doctrine).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-remediation.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-remediation: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

python3 governance/remediation/cli.py scan
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
