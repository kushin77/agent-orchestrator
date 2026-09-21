#!/usr/bin/env bash
# check-lease-policy.sh — one declared policy for every fleet lease and TTL,
# with its ordering constraints (issue #322).
#
# The fleet honours seven timings whose relationships are load-bearing: a session
# judged orphaned between two rung beats loses a live lane, and a claim reaped
# before its session's TTL destroys work in flight. This gate proves the policy
# in governance/policy/lease.py is the ONE place those numbers live, and that its
# ordering invariants BITE rather than merely exist:
#
#   * the declared values and every ordering invariant hold;
#   * no module restates a policy value as its own numeric constant, and every
#     consumer reads it from the policy — a hard-coded value is a finding;
#   * every invariant is refuted by a named mutation, and the hard-code scan is
#     refuted by a synthetic hard-code (a policy whose invariants cannot break is
#     a formality, GR-12 / AO-GR-19);
#   * the shell-level self-mutation the issue asks for: a session TTL below the
#     rung heartbeat must be refused, with the invariant named.
#
# If any of those passes when it should fail, this check FAILS.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-lease-policy.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lease-policy: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

policy_file="governance/policy/lease.py"
if [ ! -f "$policy_file" ]; then
  echo "check-lease-policy: CANNOT-ASSESS — $policy_file not found" >&2
  exit 2
fi

policy="python3 $policy_file"
fail=0

echo "== declared values and ordering invariants =="
if $policy check; then
  echo "  OK    every declared value is present and every invariant holds"
else
  echo "  FAIL  the declared policy is inconsistent" >&2
  fail=$((fail + 1))
fi

echo "== consumers read the policy; nothing restates a value =="
if $policy scan; then
  echo "  OK    every consumer reads the policy (no hard-coded lease/TTL)"
else
  echo "  FAIL  a module hard-codes a value the policy owns" >&2
  fail=$((fail + 1))
fi

echo "== self-control: every invariant is refutable =="
if $policy self-control; then
  echo "  OK    every mutation is refused, naming its invariant"
else
  echo "  FAIL  an invariant (or the hard-code scan) cannot fail" >&2
  fail=$((fail + 1))
fi

echo "== mutation proof: session TTL below the rung heartbeat =="
probe="$(mktemp)"
trap 'rm -f "$probe"' EXIT
$policy check --mutate session-ttl-below-rung-heartbeat >"$probe" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  FAIL  a session TTL below the rung heartbeat was ACCEPTED" >&2
  sed 's/^/        /' "$probe" >&2
  fail=$((fail + 1))
elif grep -q "session-ttl-exceeds-rung-heartbeat" "$probe"; then
  echo "  OK    refused (exit $rc) naming 'session-ttl-exceeds-rung-heartbeat'"
else
  echo "  FAIL  refused (exit $rc) without naming the broken invariant" >&2
  sed 's/^/        /' "$probe" >&2
  fail=$((fail + 1))
fi

echo ""
if [ "$fail" -gt 0 ]; then
  echo "check-lease-policy: FAIL — $fail finding(s)" >&2
  exit 1
fi
echo "check-lease-policy: OK — one declared lease/TTL policy, invariants enforced"
exit 0
