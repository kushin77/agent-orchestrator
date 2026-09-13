#!/usr/bin/env bash
# check-issue-claims.sh — claim-time issue-order enforcement (issue #157).
#
# The declaration gate (scripts/check-chronological-dispatch.sh) proves the rule
# is *written down*. This gate proves the rule *bites*: it replays the claim
# ledger against the committed board snapshot and fails on any violation —
# malformed record, claim on a closed or blocked issue, unjustified reason,
# duplicate live claim, release without claim, or a claim that expired without
# release while the issue is still open.
#
# The audit always runs its own self-control mutants first (unjustified reason,
# scavenged claim, blocked claim, closed claim, duplicate claim, double release,
# malformed record). If any mutant passes, the check FAILS: an audit that cannot
# fail is a formality (GR-12 / AO-GR-19).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no snapshot, no python3).
#
# Usage: bash scripts/check-issue-claims.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-issue-claims: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

python3 governance/dispatch/cli.py audit
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) exit 2 ;;
  *) exit 1 ;;
esac
