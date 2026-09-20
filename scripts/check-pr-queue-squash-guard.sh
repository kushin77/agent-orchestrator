#!/usr/bin/env bash
# check-pr-queue-squash-guard.sh — scripts/pr-queue.sh must refuse an apply-mode
# merge whose rendered squash message would drop the ticket trailer (issue
# #1102, parent #878).
#
# THE DEFECT THIS EXISTS FOR
#   scripts/pr-queue.sh's apply mode called `gh pr merge --squash` with nothing
#   checking the message that call composes; check-squash-message.sh existed
#   but nothing called it, so the same defect that produced #960/#976/#996/#991
#   could still land through the queue. This gate PROVES pr-queue.sh now calls
#   `scripts/check-squash-message.sh --pr <n>` immediately before `gh pr merge`
#   and stops (never calls `gh pr merge`) on a NOT-OK verdict.
#
# HOW
#   Runs scripts/pr-queue.sh in apply mode (AO_QUEUE_APPLY=1) against a
#   one-PR AO_QUEUE_FIXTURE, offline (no real `gh`, no network): a scratch
#   PATH puts a fake `gh` in front of the real one (its `pr merge` subcommand
#   just records that it was called and exits 0; its `pr view` subcommand is
#   never reached because AO_QUEUE_FIXTURE supplies the list) and a fake
#   `check-squash-message.sh` stands in for the real one so the verdict is
#   deterministic and no `gh pr view`/scratch-git-repo classification runs.
#
# Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-pr-queue-squash-guard.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

target="scripts/pr-queue.sh"
[ -f "$target" ] || { echo "check-pr-queue-squash-guard: CANNOT-ASSESS — $target is missing" >&2; exit 2; }

FAILED=0
fail() { printf '  FAIL  %s\n' "$*" >&2; FAILED=$((FAILED + 1)); }
ok() { printf '  ok    %s\n' "$*"; }

TMPD=""
MT_SCRATCH=""
cleanup() {
  [ -n "$TMPD" ] && rm -rf "$TMPD" || true
  [ -n "$MT_SCRATCH" ] && rm -rf "$MT_SCRATCH" || true
}
trap cleanup EXIT
TMPD="$(mktemp -d /tmp/ao1102-pr-queue-squash-guard.XXXXXX)" || {
  echo "check-pr-queue-squash-guard: CANNOT-ASSESS — no scratch directory" >&2
  exit 2
}
# The merged-tree controls below need real `git` (merge-base / rev-parse
# against origin/master), which only works inside THIS repo's working tree —
# a scratch dir under /tmp is not one. Scratch scripts copies for those
# controls therefore live under a hidden directory INSIDE the repo root
# instead (git finds .git by walking up from any subdirectory of the tree).
MT_SCRATCH="$(mktemp -d "$root/.ao1254-mt-guard.XXXXXX")" || {
  echo "check-pr-queue-squash-guard: CANNOT-ASSESS — no in-repo scratch directory for the merged-tree controls" >&2
  exit 2
}

# headRefOid == the CURRENT origin/master tip so merged_tree_evidence_by_ref's
# merge-base check trivially passes (issue #1254 step 6 made that check
# unconditional for every PR the apply loop merges, not just this file's
# original squash-message scope).
#
# A CI checkout that only ran `git fetch origin master` (no standing fetch
# refspec, e.g. Cloud Build's detached-HEAD checkout) never creates/updates
# refs/remotes/origin/master, so a bare `rev-parse origin/master` can fail
# there even though it succeeds on a dev box clone. Refresh the ref
# ourselves with an explicit refspec before resolving it, so this gate's
# own fixture setup does not depend on the checkout having already done so.
git -C "$root" fetch --quiet origin "+refs/heads/master:refs/remotes/origin/master" >/dev/null 2>&1 || true
RUN_CASE_TIP="$(git -C "$root" rev-parse origin/master 2>/dev/null)"
fixture='[
  {"number":10,"title":"ready one","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"README.md"}],"body":"## Pre-existing red\n\nNone — no failing gate is claimed to be pre-existing.\n","headRefOid":"'"$RUN_CASE_TIP"'"}
]'
printf '%s' "$fixture" > "$TMPD/fixture.json"

fakebin="$TMPD/bin"
mkdir -p "$fakebin"

# fake `gh pr merge` records the call; never called on a refused merge.
cat > "$fakebin/gh" <<'GH'
#!/usr/bin/env bash
if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then
  echo "$3" >> "$AO_TEST_GH_MERGE_CALLS"
  exit 0
fi
echo "fake gh: unexpected invocation: $*" >&2
exit 1
GH
chmod +x "$fakebin/gh"

run_case() { # <verdict-rc> <label>
  local verdict_rc="$1" label="$2"
  local calls="$TMPD/merge-calls-$label.txt"
  : > "$calls"

  local squash_stub="$TMPD/check-squash-message-$label.sh"
  cat > "$squash_stub" <<EOF
#!/usr/bin/env bash
exit $verdict_rc
EOF
  chmod +x "$squash_stub"
  # pr-queue.sh resolves the checker relative to its OWN dirname; stand a
  # whole scratch scripts/ dir next to a copy so the relative resolution
  # picks up the stub instead of the real checker. It must live INSIDE the
  # repo tree (under MT_SCRATCH, not /tmp) so pr-queue.sh's own root/cwd
  # resolution still lands somewhere `git rev-parse origin/master` (used by
  # the merged-tree evidence check, issue #1254 step 6) can answer.
  local scratch_scripts="$MT_SCRATCH/scripts-$label"
  mkdir -p "$scratch_scripts"
  cp "$root/$target" "$scratch_scripts/pr-queue.sh"
  cp "$squash_stub" "$scratch_scripts/check-squash-message.sh"

  # No gate-status.sh next to the copy -> merged-tree evidence source (a) is
  # skipped by construction; AO_QUEUE_VERIFY_MERGED=1 with a fast fake verify
  # command supplies source (b) instead, so this pre-existing squash-message
  # control still proves ONLY what it always proved.
  AO_TEST_GH_MERGE_CALLS="$calls" PATH="$fakebin:$PATH" \
    env AO_QUEUE_APPLY=1 AO_QUEUE_FIXTURE="$TMPD/fixture.json" \
    AO_QUEUE_VERIFY_MERGED=1 AO_QUEUE_VERIFY_CMD="exit 0" \
    bash "$scratch_scripts/pr-queue.sh" > "$TMPD/out-$label.txt" 2>&1
  echo $?
}

echo "== check-pr-queue-squash-guard: apply-mode merge gated on check-squash-message.sh =="

# --- NOT-OK verdict: merge must be refused, gh pr merge never called --------
rc="$(run_case 1 notok)"
if [ "$rc" -eq 1 ]; then
  ok "NOT-OK verdict: pr-queue.sh exits 1 (refused)"
else
  fail "NOT-OK verdict: pr-queue.sh exit=$rc, expected 1"
fi
if grep -q "squash-message-would-drop-trailer" "$TMPD/out-notok.txt"; then
  ok "NOT-OK verdict: refusal is named squash-message-would-drop-trailer"
else
  fail "NOT-OK verdict: refusal is not named squash-message-would-drop-trailer"
fi
if [ -s "$TMPD/merge-calls-notok.txt" ]; then
  fail "NOT-OK verdict: gh pr merge was called although the squash-message check refused"
else
  ok "NOT-OK verdict: gh pr merge was never called"
fi

# --- OK verdict: merge must proceed -----------------------------------------
rc="$(run_case 0 ok)"
if [ "$rc" -eq 0 ]; then
  ok "OK verdict: pr-queue.sh exits 0"
else
  fail "OK verdict: pr-queue.sh exit=$rc, expected 0"
fi
if grep -q "^10$" "$TMPD/merge-calls-ok.txt"; then
  ok "OK verdict: gh pr merge was called for PR #10"
else
  fail "OK verdict: gh pr merge was not called for PR #10"
fi

echo ""

# =============================================================================
# Merged-tree evidence negative controls (issue #1254 step 6, child of #1254).
#
# scripts/pr-queue.sh must refuse `gh pr merge` when there is no evidence that
# `master + this PR's head`, at the moment of merging, actually verifies
# green — per-head evidence alone is not enough (measured 2026-09-18:
# #1110+#1115, #1309+#1287, #1300, #1246 — four times two green heads were
# red together). These controls drive the offline `--check-merged-tree` seam
# directly against REAL commits already present in this checkout (no network
# fetch of anything new), and one control drives the full apply-mode loop to
# prove a fresh-evidence PR still merges.
# =============================================================================
echo "== check-pr-queue-squash-guard: merged-tree evidence (issue #1254) =="

git fetch --quiet origin "+refs/heads/master:refs/remotes/origin/master" >/dev/null 2>&1 || true
MASTER_TIP="$(git rev-parse origin/master 2>/dev/null)"
MASTER_ANCESTOR="$(git rev-parse origin/master~3 2>/dev/null)"

if [ -z "$MASTER_TIP" ] || [ -z "$MASTER_ANCESTOR" ]; then
  echo "  SKIP  merged-tree controls — could not resolve origin/master locally (offline clone?)" >&2
else
  # A scratch scripts/ dir carrying ONLY pr-queue.sh (no gate-status.sh next
  # to it) makes merged_tree_evidence_by_ref skip evidence source (a)
  # unconditionally, regardless of whether `gh`/network happen to work in
  # this sandbox — the controls below are then deterministic offline.
  mt_scripts="$MT_SCRATCH/scripts-merged-tree"
  mkdir -p "$mt_scripts"
  cp "$root/$target" "$mt_scripts/pr-queue.sh"

  # --- stale evidence: PR head's merge-base is NOT the current tip ---------
  # (an ancestor of the tip, not the tip itself — the base moved since).
  mt_stale_out="$TMPD/mt-stale-out.txt"
  bash "$mt_scripts/pr-queue.sh" --check-merged-tree 4242 \
    --head "$MASTER_ANCESTOR" --against-base "origin/master" \
    >"$mt_stale_out" 2>&1
  mt_stale_rc=$?
  if [ "$mt_stale_rc" -eq 1 ]; then
    ok "stale evidence: exits 1 (refused)"
  else
    fail "stale evidence: exit=$mt_stale_rc, expected 1"
  fi
  if grep -q "merged-tree-unverified:4242" "$mt_stale_out" && grep -q "merge-base is not the CURRENT master tip" "$mt_stale_out"; then
    ok "stale evidence: refusal named merged-tree-unverified:4242 (stale base, not a lookup failure)"
  else
    fail "stale evidence: refusal not named merged-tree-unverified:4242 for the stale-base reason — $(cat "$mt_stale_out")"
  fi

  # --- merged tree reds: fresh merge-base, but the merged-tree verify FAILS -
  # AO_QUEUE_VERIFY_CMD/AO_QUEUE_VERIFY_ATTESTATION are pr-queue.sh's own test
  # seam for the local scratch-worktree verify (a fast fake command + fixture
  # attestation stand in for a real `scripts/verify.sh verify`, which this
  # gate must not pay for on every run).
  mt_fail_attestation="$TMPD/mt-fail-attestation.json"
  cat > "$mt_fail_attestation" <<'JSON'
{"checks": [{"name": "fake-gate-check", "verdict": "FAIL"}]}
JSON
  mt_red_out="$TMPD/mt-red-out.txt"
  AO_QUEUE_VERIFY_MERGED=1 AO_QUEUE_VERIFY_CMD="exit 1" \
    AO_QUEUE_VERIFY_ATTESTATION="$mt_fail_attestation" \
    bash "$mt_scripts/pr-queue.sh" --check-merged-tree 4243 \
    --head "$MASTER_TIP" --against-base "origin/master" \
    >"$mt_red_out" 2>&1
  mt_red_rc=$?
  if [ "$mt_red_rc" -eq 1 ]; then
    ok "merged tree reds: exits 1 (refused)"
  else
    fail "merged tree reds: exit=$mt_red_rc, expected 1"
  fi
  if grep -q "merged-tree-red:fake-gate-check" "$mt_red_out"; then
    ok "merged tree reds: refusal named merged-tree-red:fake-gate-check"
  else
    fail "merged tree reds: refusal not named merged-tree-red:fake-gate-check — $(cat "$mt_red_out")"
  fi

  # --- fresh evidence: merge-base is the tip, local verify PASSES -> merges -
  mt_fresh_out="$TMPD/mt-fresh-out.txt"
  AO_QUEUE_VERIFY_MERGED=1 AO_QUEUE_VERIFY_CMD="exit 0" \
    bash "$mt_scripts/pr-queue.sh" --check-merged-tree 4244 \
    --head "$MASTER_TIP" --against-base "origin/master" \
    >"$mt_fresh_out" 2>&1
  mt_fresh_rc=$?
  if [ "$mt_fresh_rc" -eq 0 ]; then
    ok "fresh evidence: exits 0 (passes; the queue would proceed to gh pr merge)"
  else
    fail "fresh evidence: exit=$mt_fresh_rc, expected 0 — $(cat "$mt_fresh_out")"
  fi

  # --- fresh evidence through the FULL apply loop: gh pr merge is actually
  # called for the PR (not just the standalone predicate above) -----------
  mt_apply_fixture='[
    {"number":4245,"title":"merged-tree ready one","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"README.md"}],"body":"## Pre-existing red\n\nNone — no failing gate is claimed to be pre-existing.\n","headRefOid":"'"$MASTER_TIP"'"}
  ]'
  printf '%s' "$mt_apply_fixture" > "$TMPD/mt-apply-fixture.json"
  mt_apply_scripts="$MT_SCRATCH/scripts-mt-apply"
  mkdir -p "$mt_apply_scripts"
  cp "$root/$target" "$mt_apply_scripts/pr-queue.sh"
  cat > "$mt_apply_scripts/check-squash-message.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$mt_apply_scripts/check-squash-message.sh"
  mt_apply_calls="$TMPD/mt-apply-calls.txt"
  : > "$mt_apply_calls"
  mt_apply_out="$TMPD/mt-apply-out.txt"
  AO_TEST_GH_MERGE_CALLS="$mt_apply_calls" PATH="$fakebin:$PATH" \
    AO_QUEUE_APPLY=1 AO_QUEUE_FIXTURE="$TMPD/mt-apply-fixture.json" \
    AO_QUEUE_VERIFY_MERGED=1 AO_QUEUE_VERIFY_CMD="exit 0" \
    bash "$mt_apply_scripts/pr-queue.sh" >"$mt_apply_out" 2>&1
  mt_apply_rc=$?
  if [ "$mt_apply_rc" -eq 0 ]; then
    ok "fresh evidence (apply loop): pr-queue.sh exits 0"
  else
    fail "fresh evidence (apply loop): exit=$mt_apply_rc, expected 0 — $(cat "$mt_apply_out")"
  fi
  if grep -q "^4245$" "$mt_apply_calls"; then
    ok "fresh evidence (apply loop): gh pr merge was called for PR #4245"
  else
    fail "fresh evidence (apply loop): gh pr merge was not called for PR #4245 — $(cat "$mt_apply_out")"
  fi
fi

# --- scripts/merge-pr.sh's approval consumer (issue #1272) --------------
# Negative control: AO_APPROVAL_REQUIRED=1 with no record refuses by name
# (approval-missing:merge:pr#N) and never reaches gh pr merge; a grant first
# makes the same call merge. Offline: fake `gh` + fake check-squash-message.sh
# (already OK) + a scratch AO_APPROVALS_HMAC_KEY and .fleet/approvals store.
merge_pr_target="scripts/merge-pr.sh"
if [ ! -f "$merge_pr_target" ]; then
  echo "check-pr-queue-squash-guard: CANNOT-ASSESS — $merge_pr_target is missing" >&2
  exit 2
fi
AP_SCRATCH="$(mktemp -d "$root/.ao1272-approval-guard.XXXXXX")" || {
  echo "check-pr-queue-squash-guard: CANNOT-ASSESS — no in-repo scratch directory for the approval controls" >&2
  exit 2
}
ap_squash_stub="$TMPD/check-squash-message-approval.sh"
cat > "$ap_squash_stub" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$ap_squash_stub"
ap_scripts="$AP_SCRATCH/scripts"
mkdir -p "$ap_scripts"
cp "$root/$merge_pr_target" "$ap_scripts/merge-pr.sh"
cp "$ap_squash_stub" "$ap_scripts/check-squash-message.sh"
cp "$root/scripts/pr-queue.sh" "$ap_scripts/pr-queue.sh"
ln -s "$root/integrations" "$AP_SCRATCH/integrations"
ap_view_calls="$TMPD/ap-view.json"
printf '{"baseRefName":"master","headRefOid":"%s"}' "$RUN_CASE_TIP" > "$ap_view_calls"
# The REST transport (issue #1569): merge-pr.sh reads the pull with `gh api` and
# deletes the merged head branch over REST. This stand-in answers with the shape
# each caller's own `--jq` asks for rather than running jq, and NAMES the head
# repository, so the delete is authorised by a readable value rather than by an
# absent one. The slug is resolved the same way merge-pr.sh resolves it.
ap_rest_pull="$TMPD/ap-rest-pull.json"
ap_repo_slug="$(git -C "$root" remote get-url origin 2>/dev/null | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
printf '{"baseRefName":"master","headRefOid":"%s","headRef":"issue-4321","headRepo":"%s"}' \
  "$RUN_CASE_TIP" "$ap_repo_slug" > "$ap_rest_pull"
ap_fakebin="$TMPD/ap-bin"
mkdir -p "$ap_fakebin"
cat > "$ap_fakebin/gh" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "pr" ] && [ "\$2" = "view" ]; then
  cat "$ap_view_calls"
  exit 0
fi
if [ "\$1" = "pr" ] && [ "\$2" = "merge" ]; then
  echo "\$3" >> "$TMPD/ap-merge-calls.txt"
  exit 0
fi
if [ "\$1" = "api" ]; then
  shift
  method="GET"
  if [ "\${1:-}" = "-X" ]; then
    method="\${2:-GET}"
    shift 2
  fi
  url="\${1:-}"
  if [ \$# -gt 0 ]; then shift; fi
  case "\$method \$url" in
    "GET "*"/pulls/"*)
      cat "$ap_rest_pull"
      exit 0
      ;;
    "PUT "*"/pulls/"*"/merge")
      ap_pr="\${url##*/pulls/}"
      ap_pr="\${ap_pr%%/*}"
      echo "\$ap_pr" >> "$TMPD/ap-merge-calls.txt"
      echo '{"merged":true,"sha":"1111111111111111111111111111111111111111"}'
      exit 0
      ;;
    "DELETE "*"/git/refs/heads/"*)
      echo "\${url##*/git/refs/heads/}" >> "$TMPD/ap-delete-calls.txt"
      exit 0
      ;;
  esac
  echo "fake gh: unexpected api invocation: \$method \$url" >&2
  exit 1
fi
echo "fake gh: unexpected invocation: \$*" >&2
exit 1
EOF
chmod +x "$ap_fakebin/gh"
: > "$TMPD/ap-merge-calls.txt"
: > "$TMPD/ap-delete-calls.txt"
ap_key="ao1272-self-test-key-not-a-secret"
ap_store_dir="$root/.fleet/approvals"
ap_record_file="$ap_store_dir/merge__pr_4321.json"
ap_preexisting=0
[ -e "$ap_record_file" ] && ap_preexisting=1

ap_missing_out="$TMPD/ap-missing-out.txt"
if [ "$ap_preexisting" -eq 0 ]; then
  AO_APPROVAL_REQUIRED=1 AO_MERGE_APPLY=1 AO_QUEUE_VERIFY_MERGED=1 AO_QUEUE_VERIFY_CMD="exit 0" \
    PATH="$ap_fakebin:$PATH" \
    bash "$ap_scripts/merge-pr.sh" --pr 4321 >"$ap_missing_out" 2>&1
  ap_missing_rc=$?
  if [ "$ap_missing_rc" -eq 1 ] && grep -q "approval-missing:merge:pr#4321" "$ap_missing_out"; then
    ok "approval missing: merge-pr.sh refuses approval-missing:merge:pr#4321"
  else
    fail "approval missing: exit=$ap_missing_rc, expected 1 with approval-missing:merge:pr#4321 — $(cat "$ap_missing_out")"
  fi
  if [ -s "$TMPD/ap-merge-calls.txt" ]; then
    fail "approval missing: a merge was called despite no approval record"
  else
    ok "approval missing: no merge was invoked"
  fi
else
  echo "  skip  approval missing: $ap_record_file already exists (pre-existing local .fleet state) — not overwritten"
fi

AO_APPROVALS_HMAC_KEY="$ap_key" python3 "$root/integrations/paperclip/adapters/approvals/record_cli.py" \
  grant --actor operator --scope "merge:pr#4321" --ttl-seconds 900 >/dev/null 2>"$TMPD/ap-grant-err.txt" \
  || fail "approval granted: grant CLI failed — $(cat "$TMPD/ap-grant-err.txt")"
: > "$TMPD/ap-merge-calls.txt"
ap_granted_out="$TMPD/ap-granted-out.txt"
AO_APPROVAL_REQUIRED=1 AO_MERGE_APPLY=1 AO_QUEUE_VERIFY_MERGED=1 AO_QUEUE_VERIFY_CMD="exit 0" \
  AO_APPROVALS_HMAC_KEY="$ap_key" PATH="$ap_fakebin:$PATH" \
  bash "$ap_scripts/merge-pr.sh" --pr 4321 >"$ap_granted_out" 2>&1
ap_granted_rc=$?
if [ "$ap_granted_rc" -eq 0 ] && grep -q "^4321$" "$TMPD/ap-merge-calls.txt"; then
  ok "approval granted: merge-pr.sh merges #4321 after grant"
else
  fail "approval granted: exit=$ap_granted_rc, expected 0 with the merge endpoint called — $(cat "$ap_granted_out")"
fi
if grep -q "^issue-4321$" "$TMPD/ap-delete-calls.txt"; then
  ok "approval granted: the merged head branch was deleted over REST"
else
  fail "approval granted: no head-branch delete was recorded (issue #1569's REST replacement for --delete-branch)"
fi
if [ "$ap_preexisting" -eq 0 ]; then
  rm -f "$ap_record_file"
fi
rm -rf "$AP_SCRATCH"

if [ "$FAILED" -eq 0 ]; then
  echo "check-pr-queue-squash-guard: OK — refused NOT-OK without merging, merged on OK; merged-tree and approval-record evidence controls proven"
  exit 0
fi
echo "check-pr-queue-squash-guard: NOT-OK — $FAILED finding(s)" >&2
exit 1
