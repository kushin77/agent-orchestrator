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

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# mktemp's own template (it honours $TMPDIR): an explicit X-run in this source
# would be read as an unfinished marker by the docs gate.
work="$(mktemp -d)" || {
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
  if ! printf '%s' "$out" | grep -qF -- "$reason"; then
    printf '  FAIL  %s (exit %s but not refused as %s)\n' "$name" "$rc" "$reason"
    printf '%s\n' "$out" | tail -3 | sed 's/^/        /'
    fail=1
    return
  fi
  IFS='|' read -r -a wanted <<< "$markers"
  for marker in "${wanted[@]}"; do
    if ! printf '%s' "$out" | grep -qF -- "$marker"; then
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
elif ! printf '%s' "$grant_out" | grep -qF -- '"provenance"'; then
  printf '  FAIL  the granted dispatch recorded no issue -> epic -> lane provenance\n'
  printf '%s\n' "$grant_out" | tail -3 | sed 's/^/        /'
  fail=1
else
  printf '  ok    dispatchable unit granted with its provenance\n'
  grants=$((grants + 1))
fi

if [ "$fail" -ne 0 ]; then
  printf 'chronological-dispatch: FAIL (%d of %d contract doc(s) non-conforming; %d of %d refusal(s) provoked; %d grant(s) accepted)\n' \
    "$nonconforming" "${#docs[@]}" "$provoked" "$refusals" "$grants" >&2
  exit 1
fi

printf 'chronological-dispatch: OK (%d contract doc(s) declare dependency-ordered selection; %d refusal(s) provoked each naming its evidence; %d dispatch granted with its provenance)\n' \
  "$checked" "$provoked" "$grants"
exit 0
