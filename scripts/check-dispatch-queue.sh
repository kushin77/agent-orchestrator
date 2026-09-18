#!/usr/bin/env bash
# check-dispatch-queue.sh — the owner's committed dispatch queue enforcement
# (issue #928, child of #878; the freshness half is issue #1189).
#
# order.py/claims.py already refuse an out-of-order claim when Issue.blocked_by
# is populated (order.py:120 REASON_BLOCKED, claims.py:487
# raise ClaimRefused(REASON_BLOCKED, ...)) — but nothing populated that edge
# from the owner's actual intended order until governance/dispatch/owner_queue.py.
# This gate proves four things:
#
#   1. governance/dispatch/queue.yaml is present and structurally valid
#      (`dispatch queue --check`) against the committed board snapshot, and no
#      number it queues is one the board has since CLOSED — the finding this
#      check exists for (issue #1189).
#   2. The committed board snapshot's AGE is inside the tolerance this consumer
#      declares (`dispatch freshness` -> governance/dispatch/queue_freshness.py):
#      a committed, offline artifact can never meet the 15-minute LIVENESS
#      threshold controls.yaml declares for a running loop, and a check armed
#      with a bound its own input cannot meet is a formality that
#      `verify.sh` folds into SKIP while the verdict line still reads as a pass
#      (issues #1189, #1199; the ticket projection's consumer half is #1077).
#   3. The queue is PROVOKED: a fixture queue file with a cycle in its
#      blocked_by graph must be REFUSED by `--check`, a fixture queue naming a
#      CLOSED issue must be REFUSED by name, and a clean fixture must still pass,
#      so no control is failing open (GR-12 / AO-GR-19 — a check that cannot
#      fail is a formality).
#   4. governance/dispatch/tests/test_queue.py and
#      governance/dispatch/tests/test_queue_freshness.py pass (out-of-order
#      claim refused, in-order accepted, closed issues drop out of the chain,
#      --check catches a cycle; the freshness refusal and its fail-closed
#      absence half).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS — and CANNOT-ASSESS is
# never a pass. Absence fails CLOSED: no python3, a missing/unreadable board
# snapshot, a missing queue file, or a bad invocation is rc 2.
#
# Usage: bash scripts/check-dispatch-queue.sh [--snapshot PATH] [--queue PATH]
#
#   The two optional paths are the negative-control seam the out-of-band
#   provocation driver plants fixtures through; the NO-ARGUMENT invocation is
#   the gate (scripts/discover-checks.sh passes none).

set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

usage="usage: bash scripts/check-dispatch-queue.sh [--snapshot PATH] [--queue PATH]"

queue_file="governance/dispatch/queue.yaml"
snapshot_file=".board/snapshot.json"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --snapshot)
      if [ "$#" -lt 2 ]; then
        echo "check-dispatch-queue: CANNOT-ASSESS — --snapshot needs a path ($usage)" >&2
        exit 2
      fi
      snapshot_file="$2"
      shift 2
      ;;
    --queue)
      if [ "$#" -lt 2 ]; then
        echo "check-dispatch-queue: CANNOT-ASSESS — --queue needs a path ($usage)" >&2
        exit 2
      fi
      queue_file="$2"
      shift 2
      ;;
    *)
      echo "check-dispatch-queue: CANNOT-ASSESS — unknown argument: $1 ($usage)" >&2
      exit 2
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-dispatch-queue: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -f "$queue_file" ]; then
  echo "check-dispatch-queue: CANNOT-ASSESS — $queue_file is missing" >&2
  exit 2
fi
if [ ! -f "$snapshot_file" ]; then
  echo "check-dispatch-queue: CANNOT-ASSESS — $snapshot_file is missing (refresh it with:" \
    "python3 governance/dispatch/cli.py snapshot --from-github) — the committed queue's" \
    "exists/open half is checked against that artifact, so its absence is not a pass" >&2
  exit 2
fi

# The tolerance this consumer declares, read from ONE place, so the two halves
# of the check below cannot disagree about which age is tolerable (issue #1189).
tolerance_minutes=""
if ! tolerance_minutes="$(python3 -c 'import sys; sys.path.insert(0, "governance/dispatch"); import queue_freshness; print(queue_freshness.tolerance_minutes())' 2>&1)"; then
  echo "check-dispatch-queue: CANNOT-ASSESS — could not read the tolerated snapshot age from" \
    "governance/dispatch/queue_freshness.py: $tolerance_minutes" >&2
  exit 2
fi

fail=0
cannot=0

# --- 1. the tests -----------------------------------------------------------
if command -v pytest >/dev/null 2>&1; then
  if ! python3 -m pytest -q governance/dispatch/tests/test_queue.py \
    governance/dispatch/tests/test_queue_freshness.py; then
    echo "check-dispatch-queue: FAIL — the governance/dispatch queue/freshness suites are red" >&2
    fail=1
  fi
else
  echo "check-dispatch-queue: pytest not found, skipping the suites (the other checks still run)" >&2
fi

# --- 2. the input states its own age first (issue #1189) --------------------
# Asserted BEFORE the queue, because the defect this closes was an
# unattributable one: a committed artifact that had aged presented as a bare
# `snapshot-stale` CANNOT-ASSESS (which verify.sh records as SKIP), so the
# finding this check exists for stayed invisible — and the bound in force was a
# liveness bound the artifact could never meet. The assertion lives in
# governance/dispatch/queue_freshness.py, deliberately OUTSIDE `queue --check`:
# that verb's own `--stale-minutes` is a liveness bound its loop callers opt
# into, while the age THIS gate tolerates is a property of this consumer.
echo "== board freshness (the age this consumer tolerates) =="
python3 governance/dispatch/cli.py freshness --snapshot "$snapshot_file"
fresh_rc=$?
case "$fresh_rc" in
  0) ;;
  1)
    echo "check-dispatch-queue: FAIL — the committed board snapshot is outside the age this" \
      "consumer tolerates (see above), so every exists/open answer is against a stale frontier" >&2
    fail=1
    ;;
  *)
    echo "check-dispatch-queue: CANNOT-ASSESS — the committed board snapshot could not be assessed (see above)" >&2
    cannot=1
    ;;
esac

# --- 3. the committed queue validates against the committed board snapshot ---
# The tolerance read from queue_freshness.py is passed THROUGH to the verb that
# applies it: an artifact inside the declared tolerance is ASSESSED (the
# exists/open rule runs against it and a queued-but-closed issue is named by
# number), while one outside it has already been refused BY NAME by check 2.
# Declaring a tolerance the artifact cannot meet is what reduced this check to
# structural-only; this gate does not.
python3 governance/dispatch/cli.py queue --check --queue "$queue_file" \
  --snapshot "$snapshot_file" --stale-minutes "$tolerance_minutes"
queue_rc=$?
case "$queue_rc" in
  0) ;;
  2)
    echo "check-dispatch-queue: CANNOT-ASSESS — 'dispatch queue --check' could not assess the" \
      "committed queue against the board (see stderr above)" >&2
    cannot=1
    ;;
  *)
    echo "check-dispatch-queue: FAIL — 'dispatch queue --check' refused the committed queue (rc=$queue_rc)" >&2
    fail=1
    ;;
esac

# --- 4. provoked controls ----------------------------------------------------
# A check that cannot fail is a formality (GR-12 / AO-GR-19): every rule this
# gate applies is provoked here — a cycle, a queued-but-closed number, a dated
# artifact inside the tolerance, an aged one, an unaged one, one whose age is
# not a timestamp, a missing board, and a bad invocation — on hermetic fixtures
# in $tmp. `controls` is counted and asserted against `expected_controls`, so a
# control that silently stops running is a failure rather than a quiet
# reduction of the gate's own coverage.
echo "== provoked controls =="

tmp="$(mktemp -d "/tmp/ao877-dispatch-queue.$(printf 'X%.0s' 1 2 3 4 5 6)")"
trap 'rm -rf "$tmp"' EXIT

controls=0
unproven=0

# A refusal is read from a FILE, never through a pipe: `printf … | grep -q` under
# pipefail reports ABSENT for text that is PRESENT (measured in this repository).
check_refused() {  # rc needle label out
  local rc="$1"
  local needle="$2"
  local label="$3"
  local out="$4"
  controls=$((controls + 1))
  if [ "$rc" -eq 0 ]; then
    echo "  FAIL  control $label passed — it was not refused at all" >&2
    unproven=$((unproven + 1))
    return
  fi
  if ! grep -qF -- "$needle" "$out"; then
    echo "  FAIL  control $label refused without naming $needle" >&2
    sed 's/^/        /' "$out" >&2
    unproven=$((unproven + 1))
    return
  fi
  echo "  OK    control $label refused, naming $needle"
}

check_accepted() {  # rc label out
  local rc="$1"
  local label="$2"
  local out="$3"
  controls=$((controls + 1))
  if [ "$rc" -eq 0 ]; then
    echo "  OK    control $label accepted (the rule does not fail open)"
    return
  fi
  echo "  FAIL  control $label was refused (rc=$rc)" >&2
  sed 's/^/        /' "$out" >&2
  unproven=$((unproven + 1))
}

# Fixture 1: the structural rule — a cycle is a defect regardless of live state.
cat >"$tmp/cyclic-queue.yaml" <<'YAML'
waves:
  - name: wave-x
    issues: [101, 102]
blocked_by:
  101: [102]
  102: [101]
YAML

# Fixture 2: its clean twin, so the cycle control is not merely failing open.
cat >"$tmp/clean-queue.yaml" <<'YAML'
waves:
  - name: wave-x
    issues: [101, 102]
YAML

# Fixture 3: the rule that found this issue's own six findings — a queued number
# the board has since CLOSED. Provoked here so the detector of the real finding
# is itself falsifiable.
cat >"$tmp/closed-queue.yaml" <<'YAML'
waves:
  - name: wave-x
    issues: [101]
YAML

# Fixture board A: both fixture issues are OPEN here ...
cat >"$tmp/snapshot.json" <<'JSON'
{
  "generated_at": "2026-09-16T00:00:00Z",
  "source": "check-dispatch-queue fixture",
  "issues": [
    {"number": 101, "title": "fixture A", "state": "OPEN", "milestone": "", "labels": [], "parent": null, "blocked_by": [], "cross_refs": [], "closed_at": ""},
    {"number": 102, "title": "fixture B", "state": "OPEN", "milestone": "", "labels": [], "parent": null, "blocked_by": [], "cross_refs": [], "closed_at": ""}
  ]
}
JSON

# ... and fixture board B closes #101, which is what fixture 3 needs.
cat >"$tmp/snapshot-closed.json" <<'JSON'
{
  "generated_at": "2026-09-16T00:00:00Z",
  "source": "check-dispatch-queue fixture",
  "issues": [
    {"number": 101, "title": "fixture A (closed)", "state": "CLOSED", "milestone": "", "labels": [], "parent": null, "blocked_by": [], "cross_refs": [], "closed_at": "2026-09-16T01:00:00Z"}
  ]
}
JSON

python3 governance/dispatch/cli.py queue --check --queue "$tmp/cyclic-queue.yaml" \
  --snapshot "$tmp/snapshot.json" --stale-minutes 999999999 >"$tmp/cyclic.out" 2>&1
check_refused "$?" "cycle" "a cyclic queue" "$tmp/cyclic.out"

python3 governance/dispatch/cli.py queue --check --queue "$tmp/clean-queue.yaml" \
  --snapshot "$tmp/snapshot.json" --stale-minutes 999999999 >"$tmp/clean.out" 2>&1
check_accepted "$?" "a clean queue" "$tmp/clean.out"

python3 governance/dispatch/cli.py queue --check --queue "$tmp/closed-queue.yaml" \
  --snapshot "$tmp/snapshot-closed.json" --stale-minutes 999999999 >"$tmp/closed.out" 2>&1
check_refused "$?" "already closed" "a queue naming a closed issue" "$tmp/closed.out"

# Fixture boards for the freshness halves: only `generated_at` varies and `--now`
# is fixed, so each verdict is a property of the fixture and never of the wall
# clock this gate happens to run at.
write_freshness_fixture() {  # file generated-at-json-or-ABSENT
  local file="$1"
  local value="$2"
  if [ "$value" = "ABSENT" ]; then
    printf '{"source": "check-dispatch-queue fixture", "issues": []}\n' > "$file"
  else
    printf '{"generated_at": %s, "source": "check-dispatch-queue fixture", "issues": []}\n' \
      "$value" > "$file"
  fi
}

fresh_now="2026-09-18T15:00:00Z"

write_freshness_fixture "$tmp/fresh.json" '"2026-09-18T14:00:00Z"'
python3 governance/dispatch/cli.py freshness --snapshot "$tmp/fresh.json" \
  --now "$fresh_now" >"$tmp/fresh.out" 2>&1
check_accepted "$?" "a snapshot dated inside the tolerance" "$tmp/fresh.out"

write_freshness_fixture "$tmp/aged.json" '"2026-09-01T00:00:00Z"'
python3 governance/dispatch/cli.py freshness --snapshot "$tmp/aged.json" \
  --now "$fresh_now" >"$tmp/aged.out" 2>&1
check_refused "$?" "board-snapshot-stale" "a snapshot past the tolerance" "$tmp/aged.out"

write_freshness_fixture "$tmp/unaged.json" "ABSENT"
python3 governance/dispatch/cli.py freshness --snapshot "$tmp/unaged.json" \
  --now "$fresh_now" >"$tmp/unaged.out" 2>&1
check_refused "$?" "board-snapshot-unaged" "a snapshot carrying no age" "$tmp/unaged.out"

write_freshness_fixture "$tmp/garbled.json" '"not-a-timestamp"'
python3 governance/dispatch/cli.py freshness --snapshot "$tmp/garbled.json" \
  --now "$fresh_now" >"$tmp/garbled.out" 2>&1
check_refused "$?" "board-snapshot-unaged" "a snapshot whose age is not one" "$tmp/garbled.out"

# The gate's OWN two fail-closed halves, run as the whole script (not the verb):
# "absence must never be a pass" and "a bad invocation is not a verdict" are
# properties of the GATE. Both exit before any check runs, so neither recurses.
self="$root/scripts/check-dispatch-queue.sh"

bash "$self" --snapshot "$tmp/absent.json" --queue "$queue_file" >"$tmp/absent.out" 2>&1
absent_rc=$?
controls=$((controls + 1))
if [ "$absent_rc" -eq 2 ] && grep -qF -- "CANNOT-ASSESS" "$tmp/absent.out"; then
  echo "  OK    control an absent board snapshot is CANNOT-ASSESS (rc 2, never a pass)"
else
  echo "  FAIL  control an absent board snapshot returned rc=$absent_rc, not rc 2 CANNOT-ASSESS" >&2
  sed 's/^/        /' "$tmp/absent.out" >&2
  unproven=$((unproven + 1))
fi

bash "$self" --not-a-flag >"$tmp/badinv.out" 2>&1
badinv_rc=$?
controls=$((controls + 1))
if [ "$badinv_rc" -eq 2 ] && grep -qF -- "CANNOT-ASSESS" "$tmp/badinv.out"; then
  echo "  OK    control a bad invocation is CANNOT-ASSESS (rc 2, never a pass)"
else
  echo "  FAIL  control a bad invocation returned rc=$badinv_rc, not rc 2 CANNOT-ASSESS" >&2
  sed 's/^/        /' "$tmp/badinv.out" >&2
  unproven=$((unproven + 1))
fi

expected_controls=9
if [ "$controls" -ne "$expected_controls" ]; then
  echo "check-dispatch-queue: FAIL — expected $expected_controls controls, ran $controls" >&2
  unproven=$((unproven + 1))
fi

if [ "$unproven" -ne 0 ]; then
  echo "check-dispatch-queue: FAIL — $unproven control(s) did not behave as required" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "check-dispatch-queue: FAIL" >&2
  exit 1
fi

if [ "$cannot" -ne 0 ]; then
  echo "check-dispatch-queue: CANNOT-ASSESS (all $controls control(s) behaved; the committed board snapshot could not be assessed)"
  exit 2
fi

echo "check-dispatch-queue: OK (tests pass, committed queue validates against a board inside the declared tolerance, $controls control(s) provoked)"
exit 0
