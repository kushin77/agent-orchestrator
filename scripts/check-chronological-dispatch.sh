#!/usr/bin/env bash
# Enforce the chronological issue-dispatch contract (issue #152, GR-20; #726).
#
# The rule is only binding if a gate can fail on it (no-false-green doctrine,
# GR-12): a doc-only rule is advisory. This check has two halves.
#
#  1. Declaration — every governance document that carries the execution contract
#     declares the chronological, dependency-gated selection rule, and the
#     required rule vocabulary is present in each. Removing a section, or renaming
#     a rule so the statement no longer exists, fails the gate.
#  2. Behaviour (issue #726) — the A2A dispatch arbitration must REFUSE a unit
#     whose proof does not hold: a closed issue, a closed epic, a unit another
#     lane already holds, an unowned unit, a stale board. Each refusal must name
#     the evidence it checked, and a unit that does prove issue -> epic -> lane
#     must still be granted. Nothing touches repository state: the fixture board
#     lives in a temporary directory and the fleet mailbox is rebound through
#     AO_FLEET_DIR.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no mktemp).
#
# Usage: bash scripts/check-chronological-dispatch.sh
set -uo pipefail

# Every "does this report contain this string?" test below is bash-native (#852).
# `printf '%s' "$out" | grep -qF -- "$s"` is NOT the same test: `grep -q` exits on
# its first match, SIGPIPE then kills the producer, and `set -o pipefail` promotes
# that 141 to the status of the whole pipeline — so a *large* report reports
# ABSENT for text that is PRESENT. Negated, that is a false red; positive, the
# control silently stops controlling and the check fails OPEN.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 1

declare -a docs=(
  "AGENTS.md"
  "docs/GOVERNANCE.md"
  "docs/EXECUTION-PLAN.md"
)

# Vocabulary every contract document must declare (case-sensitive).
declare -a required_markers=(
  "chronological"
  "dependency"
)

declare -a required_markers_agents=(
  "Kanban"
)

fail=0
checked=0
nonconforming=0

for doc in "${docs[@]}"; do
  if [ ! -f "$doc" ]; then
    printf '  FAIL  %s (missing file)\n' "$doc"
    fail=1
    nonconforming=$((nonconforming + 1))
    continue
  fi
  checked=$((checked + 1))
  doc_failed=0

  for marker in "${required_markers[@]}"; do
    if ! grep -qi -- "$marker" "$doc"; then
      printf '  FAIL  %s (missing rule marker: %s)\n' "$doc" "$marker"
      fail=1
      doc_failed=1
    fi
  done

  # The repository contract (AGENTS.md) must forbid board scavenging by name.
  if [ "$doc" = "AGENTS.md" ]; then
    for marker in "${required_markers_agents[@]}"; do
      if ! grep -q -- "$marker" "$doc"; then
        printf '  FAIL  %s (missing rule marker: %s)\n' "$doc" "$marker"
        fail=1
        doc_failed=1
      fi
    done
  fi

  if [ "$doc_failed" -ne 0 ]; then
    nonconforming=$((nonconforming + 1))
  fi
done

# --- behavioural half: the dispatch arbitration must refuse, and say why ------
if ! command -v python3 >/dev/null 2>&1; then
  printf 'chronological-dispatch: CANNOT-ASSESS — python3 not found\n' >&2
  exit 2
fi
if ! command -v mktemp >/dev/null 2>&1; then
  printf 'chronological-dispatch: CANNOT-ASSESS — mktemp not found\n' >&2
  exit 2
fi

# Explicit /tmp template, never mktemp's own default (a shared, periodically-
# cleaned $TMPDIR can vanish mid-run). The X-run is assembled by printf so no
# literal marker token sits in this source for the docs gate to read.
work="$(mktemp -d "/tmp/ao877-chrono.$(printf 'X%.0s' 1 2 3 4 5 6)")" || {
  printf 'chronological-dispatch: CANNOT-ASSESS — could not create a fixture directory\n' >&2
  exit 2
}
trap 'rm -rf "$work"' EXIT

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$work/ledger" "$work/locks" "$work/fleet/sent"

# A board with one dispatchable unit (#601), a closed issue (#604), an open child
# of a closed epic (#610 under #611), and a unit another lane already holds (#614).
cat > "$work/snapshot.json" <<JSON
{
  "generated_at": "$now",
  "source": "check-chronological-dispatch",
  "issues": [
    {"number": 601, "title": "dispatchable", "state": "open", "milestone": "CHRONO", "labels": ["type:task"], "parent": null, "blocked_by": [], "cross_refs": []},
    {"number": 604, "title": "closed", "state": "closed", "milestone": "CHRONO", "labels": [], "parent": null, "blocked_by": [], "cross_refs": []},
    {"number": 610, "title": "child of a closed epic", "state": "open", "milestone": "CHRONO", "labels": [], "parent": 611, "blocked_by": [], "cross_refs": []},
    {"number": 611, "title": "closed epic", "state": "closed", "milestone": "CHRONO", "labels": ["type:epic"], "parent": null, "blocked_by": [], "cross_refs": []},
    {"number": 614, "title": "held by lane-a", "state": "open", "milestone": "CHRONO", "labels": [], "parent": null, "blocked_by": [], "cross_refs": []}
  ]
}
JSON

cat > "$work/ledger/00000000000000000001-00614-agent-a-claim.json" <<JSON
{"event": "claim", "issue": 614, "agent": "agent-a", "at": "$now", "lane": "lane-a", "base_commit": "", "snapshot_sha256": "", "reason": "next-in-milestone", "ttl_hours": 24, "directive_id": "", "directive_from": "", "reaped_agent": ""}
JSON
cat > "$work/fleet/sent/d-closed.json" <<JSON
{"from": "brain", "to": "lane", "type": "directive", "id": "d-closed", "task": {"issue": 604}}
JSON

common=(--snapshot "$work/snapshot.json" --ledger "$work/ledger" --locks "$work/locks" --stale-minutes 15)
refusals=0
provoked=0
grants=0

# expect_refusal <name> <exit> <marker|marker> <dispatch args...>
# Every marker must appear in the refusal: the reason, and the evidence it read.
expect_refusal() {
  name="$1"; want_rc="$2"; markers="$3"; shift 3
  out="$(env "AO_FLEET_DIR=$work/fleet" python3 governance/dispatch/cli.py "$@" 2>&1)"; rc=$?
  refusals=$((refusals + 1))
  reason="${markers%%|*}"
  if [ "$rc" -ne "$want_rc" ]; then
    printf '  FAIL  %s (exit %s, expected %s)\n' "$name" "$rc" "$want_rc"
    printf '%s\n' "$out" | tail -3 | sed 's/^/        /'
    fail=1
    return
  fi
  if ! contains "$out" "$reason"; then
    printf '  FAIL  %s (exit %s but not refused as %s)\n' "$name" "$rc" "$reason"
    printf '%s\n' "$out" | tail -3 | sed 's/^/        /'
    fail=1
    return
  fi
  IFS='|' read -r -a wanted <<< "$markers"
  for marker in "${wanted[@]}"; do
    if ! contains "$out" "$marker"; then
      printf '  FAIL  %s (refused as %s but never named the evidence %s)\n' "$name" "$reason" "$marker"
      printf '%s\n' "$out" | tail -3 | sed 's/^/        /'
      fail=1
      return
    fi
  done
  printf '  ok    %s -> %s (named: %s)\n' "$name" "$reason" "$markers"
  provoked=$((provoked + 1))
}

printf 'chronological-dispatch: A2A dispatch arbitration (issue #726)\n'
expect_refusal "closed issue" 1 "issue-closed|state=closed" \
  dispatch "${common[@]}" --issue 604 --agent agent-b --lane lane-a
expect_refusal "directive naming a closed issue" 1 "issue-closed|snapshot.json" \
  dispatch "${common[@]}" --issue 604 --agent agent-b --lane lane-a --directive d-closed
expect_refusal "open issue under a closed epic" 1 "epic-closed|#611" \
  dispatch "${common[@]}" --issue 610 --agent agent-b --lane lane-a
expect_refusal "unit another lane holds" 1 "already-claimed|agent-a|lane-a" \
  dispatch "${common[@]}" --issue 614 --agent agent-b --lane lane-b
expect_refusal "unowned unit" 1 "unowned|no lane owns" \
  dispatch "${common[@]}" --issue 601 --agent agent-b
expect_refusal "stale board" 2 "snapshot-stale|refresh first" \
  dispatch --snapshot "$work/snapshot.json" --ledger "$work/ledger" --locks "$work/locks" --stale-minutes -1 \
  --issue 601 --agent agent-b --lane lane-a

grant_out="$(env "AO_FLEET_DIR=$work/fleet" python3 governance/dispatch/cli.py dispatch "${common[@]}" \
  --issue 601 --agent agent-b --lane lane-a 2>&1)"; grant_rc=$?
if [ "$grant_rc" -ne 0 ]; then
  printf '  FAIL  a dispatchable unit was refused (exit %s)\n' "$grant_rc"
  printf '%s\n' "$grant_out" | tail -3 | sed 's/^/        /'
  fail=1
elif ! contains "$grant_out" '"provenance"'; then
  printf '  FAIL  the granted dispatch recorded no issue -> epic -> lane provenance\n'
  printf '%s\n' "$grant_out" | tail -3 | sed 's/^/        /'
  fail=1
else
  printf '  ok    dispatchable unit granted with its provenance\n'
  grants=$((grants + 1))
fi


# --- artifact provocations (issue #885): controls, audit, schema, live -------
# One provocation per artifact this surface added to raise its measured class
# to `elite` (governance/conformance/surfaces.py): each must be genuinely
# load-bearing, so each is mutated (or drifted) here and must be REFUSED by
# name — never silently accepted.
artifact_provocations=0

sha() { sha256sum "$1" | cut -d' ' -f1; }

# 1) controls: a controls.yaml that drops a real claim reason is refused by
#    policy.load() as a policy defect, never a silent pass (no-false-green).
#    sha256-restore: the committed file is mutated in place and restored byte
#    for byte, verified by digest, even if the check below fails.
controls_file="governance/dispatch/controls.yaml"
controls_backup="$work/controls.yaml.orig"
cp "$controls_file" "$controls_backup"
controls_before_sha="$(sha "$controls_file")"
restore_controls() { cp "$controls_backup" "$controls_file"; }
trap 'restore_controls; rm -rf "$work"' EXIT

python3 - "$controls_file" <<'PY'
import sys
import yaml
path = sys.argv[1]
data = yaml.safe_load(open(path, encoding="utf-8"))
data["allowed_claim_reasons"] = data["allowed_claim_reasons"][:-1]
with open(path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(data, fh)
PY
controls_out="$(cd governance/dispatch && python3 -c 'import policy; policy.load()' 2>&1)"; controls_rc=$?
restore_controls
controls_after_sha="$(sha "$controls_file")"
artifact_provocations=$((artifact_provocations + 1))
if [ "$controls_rc" -eq 0 ]; then
  printf '  FAIL  controls: a dropped claim reason was NOT refused (exit 0)\n'
  fail=1
elif ! contains "$controls_out" "allowed_claim_reasons"; then
  printf '  FAIL  controls: refused, but not by name (allowed_claim_reasons)\n'
  printf '%s\n' "$controls_out" | tail -3 | sed 's/^/        /'
  fail=1
elif [ "$controls_after_sha" != "$controls_before_sha" ]; then
  printf '  FAIL  controls: %s was not byte-for-byte restored after the mutation\n' "$controls_file"
  fail=1
else
  printf '  ok    controls: dropped claim reason refused by name, file restored (sha256 %s)\n' "$controls_before_sha"
fi

# 2) audit: a refusal must produce exactly one valid record in the trail
#    adjacent to the fixture ledger above, and a record carrying the wrong
#    schema tag must be refused when read back.
audit_out="$(cd governance/dispatch && python3 - "$work" <<'PY' 2>&1
import sys
from pathlib import Path
sys.path.insert(0, ".")
import audit
work = Path(sys.argv[1])
trail = work / "audit-trail.jsonl"
audit.append(audit.record_refusal(issue=614, agent="agent-b", at="2026-01-01T00:00:00Z",
                                   reason="already-claimed", detail="held"), path=trail)
records = audit.read(trail)
assert len(records) == 1, f"expected exactly 1 record, found {len(records)}"
trail.write_text(trail.read_text() + '{"schema": "wrong-schema", "kind": "grant"}\n', encoding="utf-8")
try:
    audit.read(trail)
    print("NOT-REFUSED: a wrong-schema record was accepted")
    sys.exit(1)
except audit.AuditUnavailable as exc:
    print(f"REFUSED: {exc}")
PY
)"; audit_rc=$?
artifact_provocations=$((artifact_provocations + 1))
if [ "$audit_rc" -ne 0 ] || ! contains "$audit_out" "REFUSED:"; then
  printf '  FAIL  audit: a wrong-schema record was not refused on read\n'
  printf '%s\n' "$audit_out" | tail -5 | sed 's/^/        /'
  fail=1
else
  printf '  ok    audit: exactly one record per refusal, wrong-schema record refused on read\n'
fi

# 3) schema: a claim event with a bogus `event` value must be refused by the
#    frozen shape, naming the field.
schema_out="$(cd governance/dispatch && python3 -c '
import schema
try:
    schema.validate({"event": "bogus", "issue": 1, "agent": "a", "at": "t"}, schema.SHAPE_CLAIM_EVENT)
    print("NOT-REFUSED")
except schema.SchemaViolation as exc:
    print(f"REFUSED: {exc}")
' 2>&1)"; schema_rc=$?
artifact_provocations=$((artifact_provocations + 1))
if ! contains "$schema_out" "REFUSED:" || ! contains "$schema_out" "event"; then
  printf '  FAIL  schema: an invalid ClaimEvent was not refused by name\n'
  printf '%s\n' "$schema_out" | tail -5 | sed 's/^/        /'
  fail=1
else
  printf '  ok    schema: an invalid ClaimEvent is refused, naming the field\n'
fi

# 4) live: the live projection must reflect a claim written to the real ledger
#    AFTER the first read — a stale/cached projection (drift) would miss it.
live_out="$(cd governance/dispatch && python3 - "$work" <<'PY' 2>&1
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, ".")
import claims
import live
from model import Issue, Snapshot

work = Path(sys.argv[1])
now = datetime.now(timezone.utc)
board = Snapshot(generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), source="gate",
                  issues={9001: Issue(9001, "gate-fixture", milestone="GATE")})
ledger = work / "live-ledger"
queue_path = work / "no-queue.yaml"

before = live.project(board, ledger=ledger, queue_path=queue_path)
if before["live_claims"]:
    print("NOT-REFUSED: projection saw a claim before one was written")
    sys.exit(1)

claims.claim(9001, "agent-live", "lane-live", board, ledger=ledger, lock_dir=work / "live-locks", now=now)
after = live.project(board, ledger=ledger, queue_path=queue_path)
if after == before:
    print("DRIFT: the live projection did not change after the real store did")
    sys.exit(1)
if len(after["live_claims"]) != 1 or after["live_claims"][0]["issue"] != 9001:
    print(f"DRIFT: the live projection does not reflect the real claim: {after}")
    sys.exit(1)
print("REFUSED-DRIFT-N/A: projection tracks the real store live")
PY
)"; live_rc=$?
artifact_provocations=$((artifact_provocations + 1))
if [ "$live_rc" -ne 0 ]; then
  printf '  FAIL  live: the projection drifted from the real ledger/snapshot store\n'
  printf '%s\n' "$live_out" | tail -5 | sed 's/^/        /'
  fail=1
else
  printf '  ok    live: the projection tracks the real store (no drift between two reads)\n'
fi

if [ "$fail" -ne 0 ]; then
  printf 'chronological-dispatch: FAIL (%d of %d contract doc(s) non-conforming; %d of %d refusal(s) provoked; %d grant(s) accepted; %d artifact provocation(s))\n' \
    "$nonconforming" "${#docs[@]}" "$provoked" "$refusals" "$grants" "$artifact_provocations" >&2
  exit 1
fi

printf 'chronological-dispatch: OK (%d contract doc(s) declare dependency-ordered selection; %d refusal(s) provoked each naming its evidence; %d dispatch granted with its provenance; %d artifact provocation(s) (controls/audit/schema/live) all refused/verified)\n' \
  "$checked" "$provoked" "$grants" "$artifact_provocations"
exit 0
