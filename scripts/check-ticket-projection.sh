#!/usr/bin/env bash
# check-ticket-projection.sh — the ticket projection must be deterministic,
# rebuildable and authority-correct (issue #401, ADR-0014).
#
# The projection joins the board snapshot, the claim ledger, the lessons
# register, the budget rail and the gate attestation onto one ticket node. Three
# properties make it a join rather than a second source of truth: two builds over
# one revision are byte-identical, the projected store can be deleted and rebuilt
# from the ledgers to the same hash, and every populated authority-tracked field
# has exactly one writer (the one contract v2 pins).
#
# The board snapshot is that join's *input*, and it is a committed point-in-time
# artifact: a clean checkout can therefore resolve against a frontier the board
# has already left. This gate states the age the projection tolerates and refuses
# beyond it (issue #1077) — the consumer half of the freshness rule RCA-0014
# prescribed (`governance/ticket/freshness.py` carries the tolerance, the
# measurement behind it, and why it differs from the dispatch liveness one).
#
# The gate does not merely assert those properties; it PROVOKES them. Each
# control below mutates an input, requires the projection to refuse, and requires
# the refusal to name the offender:
#
#   * a field written by two producers;
#   * a field written by a producer that is not its authority;
#   * a lesson whose issue origin is absent from the board;
#   * a claim for an issue the board does not carry;
#   * a budget receipt no evidence receipt backs;
#   * a ticket no ledger supplies a field for;
#   * a build that embeds a timestamp (so two builds differ);
#   * a board snapshot older than the tolerance this consumer declares;
#   * a board snapshot with no `generated_at` at all, and one whose
#     `generated_at` is not a timestamp (both fail closed).
#
# If any control passes, this gate reports FAIL: a check that cannot fail is a
# formality (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-ticket-projection.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-ticket-projection: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in governance/ticket/cli.py docs/contracts/paperclip/ticket.schema.json; do
  if [ ! -e "$required" ]; then
    echo "check-ticket-projection: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done
if [ ! -e ".board/snapshot.json" ]; then
  echo "check-ticket-projection: CANNOT-ASSESS — .board/snapshot.json is missing" >&2
  exit 2
fi

store=".verify/ticket/tickets.json"

# --- input freshness: the age this consumer tolerates (issue #1077) ----------
# Asserted BEFORE the projection, because the whole defect this closes was an
# unattributable red: a snapshot that has aged presents as `reference-unresolved`
# ("a ledger names an issue the board does not carry"), which reads as a bad
# ledger entry rather than as a stale input, and it reddened two gates of record
# for two days without naming a remedy. Here the input states its own age first.
# The assertion is deliberately OUTSIDE the projection — a build that read the
# clock could not be rebuilt byte-identically, so `build()`/`verify()` stay pure.
# See governance/ticket/freshness.py for the tolerance and the measured cadence
# behind it.
echo "== board freshness =="
# --refresh: self-heal (issue #1692) — try the one refresh verb before failing,
# so master never drifts past the tolerance while the fleet can still reach
# GitHub; a genuinely offline/unauthenticated box still fails, naming why.
if ! python3 governance/ticket/cli.py freshness --refresh; then
  echo "check-ticket-projection: FAIL — the committed board snapshot is outside the age" \
    "this consumer tolerates (see above), so every reference the projection resolves is" \
    "against a stale frontier" >&2
  exit 1
fi

echo "== projection =="
if ! python3 governance/ticket/cli.py project; then
  echo "check-ticket-projection: FAIL — the projection refuses to build (see above)" >&2
  exit 1
fi
if ! python3 governance/ticket/cli.py verify; then
  echo "check-ticket-projection: FAIL — the projection does not rebuild from the ledgers" >&2
  exit 1
fi

scratch="/tmp/ao401tp.$$.$(date +%s)"
if ! mkdir -p "$scratch" 2>/dev/null; then
  echo "check-ticket-projection: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

# --- determinism: two builds over one revision must be byte-identical --------
echo "== determinism =="
if ! python3 governance/ticket/cli.py project --out "$scratch/a.json" >/dev/null; then
  echo "check-ticket-projection: FAIL — the first build failed" >&2
  exit 1
fi
if ! python3 governance/ticket/cli.py project --out "$scratch/b.json" >/dev/null; then
  echo "check-ticket-projection: FAIL — the second build failed" >&2
  exit 1
fi
if ! cmp -s "$scratch/a.json" "$scratch/b.json"; then
  echo "check-ticket-projection: FAIL — two builds over one revision differ" >&2
  exit 1
fi
if ! cmp -s "$scratch/a.json" "$store"; then
  echo "check-ticket-projection: FAIL — the committed store is not a rebuild's output" >&2
  exit 1
fi
echo "  OK    two builds over one revision are byte-identical"

# --- negative controls -------------------------------------------------------
echo "== negative controls =="

controls=0
unproven=0

check_refused() {
  local label="$1"
  local needle="$2"
  shift 2
  controls=$((controls + 1))
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '  FAIL  control %s passed — the projection did not refuse it\n' "$label" >&2
    unproven=$((unproven + 1))
    return
  fi
  if ! printf '%s\n' "$out" | grep -qF -- "$needle"; then
    printf '  FAIL  control %s refused without naming %s\n' "$label" "$needle" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    unproven=$((unproven + 1))
    return
  fi
  printf '  OK    control %-32s refused, naming %s\n' "$label" "$needle"
}

# A ticket that actually carries a goal, so a second writer really is a second
# writer rather than the first one.
writer_ticket="$(python3 - "$store" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
for ticket in document.get("tickets", []):
    if "goal" in ticket and ticket.get("id"):
        print(ticket["id"])
        break
PY
)"
if [ -z "$writer_ticket" ]; then
  echo "check-ticket-projection: CANNOT-ASSESS — no ticket carries a goal to mutate" >&2
  exit 2
fi

cat > "$scratch/two-writers.json" <<JSON
[{"ticket": "$writer_ticket", "field": "goal", "producer": "rogue/lane", "value": "#999", "where": "negative-control"}]
JSON
check_refused "two-writer field" "$writer_ticket.goal" \
  python3 governance/ticket/cli.py project --negative-control "$scratch/two-writers.json"

cat > "$scratch/mismatch.json" <<JSON
[{"ticket": "$writer_ticket", "field": "owner", "producer": "governance/dispatch", "value": "agent-x", "where": "negative-control"}]
JSON
check_refused "writer is not the authority" "$writer_ticket.owner" \
  python3 governance/ticket/cli.py project --negative-control "$scratch/mismatch.json"

cat > "$scratch/unbacked.json" <<'JSON'
[{"ticket": "kushin77/agent-orchestrator#999999", "field": null, "producer": "rogue/lane", "value": null, "where": "negative-control"}]
JSON
check_refused "ticket no ledger backs" \
  "kushin77/agent-orchestrator#999999" \
  python3 governance/ticket/cli.py project --negative-control "$scratch/unbacked.json"

# A minimal fixture root: the real frozen schema and the real board, plus the
# one ledger under test.
make_fixture() {
  local dir="$1"
  mkdir -p "$dir/docs/contracts/paperclip" "$dir/.board" \
    "$dir/governance/lessons" "$dir/telemetry/budgets"
  cp docs/contracts/paperclip/ticket.schema.json \
    "$dir/docs/contracts/paperclip/ticket.schema.json"
  cp .board/snapshot.json "$dir/.board/snapshot.json"
}

make_fixture "$scratch/lesson"
cat > "$scratch/lesson/governance/lessons/ledger.jsonl" <<'JSON'
{"id": "INC-0001", "kind": "incident", "origin": {"kind": "issue", "ref": "#999999"}}
JSON
check_refused "unresolvable lesson edge" "governance/lessons/ledger.jsonl:1" \
  python3 governance/ticket/cli.py project --root "$scratch/lesson"

make_fixture "$scratch/claim"
mkdir -p "$scratch/claim/.board/claims"
cat > "$scratch/claim/.board/claims/0001-999999-agent-claim.json" <<'JSON'
{"event": "claim", "issue": 999999, "agent": "agent", "at": "2026-09-14T00:00:00Z"}
JSON
check_refused "claim for an unknown issue" \
  ".board/claims/0001-999999-agent-claim.json" \
  python3 governance/ticket/cli.py project --root "$scratch/claim"

make_fixture "$scratch/budget"
first_issue="$(python3 - "$scratch/budget/.board/snapshot.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
print(document["issues"][0]["number"])
PY
)"
printf '{"ticket": "#%s", "scope": {"level": "agent", "id": "paperclip"}, "spent": 1.0, "cap": 2.0, "receipt": "no-such-receipt"}\n' \
  "$first_issue" > "$scratch/budget/telemetry/budgets/ledger.jsonl"
check_refused "budget receipt nothing backs" \
  "kushin77/agent-orchestrator#$first_issue" \
  python3 governance/ticket/cli.py project --root "$scratch/budget"

# A build that stamps a timestamp cannot be deterministic, and `verify` (which
# never stamps) must notice.
if python3 governance/ticket/cli.py project --stamp 1 --out "$scratch/s1.json" >/dev/null 2>&1 \
  && python3 governance/ticket/cli.py project --stamp 2 --out "$scratch/s2.json" >/dev/null 2>&1; then
  if cmp -s "$scratch/s1.json" "$scratch/s2.json"; then
    echo "  FAIL  control non-deterministic build: two stamped builds are identical" >&2
    unproven=$((unproven + 1))
  else
    echo "  OK    control non-deterministic build         two stamped builds differ"
  fi
else
  echo "  FAIL  control non-deterministic build: a stamped build failed outright" >&2
  unproven=$((unproven + 1))
fi
check_refused "build that embeds a timestamp" "generated_at" \
  python3 governance/ticket/cli.py verify --out "$scratch/s1.json"

# The freshness controls mutate ONLY `generated_at`, on a hermetic fixture, and
# fix `--now` — so the verdict is a property of the fixture, never of the wall
# clock the gate happens to run at.
write_freshness_fixture() {  # dir generated-at-json-or-ABSENT
  local dir="$1"
  local value="$2"
  mkdir -p "$dir/.board"
  if [ "$value" = "ABSENT" ]; then
    printf '{"source": "kushin77/agent-orchestrator", "issues": []}\n' \
      > "$dir/.board/snapshot.json"
  else
    printf '{"generated_at": %s, "source": "kushin77/agent-orchestrator", "issues": []}\n' \
      "$value" > "$dir/.board/snapshot.json"
  fi
}

fresh_now="2026-09-17T13:00:00Z"

write_freshness_fixture "$scratch/fresh" '"2026-09-17T12:00:00Z"'
controls=$((controls + 1))
if python3 governance/ticket/cli.py freshness --root "$scratch/fresh" \
  --now "$fresh_now" >/dev/null 2>&1; then
  echo "  OK    control a dated snapshot inside the tolerance is accepted"
else
  echo "  FAIL  control a dated snapshot inside the tolerance was refused" >&2
  unproven=$((unproven + 1))
fi

write_freshness_fixture "$scratch/aged" '"2026-09-01T00:00:00Z"'
check_refused "board snapshot past the tolerance" "board-snapshot-stale" \
  python3 governance/ticket/cli.py freshness --root "$scratch/aged" --now "$fresh_now"

write_freshness_fixture "$scratch/unaged" "ABSENT"
check_refused "board snapshot that carries no age" "board-snapshot-unaged" \
  python3 governance/ticket/cli.py freshness --root "$scratch/unaged" --now "$fresh_now"

write_freshness_fixture "$scratch/garbled" '"not-a-timestamp"'
check_refused "board snapshot whose age is not one" "board-snapshot-unaged" \
  python3 governance/ticket/cli.py freshness --root "$scratch/garbled" --now "$fresh_now"

expected_controls=11
if [ "$controls" -ne "$expected_controls" ]; then
  echo "check-ticket-projection: FAIL — expected $expected_controls controls, ran $controls" >&2
  unproven=$((unproven + 1))
fi

if [ "$unproven" -ne 0 ]; then
  echo "check-ticket-projection: FAIL — $unproven negative control(s) did not refuse" >&2
  exit 1
fi

echo "check-ticket-projection: OK — deterministic, rebuildable, one writer per field, $controls control(s) refused"
exit 0
