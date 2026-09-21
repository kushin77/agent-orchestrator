#!/usr/bin/env bash
# check-github-lifecycle.sh — the end-to-end closure gate (issue #269).
#
# Closing a work item drifted in five separate ways on #263 and no gate noticed:
# a surviving branch, a re-claimed closed issue, a wedged claim, an unconsumed
# directive, and a worktree left behind. This gate makes each of those a *named*
# failure, and it is built so it cannot fall behind the rules:
#
#   * it provokes ONE violation per invariant in the closed vocabulary and
#     requires the audit to report that code by name — and it cross-checks the
#     provoked set against the model, so adding an invariant without provoking it
#     fails this gate rather than shipping an unexercised rule;
#   * it requires a hygienic item to pass, so the audit cannot be a wall of noise;
#   * it proves the quarantine excuses legacy work only while its tracking issue
#     is open, and that an item not named in the quarantine is never excused;
#   * it asserts the contract is declared in AGENTS.md, docs/GOVERNANCE.md and
#     docs/EXECUTION-PLAN.md, with a vacuity control on its own declaration check.
#
# It ships **no committed point-in-time GitHub record**: rules are asserted
# against fixtures this script writes. A gate that reads live board state is green
# whenever that state is stale — the trap measured in `.board/snapshot.json`
# (tracked by #170). No network is used or required.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-github-lifecycle.sh
#
# ---knowledge---
# module_id: scripts.check-github-lifecycle
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, named-refusal, lane-isolation, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#170", "#263", "#269"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

# Every "does this report contain this string?" test below is bash-native (#852).
# `printf '%s' "$output" | grep -qF -- "$s"` is NOT the same test: `grep -q` exits
# on its first match, SIGPIPE then kills the producer, and `set -o pipefail`
# promotes that 141 to the status of the whole pipeline — so a *large* report
# reports ABSENT for text that is PRESENT. Negated, that is a false red; positive,
# the control silently stops controlling and the check fails OPEN.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

cli="governance/lifecycle/cli.py"
terminal="fleet/terminal.py"
suites="scripts/pytest-suites.txt"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-github-lifecycle: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
for required in "$cli" "$terminal" "$suites" "governance/lifecycle/baseline.json"; do
  if [ ! -f "$required" ]; then
    echo "check-github-lifecycle: FAIL — $required is missing" >&2
    exit 1
  fi
done

fail=0

# --- 1. the contract is declared institutionally -----------------------------
# "file|marker|marker|..." — the substance of the rule, not its wording.
declare -a declarations=(
  "AGENTS.md|End-to-end closure|governance/lifecycle|hygienically|close-out"
  "docs/GOVERNANCE.md|governance/lifecycle|closure invariant|hygienically"
  "docs/EXECUTION-PLAN.md|governance/lifecycle/cli.py close|close-out|terminal state"
)

missing_declarations() {
  local file="$1" marker missing=0
  shift
  for marker in "$@"; do
    if ! grep -qF -- "$marker" "$file"; then
      printf '  FAIL  %s (missing declaration: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  return "$missing"
}

for entry in "${declarations[@]}"; do
  IFS='|' read -r -a parts <<< "$entry"
  if [ ! -f "${parts[0]}" ]; then
    echo "  FAIL  ${parts[0]} is missing" >&2
    fail=$((fail + 1))
    continue
  fi
  if missing_declarations "${parts[0]}" "${parts[@]:1}"; then
    echo "  OK    ${parts[0]} declares the closure contract"
  else
    fail=$((fail + 1))
  fi
done

# --- 2. the execution loop closes out through the module ---------------------
if grep -qF -- "governance/lifecycle" "$terminal"; then
  echo "  OK    $terminal drives close-out through governance/lifecycle"
else
  echo "  FAIL  $terminal does not drive close-out through governance/lifecycle" >&2
  fail=$((fail + 1))
fi

if grep -qF -- "governance/lifecycle" "$suites"; then
  echo "  OK    $suites declares the governance/lifecycle suite"
else
  echo "  FAIL  $suites does not declare the governance/lifecycle suite" >&2
  fail=$((fail + 1))
fi

# --- 3. fixtures: one provoked violation per invariant -----------------------
work="/tmp/github-lifecycle.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-github-lifecycle: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

printf '{"version": 1, "quarantine": []}\n' > "$work/empty-baseline.json"

python3 - "$work" <<'PY'
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
HEAD = "a" * 40
MERGE = "b" * 40


def item(**overrides):
    """A hygienically closed item; every fixture breaks exactly one thing."""
    base = {
        "issue": 269,
        "title": "fixture",
        "state": "closed",
        "milestone": "M26 - Session Fleet Operating Model",
        "labels": ["class:elite", "pillar:autonomous-ops"],
        "pr": {"number": 271, "state": "merged", "branch": "issue-269",
               "head_commit": HEAD, "merge_commit": MERGE},
        "branch_deleted": True,
        "claim": {"agent": None, "live": False},
        "directive": {"id": "d-269", "state": "done"},
        "lane": {},
        "verify": {"ok": True, "commit": HEAD},
        "closing_evidence": True,
    }
    base.update(overrides)
    return base


def record(items, tracking=None):
    return {"scope": "gate fixture", "items": items, "tracking": tracking or {}}


cases = {
    "clean": record([item()]),
    "PR_NOT_MERGED": record([item(pr={"number": 271, "state": "open", "branch": "issue-269",
                                      "head_commit": HEAD})]),
    "VERIFY_EVIDENCE_MISSING": record([item(verify={})]),
    "BRANCH_NOT_DELETED": record([item(branch_deleted=False)]),
    "CLAIM_STILL_HELD": record([item(claim={"agent": "subagent-dead", "live": True})]),
    "DIRECTIVE_NOT_CONSUMED": record([item(directive={"id": "d-269", "state": "sent"})]),
    "LANE_NOT_RECLAIMED": record([item(lane={"session_id": "s-269", "present": True})]),
    # A DEAD lane record — the record outlived a worktree a reaper already removed
    # (#834). `present` means a record remains, so the invariant is still owed; a
    # record treated as nothing is exactly how it came to shadow a live lane and
    # refuse a close-out whose lane was there all along.
    "LANE_DEAD_RECORD": record([item(lane={"session_id": "s-269", "worktree": "/gone/ao-269-s-269",
                                           "present": True, "worktree_exists": False})]),
    "CLOSING_EVIDENCE_MISSING": record([item(closing_evidence=False)]),
    "FILING_LABELS_MISSING": record([item(state="open", labels=["enhancement"], milestone="M26",
                                          pr={}, verify={})]),
    # A landed change must not stay on the board: closing the issue is a closure step.
    "ISSUE_NOT_CLOSED": record([item(state="open")]),
    # Evidence must name the verified head, never the (new) merge commit.
    "VERIFY_WRONG_COMMIT": record([item(verify={"ok": True, "commit": MERGE})]),
    # GitHub's canonical casing (OPEN/CLOSED/MERGED) must read as its lowercase
    # equivalent; the second end-to-end run found close-out misreading exactly this.
    "GITHUB_CASING": record([item(state="CLOSED", pr=dict(item()["pr"], state="MERGED"))]),
    # An epic closed while a declared child is still open.
    "CHILD_NOT_CLOSED": record([item(children=[
        {"number": 300, "state": "open", "body": "Parent: #269"},
    ])]),
    # An epic whose child edge CANNOT be established: a supplied child whose body
    # declares no parent marker at all. The audit must REPORT it - reading "no
    # children found" as "all children closed" is the silent pass #720 forbids.
    "EPIC_CHILD_MARKER_MISSING": record([item(children=[
        {"number": 300, "state": "closed", "body": "no parent marker here"},
    ])]),
    # Every child terminal: the invariant must be able to PASS, not only fail.
    "CHILD_ALL_CLOSED": record([item(children=[
        {"number": 300, "state": "closed", "body": "Parent: #269"},
    ])]),
    # A child that names a different parent is not this epic's declared child:
    # the marker is read, not guessed.
    "CHILD_WRONG_PARENT": record([item(children=[
        {"number": 300, "state": "open", "body": "Parent: #123"},
    ])]),
}

for name, payload in cases.items():
    (out / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

legacy = item(issue=253, state="open", labels=[], milestone="Portal", pr={}, verify={})
quarantine = {"version": 1, "quarantine": [
    {"code": "FILING_LABELS_MISSING", "subject": "#253", "tracked_by": "#174", "reason": "legacy"}]}
(out / "quarantine.json").write_text(json.dumps(quarantine, indent=2) + "\n", encoding="utf-8")
(out / "quarantine-live.json").write_text(
    json.dumps(record([legacy], {"#174": "open"}), indent=2) + "\n", encoding="utf-8")
(out / "quarantine-live-uppercase.json").write_text(
    json.dumps(record([legacy], {"#174": "OPEN"}), indent=2) + "\n", encoding="utf-8")
(out / "quarantine-stale.json").write_text(
    json.dumps(record([legacy], {"#174": "closed"}), indent=2) + "\n", encoding="utf-8")
# A different item breaking the same invariant must NOT be excused.
(out / "quarantine-leak.json").write_text(
    json.dumps(record([legacy, item(issue=400, state="open", labels=[], milestone="M26", pr={}, verify={})],
                      {"#174": "open"}), indent=2) + "\n", encoding="utf-8")
PY

audit() { # audit <record> [baseline]
  python3 "$cli" audit --record "$1" --baseline "${2:-$work/empty-baseline.json}" --json 2>&1
}

expect_pass() { # expect_pass <label> <record> [baseline]
  local output rc
  output="$(audit "$2" "${3:-}")"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  OK    $1"
  else
    echo "  FAIL  $1 (the audit refused a record it should accept)" >&2
    printf '%s\n' "$output" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_code() { # expect_code <code> <record> [baseline]
  local output rc
  output="$(audit "$2" "${3:-}")"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  FAIL  $1 went undetected (the audit passed a broken record)" >&2
    fail=$((fail + 1))
    return
  fi
  if ! contains "$output" "\"$1\""; then
    echo "  FAIL  $1 was not named in the report" >&2
    printf '%s\n' "$output" | sed 's/^/        /' >&2
    fail=$((fail + 1))
    return
  fi
  echo "  OK    a broken item is refused by name: $1"
}

expect_pass "a hygienically closed item is accepted" "$work/clean.json"
expect_pass "GitHub's canonical casing is read as hygienic" "$work/GITHUB_CASING.json"

# One provoked violation per invariant in the closed vocabulary.
declare -a provoked=(
  PR_NOT_MERGED
  VERIFY_EVIDENCE_MISSING
  BRANCH_NOT_DELETED
  CLAIM_STILL_HELD
  DIRECTIVE_NOT_CONSUMED
  LANE_NOT_RECLAIMED
  CLOSING_EVIDENCE_MISSING
  ISSUE_NOT_CLOSED
  FILING_LABELS_MISSING
  CHILD_NOT_CLOSED
  EPIC_CHILD_MARKER_MISSING
)
for code in "${provoked[@]}"; do
  expect_code "$code" "$work/$code.json"
done

# Evidence must name the verified head commit (a squash merge creates a new one).
expect_code "VERIFY_EVIDENCE_MISSING" "$work/VERIFY_WRONG_COMMIT.json"

# A dead lane record is a finding, named in the vocabulary the isolation audit
# already uses for it (#834) — and the report must SAY it is a dead record, so the
# operator does not go looking for a worktree that no longer exists.
#
# The containment test is `contains`, NOT `printf ... | grep -qF` (#852/#863): with
# `set -o pipefail` a report bigger than the pipe buffer kills the producer by
# SIGPIPE and the 141 becomes the pipeline's status, so the idiom reports ABSENT
# for text that is PRESENT — negated here, that would be a false green.
dead_output="$(audit "$work/LANE_DEAD_RECORD.json" "$work/empty-baseline.json")"
dead_rc=$?
if [ "$dead_rc" -eq 0 ]; then
  echo "  FAIL  a dead lane record went unreported (the audit passed a broken record)" >&2
  fail=$((fail + 1))
elif ! contains "$dead_output" "worktree-missing"; then
  echo "  FAIL  a dead lane record was reported without saying the worktree is gone" >&2
  printf '%s\n' "$dead_output" | sed 's/^/        /' >&2
  fail=$((fail + 1))
else
  echo "  OK    a dead lane record is refused by name: LANE_NOT_RECLAIMED (worktree-missing)"
fi

# An epic closed with a declared child still open is refused by name.
expect_code "CHILD_NOT_CLOSED" "$work/CHILD_NOT_CLOSED.json"
# ... and passes when every declared child is terminal, and when a supplied child
# names a different parent (the marker is read, not guessed).
expect_pass "an epic whose children are all terminal is accepted" "$work/CHILD_ALL_CLOSED.json"
expect_pass "a child naming a different parent is not this epic's child" "$work/CHILD_WRONG_PARENT.json"

# A supplied child whose edge cannot be established is REPORTED, and the report
# must name the child: "the epic has no children I could tie to it" is a finding,
# never a silent pass (#720).
output="$(audit "$work/EPIC_CHILD_MARKER_MISSING.json")"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  FAIL  a child whose edge cannot be established went unreported" >&2
  fail=$((fail + 1))
elif ! contains "$output" "#300"; then
  echo "  FAIL  the unedged child was not named in the report" >&2
  printf '%s\n' "$output" | sed 's/^/        /' >&2
  fail=$((fail + 1))
else
  echo "  OK    an unverifiable child edge is REPORTED and named: #300"
fi

# --- 4. the quarantine excuses legacy, and only while it is tracked ----------
expect_pass "a quarantined legacy item is excused while its tracker is open" \
  "$work/quarantine-live.json" "$work/quarantine.json"
expect_pass "a quarantine is honoured in whatever casing the tracker arrives" \
  "$work/quarantine-live-uppercase.json" "$work/quarantine.json"
expect_code "QUARANTINE_STALE" "$work/quarantine-stale.json" "$work/quarantine.json"
expect_code "FILING_LABELS_MISSING" "$work/quarantine-leak.json" "$work/quarantine.json"

# --- 5. the gate covers the whole vocabulary --------------------------------
# Adding an invariant without provoking it here would ship an unexercised rule,
# so the provoked set is compared against the model rather than trusted.
if python3 - "$root" "${provoked[@]}" <<'PY'
import sys
import pathlib

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
from governance.lifecycle.model import INVARIANTS  # noqa: E402

provoked = set(sys.argv[2:])
declared = {inv.code for inv in INVARIANTS if inv.subject_kind != "baseline"}
missing = sorted(declared - provoked)
extra = sorted(provoked - declared)
if missing:
    print(f"  FAIL  invariant(s) declared but never provoked: {', '.join(missing)}", file=sys.stderr)
if extra:
    print(f"  FAIL  provoked code(s) outside the vocabulary: {', '.join(extra)}", file=sys.stderr)
if missing or extra:
    raise SystemExit(1)
print(f"  OK    all {len(declared)} item and epic invariants are provoked by this gate")
PY
then
  :
else
  fail=$((fail + 1))
fi

# Every invariant must carry a requirement and a remediation, or a finding could
# name a rule with nothing to say about clearing it.
if python3 - "$root" <<'PY'
import sys
import pathlib

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
from governance.lifecycle.model import INVARIANTS  # noqa: E402

problems = []
codes = [inv.code for inv in INVARIANTS]
if len(set(codes)) != len(codes):
    problems.append("duplicate invariant codes")
for inv in INVARIANTS:
    if inv.subject_kind not in {"item", "baseline", "epic"}:
        problems.append(f"{inv.code}: unknown subject_kind {inv.subject_kind!r}")
    if len(inv.requires) < 20 or len(inv.remediation) < 20:
        problems.append(f"{inv.code}: requires/remediation is too thin to be actionable")
if problems:
    for problem in problems:
        print(f"  FAIL  {problem}", file=sys.stderr)
    raise SystemExit(1)
print(f"  OK    the vocabulary is closed, total and actionable ({len(INVARIANTS)} invariants)")
PY
then
  :
else
  fail=$((fail + 1))
fi

# --- 6. vacuity control: the declaration check must be able to fail ---------
grep -vF "End-to-end closure" AGENTS.md > "$work/agents-without-the-rule.md"
IFS='|' read -r -a parts <<< "${declarations[0]}"
if missing_declarations "$work/agents-without-the-rule.md" "${parts[@]:1}" >/dev/null 2>&1; then
  echo "  FAIL  vacuity control: removing the rule from AGENTS.md went undetected" >&2
  fail=$((fail + 1))
else
  echo "  OK    vacuity control: removing the rule from AGENTS.md is detected"
fi

# --- 7. declared controls: a mutated control is refused by name (issue #885) -
# check_schema requires PyYAML, already required by governance/conformance;
# sha256-restore idiom mirrors scripts/check-surface-class.sh's negative control.
controls="governance/lifecycle/controls.yaml"
base_controls_sha="$(sha256sum "$controls" | awk '{print $1}')"
python3 - "$controls" "$work/controls-mutant.yaml" <<'PY'
import sys
from pathlib import Path

import yaml

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
doc = yaml.safe_load(src.read_text(encoding="utf-8"))
doc["retire"]["min_reason_length"] = 0  # policy.load() refuses < 1 (issue #885)
Path(dst).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
PY
mutant_controls_sha="$(sha256sum "$work/controls-mutant.yaml" | awk '{print $1}')"
if [ "$mutant_controls_sha" = "$base_controls_sha" ]; then
  echo "check-github-lifecycle: CANNOT-ASSESS — the controls mutation did not change the file" >&2
  fail=$((fail + 1))
else
  control_out="$(AO_LIFECYCLE_CONTROLS="$work/controls-mutant.yaml" python3 "$cli" audit --record "$work/clean.json" --baseline "$work/empty-baseline.json" --json 2>&1)"
  control_rc=$?
  if [ "$control_rc" -eq 0 ]; then
    echo "  FAIL  a mutated control (min_reason_length: 0) was not refused" >&2
    fail=$((fail + 1))
  elif ! contains "$control_out" "min_reason_length"; then
    echo "  FAIL  the mutated control was refused, but not by name (min_reason_length)" >&2
    printf '%s\n' "$control_out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  else
    echo "  OK    a control mutated below its floor is refused by name: min_reason_length"
  fi
fi

# --- 8. decision ledger: a missing record is refused (issue #885) -----------
# directive.consume/retire write exactly one ledger record per call, success or
# refusal; a build that regressed to writing NONE for a refusal would leave the
# decision trail silently short, so this asserts the record is really there.
ledger_check="$(python3 - "$root" "$work" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
scratch = pathlib.Path(sys.argv[2]) / "ledger-fixture"
scratch.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(root))
from governance.lifecycle import directive, ledger  # noqa: E402

sent = scratch / ".fleet" / "sent"
sent.mkdir(parents=True, exist_ok=True)
(sent / "d-885.json").write_text(json.dumps({"id": "d-885", "task": {"issue": 99999885}}), encoding="utf-8")
try:
    directive.consume(scratch, "d-885", landed={})  # outside the record: refused
except directive.DirectiveRefused:
    pass
records = ledger.read_all(scratch / ledger.DEFAULT_LEDGER)
if len(records) != 1:
    print("FAIL: expected exactly 1 ledger record for the refusal, found %d" % len(records))
    raise SystemExit(1)
if records[0]["outcome"] != "refused":
    print("FAIL: the record does not carry outcome=refused")
    raise SystemExit(1)
print("OK: exactly one refusal record was written")
PY
)"
ledger_rc=$?
if [ "$ledger_rc" -ne 0 ]; then
  echo "  FAIL  a refusal must leave exactly one ledger record; ${ledger_check}" >&2
  fail=$((fail + 1))
else
  echo "  OK    a refusal produces exactly one ledger record: ${ledger_check#OK: }"
fi

# --- 9. frozen schema: a schema-invalid record is refused (issue #885) ------
schema_check="$(python3 - "$root" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
from governance.lifecycle import ledger  # noqa: E402
from governance.modules import schema as _schema  # noqa: E402

# A record missing required keys (subject/outcome/detail), bypassing render()'s
# own validation, driven straight at the schema-checked shape.
bad = {"schema": ledger.SCHEMA, "kind": "decision", "action": "close"}
problems = list(_schema.problems(bad, ledger._record_schema("ledgerRecord")))
if not problems:
    print("FAIL: a record missing required keys validated anyway")
    raise SystemExit(1)
try:
    ledger.render(kind="decision", action="close", subject="#1", outcome="not-a-real-outcome", detail="x")
    print("FAIL: an unknown outcome was accepted by render()")
    raise SystemExit(1)
except ledger.LedgerUnavailable:
    pass
print("OK: a schema-invalid record is refused before it is written")
PY
)"
schema_rc=$?
if [ "$schema_rc" -ne 0 ]; then
  echo "  FAIL  a schema-invalid record must be refused; ${schema_check}" >&2
  fail=$((fail + 1))
else
  echo "  OK    ${schema_check#OK: }"
fi

# --- 10. live feed: drift from the real store is refused (issue #885) -------
live_check="$(python3 - "$root" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
from governance.lifecycle import live  # noqa: E402

record = {
    "items": [
        {
            "issue": 269, "state": "closed", "milestone": None,
            "pr": {"state": "merged", "head_commit": "a" * 40},
            "branch_deleted": True, "claim": {}, "lane": {},
        }
    ]
}
faithful = live.project(record)
if live.check_live(record, faithful):
    print("FAIL: a faithful projection was refused")
    raise SystemExit(1)
drifted = {"items": [{"issue": 269, "stage": "filed"}]}  # the real store says "opened"
problems = live.check_live(record, drifted)
if not problems or "269" not in problems[0]:
    print("FAIL: a live feed that drifted from the real store went unreported")
    raise SystemExit(1)
print("OK: a live feed that drifted from the real store is refused, by issue")
PY
)"
live_rc=$?
if [ "$live_rc" -ne 0 ]; then
  echo "  FAIL  a drifted live feed must be refused; ${live_check}" >&2
  fail=$((fail + 1))
else
  echo "  OK    ${live_check#OK: }"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-github-lifecycle: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-github-lifecycle: OK — the contract is declared, every invariant is provoked, and legacy is quarantined only while tracked"
exit 0
