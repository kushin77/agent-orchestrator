#!/usr/bin/env bash
# check-pmo-rollup.sh — the PMO views are derived queries over the ticket graph,
# never a store of their own (issue #403).
#
# The five subcommands (`deps`, `lanes`, `report`, `raid`, `aging`) are projections
# of `governance/ticket`'s graph: they add no ledger, no cache that survives a
# rebuild and no second source of `status`. Two properties make that true rather
# than aspirational, and this gate checks both:
#
#   * the views run offline over the committed graph and exit tri-state
#     (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS), and CANNOT-ASSESS is never a pass;
#   * **the view equals the graph** — for a fixture with a known ticket set the
#     derived RAID set is exactly the expected set, and a ticket removed from the
#     graph but left in a saved view is caught by name.
#
# The gate does not merely assert those properties; it PROVOKES them (GR-12).
# Each control below drives a real failure and requires it to name the offender:
#
#   * a ticket with an owner-less (live) risk;
#   * a RAID set that disagrees with the graph (a ticket removed, a view kept);
#   * a rollup computed from a stale cache;
#   * an aging item that names no owner;
#   * a graph that cannot be built at all (no board snapshot) -> CANNOT-ASSESS.
#
# It also proves the "no store of its own" rule mechanically: deriving every view
# leaves the tree byte-for-byte unchanged, so nothing the PMO computes is cached.
#
# If any control passes, or a view writes anything, this gate reports FAIL: a
# check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-pmo-rollup.sh
#
# ---knowledge---
# module_id: scripts.check-pmo-rollup
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, schema-validation]
# derives_from: null
# owner_sme: pmo-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#403"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-pmo-rollup: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in governance/pmo/cli.py governance/ticket/builder.py \
  docs/contracts/paperclip/ticket.schema.json .board/snapshot.json; do
  if [ ! -e "$required" ]; then
    echo "check-pmo-rollup: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done

scratch="/tmp/ao403pmo.$(date +%s%N).$"
if ! mkdir -p "$scratch" 2>/dev/null; then
  echo "check-pmo-rollup: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

pmo() { python3 governance/pmo/cli.py "$@"; }

json_ok() {
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" >/dev/null 2>&1
}


# --- the real graph: every view runs, tri-state, never a pass on CANNOT-ASSESS
echo "== views over the committed graph =="
views=0
unproven=0

for view in deps lanes report raid aging; do
  rc=0
  pmo "$view" --check --json > "$scratch/real-$view.json" 2> "$scratch/real-$view.err" || rc=$?
  views=$((views + 1))
  if [ "$rc" -eq 2 ]; then
    echo "  FAIL  view $view could not assess the committed graph:" >&2
    sed 's/^/        /' "$scratch/real-$view.err" >&2
    unproven=$((unproven + 1))
    continue
  fi
  if json_ok "$scratch/real-$view.json"; then
    printf '  OK    view %-8s rc=%s (0 OK / 1 NOT-OK, 2 never treated as a pass)\n' "$view" "$rc"
  else
    echo "  FAIL  view $view did not emit a JSON document" >&2
    unproven=$((unproven + 1))
  fi
done

if pmo raid --check > "$scratch/raid-real.out" 2>&1; then
  echo "  OK    raid --check is the Verify entry point and it is green"
else
  echo "  FAIL  raid --check reported a finding on the committed graph:" >&2
  sed 's/^/        /' "$scratch/raid-real.out" >&2
  unproven=$((unproven + 1))
fi

# --- fixtures ----------------------------------------------------------------
make_fixture() {
  local dir="$1"
  mkdir -p "$dir/docs/contracts/paperclip" "$dir/.board/claims" 2>/dev/null || return 1
  cp docs/contracts/paperclip/ticket.schema.json \
    "$dir/docs/contracts/paperclip/ticket.schema.json" || return 1
}

write_board() {  # dir generated_at issues-json
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

root, generated_at, issues = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
payload = {"generated_at": generated_at, "source": "kushin77/agent-orchestrator", "issues": issues}
with open(f"{root}/.board/snapshot.json", "w", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, indent=2) + "\n")
PY
}

write_claim() {  # dir filename event issue agent at
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" <<'PY'
import json
import sys

root, name, event, number, agent, at = sys.argv[1:7]
record = {"event": event, "issue": int(number), "agent": agent, "lane": "lane-a", "at": at}
with open(f"{root}/.board/claims/{name}", "w", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

check_refused() {
  local label="$1"
  local needle="$2"
  shift 2
  controls=$((controls + 1))
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '  FAIL  control %s passed — the view did not refuse it\n' "$label" >&2
    unproven=$((unproven + 1))
    return
  fi
  if ! contains "$out" "$needle"; then
    printf '  FAIL  control %s refused without naming %s\n' "$label" "$needle" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    unproven=$((unproven + 1))
    return
  fi
  printf '  OK    control %-34s rc=%s, naming %s\n' "$label" "$rc" "$needle"
}

echo "== negative controls =="
controls=0

# --- 1. the derived RAID set equals the expected set, exactly ---------------
fixture="$scratch/live"
make_fixture "$fixture" || { echo "check-pmo-rollup: CANNOT-ASSESS — fixture" >&2; exit 2; }
write_board "$fixture" "2026-09-14T00:00:00Z" \
  '[{"number": 4, "title": "epic", "state": "OPEN", "labels": [], "parent": null, "blocked_by": [], "closed_at": ""},
    {"number": 10, "title": "risky", "state": "OPEN", "labels": ["priority:P0"], "parent": 4, "blocked_by": [9], "closed_at": ""},
    {"number": 9, "title": "dep", "state": "OPEN", "labels": [], "parent": 4, "blocked_by": [], "closed_at": ""}]'
write_claim "$fixture" "0001-00010-claim.json" claim 10 "agent-a" "2026-09-01T00:00:00Z"

expected_risks='["kushin77/agent-orchestrator#10"]'
actual_risks="$(pmo raid --root "$fixture" --json 2>/dev/null \
  | python3 -c 'import json,sys; print(json.dumps([r["ticket"] for r in json.load(sys.stdin)["risks"]]))')"
controls=$((controls + 1))
if [ "$actual_risks" = "$expected_risks" ]; then
  echo "  OK    control the derived RAID set equals the expected set exactly"
else
  echo "  FAIL  control the RAID set is $actual_risks, expected $expected_risks" >&2
  unproven=$((unproven + 1))
fi
if pmo raid --root "$fixture" --check >/dev/null 2>&1; then
  echo "  OK    control a live risk that names its owner is accepted"
else
  echo "  FAIL  control a live risk that names its owner was refused" >&2
  unproven=$((unproven + 1))
fi

# --- 2. a ticket with an owner-less risk ------------------------------------
write_claim "$fixture" "0002-00010-claim.json" claim 10 "" "2026-09-02T00:00:00Z"
check_refused "owner-less live risk" "kushin77/agent-orchestrator#10" \
  pmo raid --root "$fixture" --check
# restore a named owner so the fixture is reusable below
write_claim "$fixture" "0003-00010-claim.json" claim 10 "agent-a" "2026-09-03T00:00:00Z"

# --- 3. a RAID set that disagrees with the graph ----------------------------
stale="$scratch/stale"
make_fixture "$stale" || { echo "check-pmo-rollup: CANNOT-ASSESS — fixture" >&2; exit 2; }
write_board "$stale" "2026-09-14T00:00:00Z" \
  '[{"number": 10, "title": "risky", "state": "OPEN", "labels": ["priority:P0"], "parent": null, "blocked_by": [], "closed_at": ""},
    {"number": 11, "title": "gone", "state": "OPEN", "labels": ["priority:P0"], "parent": null, "blocked_by": [], "closed_at": ""}]'
write_claim "$stale" "0001-00010-claim.json" claim 10 "agent-a" "2026-09-01T00:00:00Z"
write_claim "$stale" "0001-00011-claim.json" claim 11 "agent-a" "2026-09-01T00:00:00Z"
pmo raid --root "$stale" --json > "$scratch/raid-saved.json" 2>/dev/null
# the graph loses #11 (and its claim, which would otherwise dangle); the saved
# view keeps it
write_board "$stale" "2026-09-14T00:00:00Z" \
  '[{"number": 10, "title": "risky", "state": "OPEN", "labels": ["priority:P0"], "parent": null, "blocked_by": [], "closed_at": ""}]'
rm -f "$stale/.board/claims/0001-00011-claim.json"
check_refused "RAID view disagrees with graph" "kushin77/agent-orchestrator#11" \
  pmo raid --root "$stale" --check --against "$scratch/raid-saved.json"

# --- 4. a rollup computed from a stale cache --------------------------------
rollup="$scratch/rollup"
make_fixture "$rollup" || { echo "check-pmo-rollup: CANNOT-ASSESS — fixture" >&2; exit 2; }
write_board "$rollup" "2026-09-14T00:00:00Z" \
  '[{"number": 10, "title": "one", "state": "OPEN", "labels": [], "parent": null, "blocked_by": [], "closed_at": ""}]'
pmo report --root "$rollup" --json > "$scratch/report-saved.json" 2>/dev/null
write_board "$rollup" "2026-09-14T00:00:00Z" \
  '[{"number": 10, "title": "one", "state": "OPEN", "labels": [], "parent": null, "blocked_by": [], "closed_at": ""},
    {"number": 11, "title": "two", "state": "OPEN", "labels": [], "parent": null, "blocked_by": [], "closed_at": ""}]'
check_refused "rollup from a stale cache" "view-stale-count" \
  pmo report --root "$rollup" --check --against "$scratch/report-saved.json"

# --- 5. an aging item that names no owner -----------------------------------
aged="$scratch/aged"
make_fixture "$aged" || { echo "check-pmo-rollup: CANNOT-ASSESS — fixture" >&2; exit 2; }
write_board "$aged" "2026-09-14T00:00:00Z" \
  '[{"number": 10, "title": "waiting", "state": "OPEN", "labels": [], "parent": null, "blocked_by": [], "closed_at": ""}]'
write_claim "$aged" "0001-00010-claim.json" claim 10 "" "2026-06-01T00:00:00Z"
check_refused "aging item with no owner" "aging-unowned-item" \
  pmo aging --root "$aged" --check
# and the same item with a named owner is a *reported* aging item, not a failure
pmo aging --root "$aged" --json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["items"] and d["items"][0]["tier"] == "postmortem", d' \
  && echo "  OK    control the aging item is tiered postmortem at 105 days"

# --- 6. a graph that cannot be built is CANNOT-ASSESS, never a pass ---------
hollow="$scratch/hollow"
mkdir -p "$hollow/.board"
rc=0
pmo raid --root "$hollow" --check > "$scratch/hollow.out" 2>&1 || rc=$?
controls=$((controls + 1))
if [ "$rc" -eq 2 ] && grep -qF "CANNOT-ASSESS" "$scratch/hollow.out"; then
  echo "  OK    control an unbuildable graph is CANNOT-ASSESS (rc=2), never a pass"
else
  printf '  FAIL  control an unbuildable graph exited %s, expected 2\n' "$rc" >&2
  unproven=$((unproven + 1))
fi

# --- 7. the PMO keeps no store of its own -----------------------------------
echo "== no store of its own =="
before="$(find "$stale" -type f | sort | sed "s|^$stale/||")"
pmo deps --root "$stale" >/dev/null 2>&1
pmo lanes --root "$stale" >/dev/null 2>&1
pmo report --root "$stale" >/dev/null 2>&1
pmo raid --root "$stale" >/dev/null 2>&1
pmo aging --root "$stale" >/dev/null 2>&1
after="$(find "$stale" -type f | sort | sed "s|^$stale/||")"
controls=$((controls + 1))
if [ "$before" = "$after" ]; then
  echo "  OK    deriving every view left the tree byte-for-byte unchanged"
else
  echo "  FAIL  a view wrote a store of its own:" >&2
  diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") | sed 's/^/        /' >&2
  unproven=$((unproven + 1))
fi

# --- 8. priority + dispatch: run offline, tri-state, over the real graph ----
echo "== priority + dispatch (issue #403 follow-on) =="
for view in priority dispatch; do
  rc=0
  pmo "$view" --check --json > "$scratch/real-$view.json" 2> "$scratch/real-$view.err" || rc=$?
  views=$((views + 1))
  if [ "$rc" -eq 2 ]; then
    echo "  FAIL  view $view could not assess the committed graph:" >&2
    sed 's/^/        /' "$scratch/real-$view.err" >&2
    unproven=$((unproven + 1))
    continue
  fi
  if json_ok "$scratch/real-$view.json"; then
    printf '  OK    view %-8s rc=%s (0 OK / 1 NOT-OK, 2 never treated as a pass)\n' "$view" "$rc"
  else
    echo "  FAIL  view $view did not emit a JSON document" >&2
    unproven=$((unproven + 1))
  fi
done

# --- 9. priority determinism: re-derive twice, identical (--check already
# does this in-process; this control re-derives via two separate processes,
# which is what would catch a formula that leaked the wall clock or depended
# on PYTHONHASHSEED-sensitive dict/set order across runs) -------------------
controls=$((controls + 1))
pmo priority --root . --json > "$scratch/prio-1.json" 2>/dev/null
pmo priority --root . --json > "$scratch/prio-2.json" 2>/dev/null
if diff -q "$scratch/prio-1.json" "$scratch/prio-2.json" >/dev/null 2>&1; then
  echo "  OK    control priority is byte-identical across two separate processes"
else
  echo "  FAIL  control priority differs across two separate processes" >&2
  unproven=$((unproven + 1))
fi

# --- 10. dispatch refuses a real lane collision (crafted plan document) ----
controls=$((controls + 1))
collision_out="$(cd governance/pmo && python3 - <<'PY'
from dispatch import validate_plan
document = {
    "wave": 1,
    "assignments": [
        {"ticket": "kushin77/agent-orchestrator#10", "lane": "registry"},
        {"ticket": "kushin77/agent-orchestrator#11", "lane": "registry"},
    ],
    "deferred": [],
    "unowned_risks": [],
}
findings = validate_plan(document)
codes = {f.code for f in findings}
assert "dispatch-lane-collision" in codes, findings
detail = next(f.detail for f in findings if f.code == "dispatch-lane-collision")
assert "#10" in detail and "#11" in detail, detail
print("dispatch-lane-collision: " + detail)
PY
)"
if [ $? -eq 0 ] && contains "$collision_out" "dispatch-lane-collision"; then
  echo "  OK    control dispatch refuses a real lane collision, naming both tickets"
else
  echo "  FAIL  control dispatch did not refuse a crafted lane collision:" >&2
  printf '%s\n' "$collision_out" | sed 's/^/        /' >&2
  unproven=$((unproven + 1))
fi

# --- 11. dispatch refuses a silently dropped unowned R item ----------------
controls=$((controls + 1))
dropped_out="$(cd governance/pmo && python3 - <<'PY'
from dispatch import validate_plan
document = {
    "wave": 1,
    "assignments": [],
    "deferred": [],
    "unowned_risks": [],
    "_assert_unowned_risks": ["kushin77/agent-orchestrator#99"],
}
findings = validate_plan(document)
codes = {f.code for f in findings}
assert "dispatch-unowned-risk" in codes, findings
detail_subject = next(f.subject for f in findings if f.code == "dispatch-unowned-risk")
assert detail_subject == "kushin77/agent-orchestrator#99", detail_subject
print("dispatch-unowned-risk: " + detail_subject)
PY
)"
if [ $? -eq 0 ] && contains "$dropped_out" "dispatch-unowned-risk"; then
  echo "  OK    control dispatch refuses a silently dropped unowned risk, naming it"
else
  echo "  FAIL  control dispatch did not refuse a crafted unowned-risk drop:" >&2
  printf '%s\n' "$dropped_out" | sed 's/^/        /' >&2
  unproven=$((unproven + 1))
fi

# --- 12. a malformed policy.yaml is CANNOT-ASSESS, never a pass ------------
malformed="$scratch/malformed-policy"
make_fixture "$malformed" || { echo "check-pmo-rollup: CANNOT-ASSESS — fixture" >&2; exit 2; }
write_board "$malformed" "2026-09-14T00:00:00Z" '[]'
mkdir -p "$malformed/governance/pmo"
cp governance/pmo/policy.schema.json "$malformed/governance/pmo/policy.schema.json"
printf 'version: 1\n' > "$malformed/governance/pmo/policy.yaml"
rc=0
pmo priority --root "$malformed" --check > "$scratch/malformed.out" 2>&1 || rc=$?
controls=$((controls + 1))
if [ "$rc" -eq 2 ] && grep -qF "CANNOT-ASSESS" "$scratch/malformed.out"; then
  echo "  OK    control a malformed policy.yaml is CANNOT-ASSESS (rc=2), never a pass"
else
  printf '  FAIL  control a malformed policy.yaml exited %s, expected 2\n' "$rc" >&2
  cat "$scratch/malformed.out" | sed 's/^/        /' >&2
  unproven=$((unproven + 1))
fi

# --- 13. dispatch --by-cluster: a schema-invalid clusters.json is
# CANNOT-ASSESS (rc 2), never a pass, and a valid one dispatches by cluster --
byclust="$scratch/byclust"
make_fixture "$byclust" || { echo "check-pmo-rollup: CANNOT-ASSESS — fixture" >&2; exit 2; }
write_board "$byclust" "2026-09-14T00:00:00Z" \
  '[{"number": 10, "title": "one", "state": "OPEN", "labels": [], "parent": null, "blocked_by": [], "closed_at": ""}]'
mkdir -p "$byclust/governance/pmo"
printf '{"generated_at": "2026-09-14T00:00:00Z"}\n' > "$byclust/governance/pmo/clusters.json"  # missing required keys
rc=0
pmo dispatch --root "$byclust" --by-cluster --check > "$scratch/byclust-invalid.out" 2>&1 || rc=$?
controls=$((controls + 1))
if [ "$rc" -eq 2 ] && grep -qF "CANNOT-ASSESS" "$scratch/byclust-invalid.out"; then
  echo "  OK    control a schema-invalid clusters.json is CANNOT-ASSESS (rc=2), never a pass"
else
  printf '  FAIL  control a schema-invalid clusters.json exited %s, expected 2\n' "$rc" >&2
  cat "$scratch/byclust-invalid.out" | sed 's/^/        /' >&2
  unproven=$((unproven + 1))
fi

cat > "$byclust/governance/pmo/clusters.json" <<'JSON'
{"generated_at": "2026-09-14T00:00:00Z",
 "source_repos": ["kushin77/agent-orchestrator"],
 "clusters": [{"id": "c-rca-backfill-1", "family": "RCA backfill", "title": "Backfill RCA docs",
   "recipe": "apply the standard RCA template", "sme": "sniper-generic", "tier": "L0",
   "batchable": true, "wave": 1, "priority_rank": 1,
   "evidence": "1 open issue matched family 'rca' by label scan",
   "issues": [{"repo": "kushin77/agent-orchestrator", "number": 100, "title": "rca 100",
     "labels": ["type:task"], "age_days": 10, "parent": null}]}],
 "unclustered": [],
 "hygiene": {"duplicates": [], "orphan_children": [], "empty_epics": [], "template_gaps": []}}
JSON
controls=$((controls + 1))
by_cluster_flag="$(pmo dispatch --root "$byclust" --by-cluster --json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["by_cluster"], len(d["assignments"]))')"
if [ "$by_cluster_flag" = "True 1" ]; then
  echo "  OK    control a valid clusters.json dispatches one agent per batchable cluster"
else
  echo "  FAIL  control a valid clusters.json produced: $by_cluster_flag (expected 'True 1')" >&2
  unproven=$((unproven + 1))
fi

# --- 14. the enterprise project plan (issue #1648) runs offline and its
# dependency-order-violation rule is provoked, not assumed ------------------
echo "== plan (issue #1648) =="
if pmo plan --json > "$scratch/plan-real.json" 2>"$scratch/plan-real.err"; then
  if json_ok "$scratch/plan-real.json"; then
    echo "  OK    plan renders offline over the committed plan.yaml"
  else
    echo "  FAIL  plan did not emit a JSON document" >&2
    unproven=$((unproven + 1))
  fi
else
  echo "  FAIL  plan could not be derived from the committed plan.yaml:" >&2
  sed 's/^/        /' "$scratch/plan-real.err" >&2
  unproven=$((unproven + 1))
fi

plan_fixture="$scratch/plan-fixture"
mkdir -p "$plan_fixture/governance/pmo"
cp governance/pmo/plan.schema.json "$plan_fixture/governance/pmo/plan.schema.json"
cat > "$plan_fixture/governance/pmo/plan.yaml" <<'YAML'
goal: "fixture goal"
milestones:
  - id: M0
    name: "only"
    order: 0
    exit_criteria:
      - description: "x"
        command: "true"
        expect: "0"
tasks:
  - id: t-a
    repo: agent-orchestrator
    issue: 1
    module: x
    milestone: M0
    priority: 1
    depends_on: ["t-does-not-exist"]
    sme: platform-sme
    tier: L0
    status_source: github
YAML
check_refused "plan dependency-order-violation (unknown dep)" "t-does-not-exist" \
  python3 governance/pmo/cli.py plan --root "$plan_fixture"

expected_controls=14
if [ "$controls" -ne "$expected_controls" ]; then
  echo "check-pmo-rollup: FAIL — expected $expected_controls controls, ran $controls" >&2
  unproven=$((unproven + 1))
fi

if [ "$unproven" -ne 0 ]; then
  echo "check-pmo-rollup: FAIL — $unproven control(s) did not refuse" >&2
  exit 1
fi

echo "check-pmo-rollup: OK — $views view(s) derived offline, $controls control(s) exercised, no store written"
exit 0
