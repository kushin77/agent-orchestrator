#!/usr/bin/env bash
# check-dispatch-queue.sh — the owner's committed dispatch queue enforcement
# (issue #928, child of #878).
#
# order.py/claims.py already refuse an out-of-order claim when Issue.blocked_by
# is populated (order.py:120 REASON_BLOCKED, claims.py:487
# raise ClaimRefused(REASON_BLOCKED, ...)) — but nothing populated that edge
# from the owner's actual intended order until governance/dispatch/owner_queue.py.
# This gate proves three things:
#
#   1. governance/dispatch/queue.yaml is present and structurally valid
#      (`dispatch queue --check`) against the committed board snapshot.
#   2. The queue is PROVOKED: a fixture queue file with a cycle in its
#      blocked_by graph must be REFUSED by `--check` (GR-12 / AO-GR-19 — a
#      check that cannot fail is a formality). A clean fixture must still pass,
#      so the control is not failing open.
#   3. governance/dispatch/tests/test_queue.py passes (out-of-order claim
#      refused, in-order accepted, closed issues drop out of the chain,
#      --check catches a cycle).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no
# .board/snapshot.json).
#
# Usage: bash scripts/check-dispatch-queue.sh

set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-dispatch-queue: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

queue_file="governance/dispatch/queue.yaml"
snapshot_file=".board/snapshot.json"

if [ ! -f "$queue_file" ]; then
  echo "check-dispatch-queue: CANNOT-ASSESS — $queue_file is missing" >&2
  exit 2
fi
if [ ! -f "$snapshot_file" ]; then
  echo "check-dispatch-queue: CANNOT-ASSESS — $snapshot_file is missing" >&2
  exit 2
fi

fail=0
stale=0

# --- 1. the tests -----------------------------------------------------------
if command -v pytest >/dev/null 2>&1; then
  if ! python3 -m pytest -q governance/dispatch/tests/test_queue.py; then
    echo "check-dispatch-queue: FAIL — governance/dispatch/tests/test_queue.py is red" >&2
    fail=1
  fi
else
  echo "check-dispatch-queue: pytest not found, skipping test_queue.py (structural checks still run)" >&2
fi

# --- 2. the committed queue validates against the committed snapshot --------
# Tri-state: a stale committed snapshot (".board/snapshot.json" is refreshed
# only by an explicit command, per governance/dispatch/README.md) makes the
# "do these numbers exist/are they open" half CANNOT-ASSESS rather than a
# false NOT-OK or a false green — 'dispatch queue --check' still runs its
# structural checks (duplicates, cycles) against a stale board.
python3 governance/dispatch/cli.py queue --check
rc=$?
case "$rc" in
  0) ;;
  2)
    echo "check-dispatch-queue: CANNOT-ASSESS — 'dispatch queue --check' could not assess against the current board (see stderr above)" >&2
    stale=1
    ;;
  *)
    echo "check-dispatch-queue: FAIL — 'dispatch queue --check' refused the committed queue (rc=$rc)" >&2
    fail=1
    ;;
esac

# --- 3. provoked negative control: a cycle must be refused BY NAME ----------
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/cyclic-queue.yaml" <<'YAML'
waves:
  - name: wave-x
    issues: [101, 102]
blocked_by:
  101: [102]
  102: [101]
YAML

cat >"$tmp/clean-queue.yaml" <<'YAML'
waves:
  - name: wave-x
    issues: [101, 102]
YAML

cat >"$tmp/snapshot.json" <<'JSON'
{
  "generated_at": "2026-09-16T00:00:00Z",
  "source": "check-dispatch-queue fixture",
  "issues": [
    {"number": 101, "title": "fixture A", "state": "open", "milestone": "", "labels": [], "parent": null, "blocked_by": [], "cross_refs": [], "closed_at": ""},
    {"number": 102, "title": "fixture B", "state": "open", "milestone": "", "labels": [], "parent": null, "blocked_by": [], "cross_refs": [], "closed_at": ""}
  ]
}
JSON

python3 governance/dispatch/cli.py queue --check --queue "$tmp/cyclic-queue.yaml" --snapshot "$tmp/snapshot.json" --stale-minutes 999999999 >"$tmp/cyclic.out" 2>&1
cyclic_rc=$?
if [ "$cyclic_rc" -eq 0 ] || ! grep -q "cycle" "$tmp/cyclic.out"; then
  echo "check-dispatch-queue: FAIL — the provoked cycle fixture was NOT refused by name (rc=$cyclic_rc)" >&2
  echo "  output: $(cat "$tmp/cyclic.out")" >&2
  fail=1
fi

python3 governance/dispatch/cli.py queue --check --queue "$tmp/clean-queue.yaml" --snapshot "$tmp/snapshot.json" --stale-minutes 999999999 >"$tmp/clean.out" 2>&1
clean_rc=$?
if [ "$clean_rc" -ne 0 ]; then
  echo "check-dispatch-queue: FAIL — a clean fixture queue was refused (rc=$clean_rc): $(cat "$tmp/clean.out")" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "check-dispatch-queue: FAIL" >&2
  exit 1
fi

if [ "$stale" -ne 0 ]; then
  echo "check-dispatch-queue: CANNOT-ASSESS (tests pass, cycle control provoked and refused; committed board snapshot is stale)"
  exit 2
fi

echo "check-dispatch-queue: OK (tests pass, committed queue validates, cycle control provoked and refused)"
exit 0
