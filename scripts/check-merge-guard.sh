#!/usr/bin/env bash
# check-merge-guard.sh — the raw merge path must be guarded, and the instruction
# every spawned lane receives must name the guarded entrypoint (issue #1233,
# parent #1145).
#
# THE DEFECT THIS EXISTS FOR
#   `gh pr merge --squash` composes the landed commit message from the PR body
#   (`squash_merge_commit_message: PR_BODY`), so a body whose last paragraph is
#   not the trailer block lands a commit carrying no `Refs <slug>#<n>`. Then
#   `scripts/check-isolation-landed.sh` reddens on MASTER'S OWN TIP, which makes
#   `make verify` red for every lane on the box. Eight recurrences in one day
#   (#1145). `scripts/merge-pr.sh` now guards the raw path; this file proves that
#   guard offline with nothing merged, and proves the instruction that spawns
#   every lane hands out the guarded entrypoint instead of the raw command.
#
# HOW — everything below runs OFFLINE. A scratch PATH puts a RECORDING fake `gh`
# in front of the real one, and the REAL `scripts/check-squash-message.sh` is
# driven through it, so the refusal observed here is the real predicate's
# refusal and not a stub's:
#   1. NOT-OK: the pull answers with a body whose last paragraph is not the
#      trailer block. merge-pr.sh must exit 1, print the refusal name
#      `squash-message-would-drop-trailer`, report the predicate's own finding
#      `commit-missing-ticket-trailer`, and the recording `gh` must show that
#      `gh pr merge` was NEVER invoked. Run in apply mode, so that last claim is
#      load-bearing rather than trivially true.
#   2. OK: a trailer-bearing body. Dry run by default (exit 0, nothing merged),
#      then AO_MERGE_APPLY=1 must exit 0, record the REST merge call EXACTLY once
#      (issue #1569 moved the read, the merge and the head-branch delete to
#      `gh api`, because `gh pr view`/`gh pr merge` are GraphQL and this box's
#      SHARED GraphQL budget is exhausted by the fleet's own agents) and delete
#      the merged head branch over REST exactly once. The GUARD reads the same
#      pull over REST as well (issue #1567), so every read in this gate is a
#      `gh api` GET of ONE pull -- answered per-arm below, because arm 1 and arm
#      2 assert OPPOSITE verdicts about that one endpoint.
#   3. NO VERDICT: the pull read fails, so the guard reports CANNOT-ASSESS.
#      merge-pr.sh must exit 2 and NEVER merge (also asserted in apply mode).
#   4. The instruction surfaces: the source of the ONE render module AND the
#      prompt it actually renders must both hand out `scripts/merge-pr.sh`, and
#      no line of either may mention `gh pr merge` without naming it. A PLANTED
#      raw instruction (the verbatim pre-fix prose that spawned the eight
#      offenders) must be refused BY NAME, and a surface with the guarded
#      entrypoint removed must be refused by the OTHER name — so the rule can
#      neither pass by matching nothing nor fire on everything.
#
# Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-merge-guard.sh
#
# ---knowledge---
# module_id: scripts.check-merge-guard
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, offline-hermetic, named-refusal, dry-run-default, lane-isolation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#1145", "#1233", "#1567", "#1569"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

FAILED=0
fail() { printf '  FAIL  %s\n' "$*" >&2; FAILED=$((FAILED + 1)); }
ok() { printf '  ok    %s\n' "$*"; }

# Has <file> <needle> — a containment test that cannot kill its own producer
# (no pipe into a quiet grep; see scripts/check-verdict-contains.sh).
has() { grep -qF -- "$2" "$1"; }

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

entry="scripts/merge-pr.sh"
guard="scripts/check-squash-message.sh"
# The ONE render module: the copy of the instruction prose every spawned lane
# receives (its own docstring makes that claim, and part 4 below measures it
# against the other spawn path).
render_module="governance/spawn/render.py"

[ -f "$entry" ] || { echo "check-merge-guard: CANNOT-ASSESS — $entry is missing" >&2; exit 2; }
[ -f "$guard" ] || { echo "check-merge-guard: CANNOT-ASSESS — $guard is missing" >&2; exit 2; }
[ -f "$render_module" ] || { echo "check-merge-guard: CANNOT-ASSESS — $render_module is missing" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || {
  echo "check-merge-guard: CANNOT-ASSESS — python3 is not on PATH" >&2
  exit 2
}

TMPD="$(mktemp -d "/tmp/ao1233-merge-guard.$(printf 'X%.0s' 1 2 3 4 5 6)" 2>/dev/null)" || {
  echo "check-merge-guard: CANNOT-ASSESS — no scratch directory" >&2
  exit 2
}

# --- the recording fake `gh` -------------------------------------------------
fakebin="$TMPD/bin"
mkdir -p "$fakebin"

cat > "$fakebin/gh" <<'GH'
#!/usr/bin/env bash
# A RECORDING fake gh for check-merge-guard.sh. Never touches the network:
#   pr view   -> the JSON at $AO_TEST_GH_VIEW_JSON, or a failure when
#                $AO_TEST_GH_VIEW_MODE is `fail`
#   pr merge  -> appends the PR number to $AO_TEST_GH_MERGE_CALLS, exits 0
#   api ...   -> the REST transport merge-pr.sh AND its guard use (issues #1569,
#                #1567). The stand-in does NOT implement jq: it answers with the
#                shape each caller's own `--jq` asks for (a GET of the pull
#                returns the fixture at $AO_TEST_GH_REST_PULL, which carries the
#                union of the renamed keys the THREE REST reads look up -- the
#                guard's title/body/headRefName, the apply path's
#                baseRefName/headRefOid, and the delete's headRef/headRepo), the
#                merge endpoint appends the PR number to the SAME
#                $AO_TEST_GH_MERGE_CALLS record `pr merge` writes -- so the
#                "exactly once" property covers either transport -- and the
#                head-ref DELETE appends the branch to $AO_TEST_GH_DELETE_CALLS.
set -u
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "view" ]; then
  if [ "${AO_TEST_GH_VIEW_MODE:-ok}" = "fail" ]; then
    echo "fake gh: could not resolve to a Repository (offline)" >&2
    exit 1
  fi
  cat "$AO_TEST_GH_VIEW_JSON"
  exit 0
fi
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "merge" ]; then
  printf '%s\n' "${3:-}" >> "$AO_TEST_GH_MERGE_CALLS"
  exit 0
fi
if [ "${1:-}" = "api" ]; then
  shift
  method="GET"
  if [ "${1:-}" = "-X" ]; then
    method="${2:-GET}"
    shift 2
  fi
  url="${1:-}"
  if [ $# -gt 0 ]; then shift; fi
  case "$method $url" in
    "GET "*"/pulls/"*)
      if [ "${AO_TEST_GH_VIEW_MODE:-ok}" = "fail" ]; then
        echo "fake gh: could not resolve to a Repository (offline)" >&2
        exit 1
      fi
      cat "$AO_TEST_GH_REST_PULL"
      exit 0
      ;;
    "PUT "*"/pulls/"*"/merge")
      pr="${url##*/pulls/}"
      printf '%s\n' "${pr%%/*}" >> "$AO_TEST_GH_MERGE_CALLS"
      printf '{"merged":true,"sha":"1111111111111111111111111111111111111111","message":"Pull Request successfully merged"}\n'
      exit 0
      ;;
    "DELETE "*"/git/refs/heads/"*)
      printf '%s\n' "${url##*/git/refs/heads/}" >> "$AO_TEST_GH_DELETE_CALLS"
      exit 0
      ;;
  esac
  echo "fake gh: unexpected api invocation: $method $url" >&2
  exit 1
fi
echo "fake gh: unexpected invocation: $*" >&2
exit 1
GH
chmod +x "$fakebin/gh"

# --- the two PR bodies the fake `gh pr view` answers with -------------------
view_bad="$TMPD/view-bad.json"
view_good="$TMPD/view-good.json"
# A real, resolvable commit for headRefOid: merged-tree evidence (issue #1254
# step 6, wired into merge-pr.sh's apply path by #1332) computes a real
# merge-base against origin/master, so the fixture must name a commit that
# actually exists in this checkout rather than a placeholder string.
#
# It must NOT be `git rev-parse HEAD` of the invoking checkout: merge-pr.sh's
# apply path refuses (merged-tree-unverified) unless the fixture's head is a
# DESCENDANT of origin/master's current tip, and whatever checkout `make
# verify` happens to be running from is not guaranteed to be at or ahead of
# that tip (a lane worktree, a detached older commit, a stale local clone all
# reproduce the refusal offline — measured on the shared-services runner,
# issue #1233 follow-up). Build a throwaway commit ON TOP of the CURRENT
# origin/master tip instead, so the fixture is hermetic regardless of what
# the ambient HEAD happens to be.
git fetch --quiet origin master >/dev/null 2>&1 || true
master_tip_for_fixture="$(git rev-parse origin/master 2>/dev/null)"
if [ -z "$master_tip_for_fixture" ]; then
  echo "check-merge-guard: CANNOT-ASSESS — could not resolve origin/master for the scratch fixtures" >&2
  exit 2
fi
head_oid_for_fixture="$(git commit-tree "$master_tip_for_fixture^{tree}" -p "$master_tip_for_fixture" -m "check-merge-guard scratch fixture (offline, never pushed)" 2>/dev/null)"
if [ -z "$head_oid_for_fixture" ]; then
  echo "check-merge-guard: CANNOT-ASSESS — could not build the scratch fixture commit" >&2
  exit 2
fi
# The REST fixture (issue #1569) and the repository slug the apply path derives
# from `git remote get-url origin`. The slug matters: the merged head branch may
# only be deleted when the head really lives in that same repository, so the
# fixture has to name it rather than leave it unreadable.
# One REST fixture PER ARM. The guard (issue #1567) and the apply path (issue
# #1569) read the SAME pull over the SAME endpoint, and the two arms below assert
# OPPOSITE verdicts about it -- arm 1's body is not a trailer block, arm 2's is.
# One shared fixture would have to be both, so each arm names the one it means and
# the fixtures are written from that arm's own body rather than one shared blob.
rest_pull_bad="$TMPD/rest-bad.json"
rest_pull_good="$TMPD/rest-good.json"
repo_slug="$(git remote get-url origin 2>/dev/null | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
python3 - "$view_bad" "$view_good" "$head_oid_for_fixture" \
  "$rest_pull_bad" "$rest_pull_good" "$repo_slug" <<'PY'
import json
import sys

bad, good = sys.argv[1], sys.argv[2]
# baseRefName/headRefOid (issue #1332's merged-tree seam): merge-pr.sh's apply
# path reads these off the call this fake answers, so a fixture missing them
# starves that seam of evidence and merge-pr.sh reaches CANNOT-ASSESS before
# ever reaching the merge -- that is not this gate's NOT-OK/no-verdict case, it
# is a fixture gap, so all four bodies carry them.
head_oid = sys.argv[3]
rest_bad, rest_good, repo_slug = sys.argv[4], sys.argv[5], sys.argv[6]

# The title and the two bodies, written ONCE. Both transports describe the same
# pull, so rendering them from separate literals would let the harness drift into
# measuring a fiction -- a REST read and a GraphQL read disagreeing about one PR.
title = "fix(thing): do the thing"
bad_body = "What changed.\n\nSome detail with no trailer paragraph at all.\n"
# The OK body carries BOTH trailer lines, because the guard applies its issue-lane
# rule as soon as the REST read reports headRefName=issue-1233 (issue #1567): a lane
# branch whose trailer block names only `Refs` would not auto-close the issue, so
# the guard refuses it by name (closes-missing:1233). That is the guard's own
# production rule, not a harness convention -- the OK fixture must therefore
# describe a pull that would really be accepted, or arm 2 would be asserting that a
# body the guard is required to refuse lands.
good_body = (
    "What changed.\n\nSome detail.\n\n"
    "Refs kushin77/agent-orchestrator#1233\nCloses #1233\n"
)

# The GraphQL shape, kept for the `gh pr view` the apply path's publish step still
# makes. It carries no `state`/`mergeCommit`, so that step reports it could not
# name the landed commit -- its own tolerated path, and NOT arm 2's claim (which
# is about the merge call and the head-branch delete).
for path, body in ((bad, bad_body), (good, good_body)):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "title": title,
                "body": body,
                "baseRefName": "master",
                "headRefOid": head_oid,
            },
            handle,
        )

# The REST shape. The fake answers after-the-filter rather than running jq (the
# real filters are measured against the live API elsewhere), so this fixture
# carries the union of the renamed keys the THREE REST reads look up:
#   * scripts/check-squash-message.sh --jq '{title, body, headRefName: .head.ref}'
#     (issue #1567 -- the guard reads the pull over REST, so a fixture without
#     title/body/headRefName starves it into CANNOT-ASSESS and merge-pr.sh exits 2
#     where arm 1 expects 1 and arm 2 expects 0)
#   * scripts/merge-pr.sh (apply)  --jq '{baseRefName: .base.ref, headRefOid: .head.sha}'
#   * scripts/merge-pr.sh (delete) --jq '{headRef: .head.ref, headRepo: (.head.repo.full_name // "")}'
# headRefName and headRef are the same underlying field (.head.ref), which is why
# the fixture names it twice; headRepo must equal the slug merge-pr.sh derived
# from origin or the delete is refused as a branch living in another repository.
for path, body in ((rest_bad, bad_body), (rest_good, good_body)):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "title": title,
                "body": body,
                "headRefName": "issue-1233",
                "baseRefName": "master",
                "headRefOid": head_oid,
                "headRef": "issue-1233",
                "headRepo": repo_slug,
            },
            handle,
        )
PY
fixtures_rc=$?
if [ "$fixtures_rc" -ne 0 ]; then
  echo "check-merge-guard: CANNOT-ASSESS — could not write the scratch fixtures (rc=$fixtures_rc)" >&2
  exit 2
fi

# --- drive the REAL entrypoint (which drives the REAL guard) -----------------
# <label> <view-mode> <graphql-json> <rest-json> <apply 0|1> -> prints the
# entrypoint's exit code; its output lands in $TMPD/out-<label>.txt, the recorded
# merge calls in $TMPD/calls-<label>.txt, the recorded head-branch deletes in
# $TMPD/deletes-<label>.txt.
#
# Two json files, because one arm must present ONE pull over BOTH transports the
# production code now uses: the guard reads it over REST (issue #1567) and the
# apply path reads it over REST for the merge/delete (issue #1569) plus GraphQL
# for the post-merge read-back. Passing the arm's pair keeps the arm's verdict and
# the arm's recorded calls describing a single pull.
run_merge() {
  local label="$1" mode="$2" json="$3" rest_json="$4" apply="$5"
  local calls deletes out rc
  calls="$TMPD/calls-$label.txt"
  deletes="$TMPD/deletes-$label.txt"
  out="$TMPD/out-$label.txt"
  : > "$calls"
  : > "$deletes"
  # AO_QUEUE_VERIFY_MERGED/AO_QUEUE_VERIFY_CMD are scripts/pr-queue.sh's own
  # offline test seam for merged-tree evidence source (b) — merge-pr.sh's
  # apply path now reuses merged_tree_evidence_by_ref (issue #1254 step 6 via
  # #1332), and this fake `gh` cannot answer a real `gate-status.sh show`
  # (evidence source (a)), so without this seam every apply-mode run would
  # refuse merged-tree-unverified before ever reaching gh pr merge.
  AO_TEST_GH_MERGE_CALLS="$calls" AO_TEST_GH_VIEW_MODE="$mode" AO_TEST_GH_VIEW_JSON="$json" \
    AO_TEST_GH_REST_PULL="$rest_json" AO_TEST_GH_DELETE_CALLS="$deletes" \
    AO_MERGE_APPLY="$apply" AO_QUEUE_VERIFY_MERGED=1 AO_QUEUE_VERIFY_CMD="exit 0" \
    PATH="$fakebin:$PATH" \
    bash "$root/$entry" --pr 1233 > "$out" 2>&1
  rc=$?
  printf '%s' "$rc"
}

echo "== check-merge-guard: the merge entrypoint is gated on the squash-message guard =="

# --- 1. NOT-OK: refused by name, and never merged ---------------------------
rc="$(run_merge notok ok "$view_bad" "$rest_pull_bad" 1)"
if [ "$rc" = "1" ]; then
  ok "NOT-OK verdict: merge-pr.sh exits 1 (refused)"
else
  fail "NOT-OK verdict: merge-pr.sh exit=$rc, expected 1"
fi
if has "$TMPD/out-notok.txt" "squash-message-would-drop-trailer"; then
  ok "NOT-OK verdict: the refusal is named squash-message-would-drop-trailer"
else
  fail "NOT-OK verdict: the refusal is not named squash-message-would-drop-trailer"
fi
if has "$TMPD/out-notok.txt" "commit-missing-ticket-trailer"; then
  ok "NOT-OK verdict: the REAL shared predicate fired (commit-missing-ticket-trailer), not a stub"
else
  fail "NOT-OK verdict: the shared predicate's own finding was not reported"
fi
if [ -s "$TMPD/calls-notok.txt" ]; then
  fail "NOT-OK verdict: the REST merge was called although the guard refused"
else
  ok "NOT-OK verdict: the REST merge was never called"
fi
if [ -s "$TMPD/deletes-notok.txt" ]; then
  fail "NOT-OK verdict: a head branch was deleted although nothing merged"
else
  ok "NOT-OK verdict: no head branch was deleted"
fi

# --- 2. OK: dry run by default, then merged exactly once --------------------
rc="$(run_merge ok-dryrun ok "$view_good" "$rest_pull_good" 0)"
if [ "$rc" = "0" ]; then
  ok "OK verdict (dry run by default): merge-pr.sh exits 0"
else
  fail "OK verdict (dry run by default): merge-pr.sh exit=$rc, expected 0"
fi
if has "$TMPD/out-ok-dryrun.txt" "DRY RUN"; then
  ok "OK verdict (dry run by default): the plan is printed instead of merging"
else
  fail "OK verdict (dry run by default): no DRY RUN plan was printed"
fi
if [ -s "$TMPD/calls-ok-dryrun.txt" ]; then
  fail "OK verdict (dry run by default): a merge was recorded in dry-run mode"
else
  ok "OK verdict (dry run by default): the REST merge was never called"
fi

rc="$(run_merge ok-apply ok "$view_good" "$rest_pull_good" 1)"
if [ "$rc" = "0" ]; then
  ok "OK verdict (apply): merge-pr.sh exits 0"
else
  fail "OK verdict (apply): merge-pr.sh exit=$rc, expected 0"
fi
merge_count="$(wc -l < "$TMPD/calls-ok-apply.txt")"
merge_count="${merge_count// /}"
if [ "$merge_count" = "1" ]; then
  ok "OK verdict (apply): the merge endpoint was called exactly once"
else
  fail "OK verdict (apply): the merge endpoint was called $merge_count time(s), expected exactly 1"
fi
if [ "$(head -n1 "$TMPD/calls-ok-apply.txt")" = "1233" ]; then
  ok "OK verdict (apply): the recorded merge is for PR 1233"
else
  fail "OK verdict (apply): the recorded merge names the wrong PR"
fi
# The head-branch delete is the REST replacement for `--delete-branch` (issue
# #1569): without this arm the new call could stop happening and no control here
# would notice.
delete_count="$(wc -l < "$TMPD/deletes-ok-apply.txt")"
delete_count="${delete_count// /}"
if [ "$delete_count" = "1" ] && [ "$(head -n1 "$TMPD/deletes-ok-apply.txt")" = "issue-1233" ]; then
  ok "OK verdict (apply): the merged head branch was deleted over REST exactly once (issue-1233)"
else
  fail "OK verdict (apply): the head-branch delete recorded $(tr '\n' ' ' < "$TMPD/deletes-ok-apply.txt"), expected exactly one delete of issue-1233"
fi

# --- 3. NO VERDICT: CANNOT-ASSESS, and never merged -------------------------
rc="$(run_merge noverdict fail "$view_good" "$rest_pull_good" 1)"
if [ "$rc" = "2" ]; then
  ok "no verdict: merge-pr.sh exits 2 (CANNOT-ASSESS)"
else
  fail "no verdict: merge-pr.sh exit=$rc, expected 2"
fi
if has "$TMPD/out-noverdict.txt" "CANNOT-ASSESS"; then
  ok "no verdict: the refusal is named CANNOT-ASSESS"
else
  fail "no verdict: the refusal is not named CANNOT-ASSESS"
fi
if [ -s "$TMPD/calls-noverdict.txt" ]; then
  fail "no verdict: a merge was recorded although the guard reached no verdict"
else
  ok "no verdict: the REST merge was never called"
fi

# --- 4. the instruction surfaces --------------------------------------------
echo "== check-merge-guard: the spawn instruction hands out the guarded entrypoint =="

# The rule, in ONE place, applied to ONE surface: a surface must name the
# guarded entrypoint, and no LINE of it may mention the raw command without
# naming that entrypoint. Line granularity rather than whole-file granularity on
# purpose: the pre-fix prose named `gh pr merge` and named no entrypoint at all,
# and the cheap whole-file version of this rule would have passed that prose the
# moment any other line of the file happened to mention merge-pr.sh.
cat > "$TMPD/scan.py" <<'PY'
import sys

GUARD = "scripts/merge-pr.sh"
RAW = "gh pr merge"
RAW_FINDING = "instruction-teaches-raw-merge"
MISSING_FINDING = "instruction-missing-guarded-entrypoint"


def findings(text):
    """The finding names a single surface earns, in report order."""
    out = []
    if GUARD not in text:
        out.append(MISSING_FINDING)
    for line in text.splitlines():
        if RAW in line and GUARD not in line:
            out.append(RAW_FINDING)
    return out


bad = 0
for path in sys.argv[1:]:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        sys.stderr.write("check-merge-guard: CANNOT-ASSESS — cannot read %s: %s\n" % (path, exc))
        raise SystemExit(2)
    for finding in findings(text):
        print("%s %s" % (finding, path))
        bad = 1
raise SystemExit(bad)
PY

# <label> <file>... -> prints the scanner's exit code; findings land in
# $TMPD/scan-<label>.txt. The scanner takes file paths, so the REAL surfaces and
# the planted ones are read by the very same code path.
run_scan() {
  local label="$1"
  shift
  local out rc
  out="$TMPD/scan-$label.txt"
  python3 "$TMPD/scan.py" "$@" > "$out" 2>"$TMPD/scan-$label.err"
  rc=$?
  printf '%s' "$rc"
}

# The fleet shape, not a minimal one: session identity, lane and trailer all
# present, so the rendered prompt carries the same prose a real spawn receives.
python3 - "$TMPD/rendered-prompt.txt" <<'PY'
import sys

sys.path.insert(0, ".")
from governance.spawn import render  # noqa: E402

document = {
    "issue": 1233,
    "session": {"agent": "check-merge-guard"},
    "lane": "standards",
    "trailer": "Refs kushin77/agent-orchestrator#1233",
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(render.prompt(document))
PY
render_rc=$?
if [ "$render_rc" -ne 0 ]; then
  echo "check-merge-guard: CANNOT-ASSESS — could not render the spawn prompt (rc=$render_rc)" >&2
  exit 2
fi

surface_source="$root/$render_module"
surface_prompt="$TMPD/rendered-prompt.txt"

# The plants. The first is the VERBATIM pre-fix instruction (the producer that
# spawned the eight offenders), so the negative control is the measured defect
# rather than a shape invented for the test.
plant_raw="$TMPD/plant-raw-instruction.txt"
cat > "$plant_raw" <<'PLANT'
4. After `make verify` is green and the PR is open, squash-merge it with `gh pr merge <number> --squash --delete-branch`, then close the issue. NEVER leave a completed PR unmerged or the issue open.
PLANT

plant_noguard="$TMPD/plant-no-guard.txt"
cat > "$plant_noguard" <<'PLANT'
4. After `make verify` is green and the PR is open, merge the PR and close the issue.
PLANT

plant_guarded="$TMPD/plant-guarded-instruction.txt"
cat > "$plant_guarded" <<'PLANT'
4. Land the PR through `bash scripts/merge-pr.sh --pr <number>`; running `gh pr merge <number> --squash` instead of that entrypoint is FORBIDDEN.
PLANT

# 4a. the real surfaces: both must hand out the guarded entrypoint
rc="$(run_scan real "$surface_source" "$surface_prompt")"
if [ "$rc" = "0" ] && [ ! -s "$TMPD/scan-real.txt" ]; then
  ok "instruction surfaces: the render module's source and the prompt it renders both name $entry"
else
  fail "instruction surfaces: refused (rc=$rc):"
  sed 's/^/        /' "$TMPD/scan-real.txt" >&2
fi

# 4b. the planted raw instruction must be refused BY NAME
rc="$(run_scan plantraw "$plant_raw")"
if [ "$rc" = "1" ] && has "$TMPD/scan-plantraw.txt" "instruction-teaches-raw-merge"; then
  ok "instruction surfaces: the verbatim pre-fix instruction is refused by name (instruction-teaches-raw-merge)"
else
  fail "instruction surfaces: the planted raw instruction was NOT refused by name (rc=$rc)"
fi

# 4c. attribution: a surface with the entrypoint removed is refused by the OTHER
# name, which is what proves 4b came from the raw-command rule and not from this
# one (an assertion that only checked "a finding fired" would conflate them).
rc="$(run_scan plantnoguard "$plant_noguard")"
if [ "$rc" = "1" ] && has "$TMPD/scan-plantnoguard.txt" "instruction-missing-guarded-entrypoint"; then
  ok "instruction surfaces: a surface naming no guarded entrypoint is refused by name (instruction-missing-guarded-entrypoint)"
else
  fail "instruction surfaces: a surface naming no guarded entrypoint was NOT refused by name (rc=$rc)"
fi
if has "$TMPD/scan-plantnoguard.txt" "instruction-teaches-raw-merge"; then
  fail "instruction surfaces: the no-entrypoint plant also tripped the raw-command rule, so 4b's attribution is not established"
else
  ok "instruction surfaces: the no-entrypoint plant trips ONLY the missing-entrypoint rule"
fi

# 4d. vacuity, the other direction: an instruction that names the entrypoint and
# the raw command on the same line must be ACCEPTED, or the rule refuses
# everything and proves nothing.
rc="$(run_scan plantguarded "$plant_guarded")"
if [ "$rc" = "0" ] && [ ! -s "$TMPD/scan-plantguarded.txt" ]; then
  ok "instruction surfaces: a guarded instruction that names the entrypoint is accepted (the rule does not match everything)"
else
  fail "instruction surfaces: a guarded instruction was refused (rc=$rc):"
  sed 's/^/        /' "$TMPD/scan-plantguarded.txt" >&2
fi

# 4e. the ONE-copy claim: the other spawn path must carry no second copy of the
# prose, or the surface this check measured is not the surface agents receive.
if has "$root/fleet/terminal.py" "gh pr merge"; then
  fail "one copy: fleet/terminal.py carries a second copy of the merge instruction"
else
  ok "one copy: fleet/terminal.py carries no second copy of the merge instruction"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "check-merge-guard: OK — guarded refusals (NOT-OK and no-verdict) never merged, OK merged exactly once, and the instruction surfaces hand out $entry"
  exit 0
fi
echo "check-merge-guard: NOT-OK — $FAILED finding(s)" >&2
exit 1
