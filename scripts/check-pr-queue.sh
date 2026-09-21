#!/usr/bin/env bash
# check-pr-queue.sh — scripts/pr-queue.sh, proved offline (issue #1053).
#
# THE DEFECT THIS EXISTS FOR
#   The serial PR-queue-clearing procedure done by hand on 2026-09-17 (10 PRs,
#   docs/PR-QUEUE.md) has rules a human can forget under pressure: land
#   gate-changing PRs last, never auto-merge a declared pre-existing red, skip
#   (don't fight) a conflict, and re-read mergeability after every merge
#   because master moves. `scripts/pr-queue.sh` codifies the rules; this gate
#   PROVOKES each one against a fixture PR list so a regression is refused by
#   name rather than discovered the next time ten PRs are cleared by hand.
#
# WHAT IT PROVES
#   1. a draft PR is excluded from the merge order by default;
#   2. AO_QUEUE_INCLUDE_DRAFTS=1 admits the same draft into the merge order;
#   3. a gate-changing PR (touches a gate path) is ordered AFTER every ready
#      PR in the printed plan;
#   4. a conflicting PR is reported but never enters the merge order;
#   5. a PR whose body's "## Pre-existing red" is not "None" is reported
#      (by name) and never enters the merge order;
#   6. a MUTANT (the gate-path glob list emptied via AO_QUEUE_GATE_PATHS="")
#      must diverge: the same fixture PR that was gate-changing becomes ready.
#      A control that answers the same both ways proves nothing.
#
# Offline and deterministic: PR data comes from AO_QUEUE_FIXTURE (no `gh`, no
# network); nothing here ever exports AO_QUEUE_APPLY=1.
#
# Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-pr-queue.sh
#
# ---knowledge---
# module_id: scripts.check-pr-queue
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, offline-hermetic, dry-run-default, deterministic]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#1053"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

target="scripts/pr-queue.sh"
[ -f "$target" ] || { echo "check-pr-queue: CANNOT-ASSESS — $target is missing" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "check-pr-queue: CANNOT-ASSESS — python3 not found" >&2; exit 2; }

FAILED=0
fail() { printf '  FAIL  %s\n' "$*" >&2; FAILED=$((FAILED + 1)); }
ok() { printf '  ok    %s\n' "$*"; }

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT
TMPD="$(mktemp -d /tmp/ao1053-pr-queue.XXXXXX)" || {
  echo "check-pr-queue: CANNOT-ASSESS — no scratch directory for the self-test" >&2
  exit 2
}

fixture="$TMPD/fixture.json"
cat > "$fixture" <<'JSON'
[
  {"number":10,"title":"ready one","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"README.md"}],"body":"## Pre-existing red\n\nNone — no failing gate is claimed to be pre-existing.\n"},
  {"number":20,"title":"gate change","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"scripts/verify.sh"}],"body":"## Pre-existing red\n\nNone\n"},
  {"number":30,"title":"a draft","isDraft":true,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"docs/x.md"}],"body":"## Pre-existing red\n\nNone\n"},
  {"number":40,"title":"a conflict","isDraft":false,"mergeable":"CONFLICTING","mergeStateStatus":"DIRTY","files":[{"path":"README.md"}],"body":"## Pre-existing red\n\nNone\n"},
  {"number":50,"title":"a red one","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"README.md"}],"body":"## Pre-existing red\n\nReproduce: `bash scripts/check-x.sh`\n"}
]
JSON

run_plan() { # <out-file> [env assignments...]
  local out="$1"; shift
  env -u AO_QUEUE_APPLY AO_QUEUE_FIXTURE="$fixture" "$@" \
    bash "$target" > "$out" 2>&1
  return $?
}

echo "== check-pr-queue: the fixture plan, each rule provoked =="

# --- 1. draft excluded by default -------------------------------------------
out="$TMPD/out-default.txt"
run_plan "$out"; rc=$?
[ "$rc" -eq 0 ] || fail "default plan: rc=$rc, expected 0"
if grep -qE '^ *30 +draft' "$out"; then
  ok "draft PR (#30) is reported, classified 'draft'"
else
  fail "draft PR (#30) is not reported as 'draft' in the default plan"
fi
if grep -q '^MERGE_ORDER:.*\b30\b' "$out"; then
  fail "draft PR (#30) appears in the merge order although drafts are excluded by default"
else
  ok "draft PR (#30) is excluded from the merge order by default"
fi

# --- 2. AO_QUEUE_INCLUDE_DRAFTS=1 admits the draft --------------------------
out="$TMPD/out-drafts.txt"
run_plan "$out" AO_QUEUE_INCLUDE_DRAFTS=1; rc=$?
[ "$rc" -eq 0 ] || fail "include-drafts plan: rc=$rc, expected 0"
if grep -qE '^ *30 +ready' "$out"; then
  ok "AO_QUEUE_INCLUDE_DRAFTS=1 admits PR #30 (now classified 'ready')"
else
  fail "AO_QUEUE_INCLUDE_DRAFTS=1 did not admit PR #30 into a mergeable class: $(grep -E '^ *30 ' "$out")"
fi

# --- 3. gate-changing is ordered after every ready PR -----------------------
out="$TMPD/out-order.txt"
run_plan "$out"; rc=$?
if grep -qE '^ *20 +gate-changing' "$out"; then
  ok "PR #20 (touches scripts/verify.sh) is classified 'gate-changing'"
else
  fail "PR #20 is not classified 'gate-changing': $(grep -E '^ *20 ' "$out")"
fi
pos10="$(grep -n '^ *10 ' "$out" | head -1 | cut -d: -f1)"
pos20="$(grep -n '^ *20 ' "$out" | head -1 | cut -d: -f1)"
if [ -n "$pos10" ] && [ -n "$pos20" ] && [ "$pos10" -lt "$pos20" ]; then
  ok "the ready PR (#10) precedes the gate-changing PR (#20) in the printed plan"
else
  fail "ordering is wrong: ready (#10, line $pos10) does not precede gate-changing (#20, line $pos20)"
fi

# --- 4. conflict is reported, never merged ----------------------------------
if grep -qE '^ *40 +conflict' "$out"; then
  ok "the conflicting PR (#40) is reported, classified 'conflict'"
else
  fail "the conflicting PR (#40) is not classified 'conflict': $(grep -E '^ *40 ' "$out")"
fi
if grep -q '^MERGE_ORDER:.*\b40\b' "$out"; then
  fail "the conflicting PR (#40) is in the merge order — a conflict must be skipped, not fought"
else
  ok "the conflicting PR (#40) never enters the merge order"
fi

# --- 5. pre-existing red is flagged, never merged ---------------------------
if grep -qE '^ *50 +pre-existing-red' "$out"; then
  ok "the PR declaring pre-existing red (#50) is reported, classified 'pre-existing-red'"
else
  fail "PR #50 is not classified 'pre-existing-red': $(grep -E '^ *50 ' "$out")"
fi
if grep -q '^MERGE_ORDER:.*\b50\b' "$out"; then
  fail "PR #50 (declared pre-existing red) is in the merge order — it must never be auto-merged"
else
  ok "PR #50 (declared pre-existing red) never enters the merge order"
fi
if grep -qE '^ *10 +ready' "$out" && grep -q '^MERGE_ORDER:.*\b10\b' "$out"; then
  ok "the plain ready PR (#10, 'None' pre-existing red) DOES enter the merge order (the rule is directional)"
else
  fail "PR #10 (a clean 'None' pre-existing-red section) did not enter the merge order — the rule over-matches"
fi

# --- 6. the mutant: the gate-path list emptied must diverge -----------------
echo "== check-pr-queue: the mutant (gate-path list emptied) must diverge =="
out_real="$TMPD/out-real.txt"
out_mutant="$TMPD/out-mutant.txt"
run_plan "$out_real"; real_rc=$?
run_plan "$out_mutant" AO_QUEUE_GATE_PATHS=""; mutant_rc=$?
[ "$real_rc" -eq 0 ] && [ "$mutant_rc" -eq 0 ] || fail "the real/mutant runs did not both exit 0 (real=$real_rc, mutant=$mutant_rc)"
if grep -qE '^ *20 +gate-changing' "$out_real" && grep -qE '^ *20 +ready' "$out_mutant"; then
  ok "PR #20: gate-changing with the real gate-path list, ready with it emptied — the mutant diverges"
else
  fail "the mutant did not diverge: real=$(grep -E '^ *20 ' "$out_real"), mutant=$(grep -E '^ *20 ' "$out_mutant")"
fi
if [ "$out_real" != "" ] && ! cmp -s "$out_real" "$out_mutant"; then
  ok "the real and mutant plans are not byte-identical (the control is not vacuous)"
else
  fail "the real and mutant plans are byte-identical — the mutation had no effect"
fi

# --- 8. mergeable/mergeStateStatus "UNKNOWN" (string) is unknown, not ready -
# Issue #1145 defect 1: `gh pr list --json mergeable` can report the JSON
# STRING "UNKNOWN" (GitHub still computing it), not just null; only null was
# ever treated as unknown, so a PR in this state was misclassified ready.
fixture_unknown="$TMPD/fixture-unknown.json"
cat > "$fixture_unknown" <<'JSON'
[
  {"number":60,"title":"still computing","isDraft":false,"mergeable":"UNKNOWN","mergeStateStatus":"UNKNOWN","files":[{"path":"README.md"}],"body":"## Pre-existing red\n\nNone\n"}
]
JSON
out="$TMPD/out-unknown.txt"
env -u AO_QUEUE_APPLY AO_QUEUE_FIXTURE="$fixture_unknown" bash "$target" > "$out" 2>&1
if grep -qE '^ *60 +unknown' "$out"; then
  ok "mergeable/mergeStateStatus = string \"UNKNOWN\" is classified 'unknown', not 'ready'"
else
  fail "PR #60 (mergeable=\"UNKNOWN\") was not classified 'unknown': $(grep -E '^ *60 ' "$out")"
fi
if grep -q '^MERGE_ORDER:.*\b60\b' "$out"; then
  fail "PR #60 (mergeable=\"UNKNOWN\") entered the merge order"
else
  ok "PR #60 (mergeable=\"UNKNOWN\") never enters the merge order"
fi

# --- 9. gate-paths.txt comments are stripped, not treated as globs ----------
# Issue #1145 defect 2: a `#`-prefixed comment line in scripts/lib/gate-paths.txt
# was read as a literal glob. The real file's own header IS such a comment
# (it names scripts/pr-queue.sh in prose), so a PR touching ONLY
# scripts/pr-queue.sh — a real file, not a gate path — must still classify
# 'ready' against the real file, proving the header text isn't glob-matched.
if grep -qE '^[[:space:]]*#' "$root/scripts/lib/gate-paths.txt"; then
  fixture_comment="$TMPD/fixture-comment.json"
  cat > "$fixture_comment" <<'JSON'
[
  {"number":70,"title":"touches pr-queue.sh only","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"scripts/pr-queue.sh"}],"body":"## Pre-existing red\n\nNone\n"}
]
JSON
  out="$TMPD/out-comment.txt"
  env -u AO_QUEUE_APPLY AO_QUEUE_FIXTURE="$fixture_comment" bash "$target" > "$out" 2>&1
  if grep -qE '^ *70 +ready' "$out"; then
    ok "a PR touching only scripts/pr-queue.sh is 'ready' against the real gate-paths.txt (its header comments are not read as globs)"
  else
    fail "PR #70 was misclassified against the real gate-paths.txt: $(grep -E '^ *70 ' "$out")"
  fi
else
  fail "scripts/lib/gate-paths.txt has no '#' comment line to provoke this case against"
fi

# --- 10. a Pre-existing-red heading that doesn't match exactly is unclear ---
# Issue #1145 defect 3: the queue used to prefix-match "## pre-existing red",
# so "## Pre-existing red / environment notes" (#1127's real shape) was
# treated as a clean "None" section instead of flagged. Reconciled against
# check-pr-contract.sh's exact-heading predicate, and "no section found" is
# refused (class 'unclear-pre-existing-red'), not silently treated as none.
fixture_fuzzy="$TMPD/fixture-fuzzy-heading.json"
cat > "$fixture_fuzzy" <<'JSON'
[
  {"number":80,"title":"fuzzy heading","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"README.md"}],"body":"## Pre-existing red / environment notes\n\nNone\n"},
  {"number":90,"title":"no section at all","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"README.md"}],"body":"Just a description, no pre-existing-red section.\n"}
]
JSON
out="$TMPD/out-fuzzy.txt"
env -u AO_QUEUE_APPLY AO_QUEUE_FIXTURE="$fixture_fuzzy" bash "$target" > "$out" 2>&1
if grep -qE '^ *80 +unclear-pre-existing-red' "$out"; then
  ok "a heading that isn't the exact '## Pre-existing red' text (#1127's shape) is flagged 'unclear-pre-existing-red', not read as a clean None"
else
  fail "PR #80 (fuzzy heading) was not flagged: $(grep -E '^ *80 ' "$out")"
fi
if grep -qE '^ *90 +unclear-pre-existing-red' "$out"; then
  ok "a body with no Pre-existing-red section at all is flagged 'unclear-pre-existing-red', not defaulted to ready"
else
  fail "PR #90 (no section) was not flagged: $(grep -E '^ *90 ' "$out")"
fi
merge_order_line="$(grep '^MERGE_ORDER:' "$out" || true)"
if [[ "$merge_order_line" =~ (^|[^0-9])80([^0-9]|$) ]] || [[ "$merge_order_line" =~ (^|[^0-9])90([^0-9]|$) ]]; then
  fail "a PR with an unclear/missing Pre-existing-red section entered the merge order"
else
  ok "neither PR #80 nor #90 enters the merge order"
fi

# --- 7. nothing here ever applies -------------------------------------------
if [ -z "${AO_QUEUE_APPLY:-}" ]; then
  ok "this self-test never sets AO_QUEUE_APPLY — every run above was a dry-run plan"
else
  fail "AO_QUEUE_APPLY leaked into the self-test's own environment"
fi

# --- summary -----------------------------------------------------------------
if [ "$FAILED" -gt 0 ]; then
  printf 'check-pr-queue: FAIL (%s finding(s)) — scratch kept at %s\n' "$FAILED" "$TMPD" >&2
  TMPD=""
  exit 1
fi
echo "check-pr-queue: OK — pr-queue.sh excludes drafts by default (and admits them on request), orders gate-changing PRs after every ready PR, skips conflicts and pre-existing reds by name, and its gate-path classification is proven load-bearing by a diverging mutant"
exit 0
