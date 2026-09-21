#!/usr/bin/env bash
# land.sh — issue #1675: one command lands a PR end to end (single-developer
# method, AGENTS.md rule 10, adopted 2026-09-21):
#   scripts/check-squash-message.sh --pr N  (trailer guard)
#   -> gh pr merge N --squash --delete-branch (close-out is automatic: the PR
#      body's Closes #N trailer, already required by the guard, closes the
#      issue -- nothing manual)
#   -> scripts/prune-worktrees.sh --branches --apply (reclaim: removes the now
#      landed lane worktree and its local branch)
#
# ---knowledge---
# module_id: scripts.land
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: [single-command-orchestration, guard-then-merge-then-reclaim]
# derives_from: scripts/check-squash-message.sh
# owner_sme: platform-sme
# tier: L1
# interfaces: [usage, run_self_test]
# invariants: "the trailer guard always runs before merge; reclaim only runs after a successful merge"
# gotchas: ""
# related: ["#1675"]
# do_not_duplicate: null
# ---knowledge---
#
# No verify/attest step in this path -- those are advisory and run at another
# venue (AGENTS.md rule 7). This script is code-only landing.
#
# Refuses BY NAME (exit 1) when the trailer guard fails or the PR is not
# mergeable (REST mergeable=false; the refusal names the real mergeable_state).
# CANNOT-ASSESS (exit 2) when a precondition (gh, AO_REPO/origin, a readable
# PR) cannot be met, or when GitHub has not yet computed mergeability
# (REST mergeable=null).
#
# DRY RUN BY DEFAULT, matching scripts/merge-pr.sh / scripts/land-lane.sh:
#   bash scripts/land.sh <PR>                 # dry run
#   AO_LAND_APPLY=1 bash scripts/land.sh <PR>  # land for real
#   bash scripts/land.sh --self-test           # offline, fakes gh via PATH
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

usage() {
  cat <<'USAGE'
Usage: bash scripts/land.sh <PR>
       AO_LAND_APPLY=1 bash scripts/land.sh <PR>
       bash scripts/land.sh --self-test
Runs scripts/check-squash-message.sh first and refuses by name
(squash-message-would-drop-trailer) or pr-not-mergeable:<mergeable_state>
before any merge. Mergeability is read via gh api (REST): mergeable=true is
landable regardless of mergeable_state; mergeable=false is refused by name
carrying mergeable_state; mergeable=null (still computing) is CANNOT-ASSESS.
Dry run by default. Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

run_self_test() {
  local tmp bin failures=0
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/ao1675-land.XXXXXX")" || {
    echo "land --self-test: CANNOT-ASSESS — mktemp failed" >&2
    return 2
  }
  bin="$tmp/bin"
  mkdir -p "$bin"

  # A fake `gh` covering every call this script and check-squash-message.sh
  # make: the mergeability read, the trailer-guard read, and the merge itself.
  # The fixture PR (#4242) is emitted in the REAL REST shape -- `mergeable` is a
  # JSON boolean (or null) and the human state lives in `mergeable_state` --
  # because a fixture returning the GraphQL enum string is exactly what hid
  # issue #1856: it fed the predicate the value the predicate wanted to see.
  # state / mergeable / mergeable_state are overridable via env so the one shim
  # proves every path.
  cat >"$bin/gh" <<SHIM
#!/usr/bin/env bash
args="\$*"
case "\$args" in
  *"pulls/4242"*mergeable*)
    printf '{"state":"%s","mergeable":%s,"mergeable_state":"%s","headRefName":"issue-4242-selftest"}\n' \\
      "\${AO_SELFTEST_STATE:-open}" "\${AO_SELFTEST_MERGEABLE:-true}" "\${AO_SELFTEST_MERGEABLE_STATE:-clean}"
    ;;
  *"pulls/4242"*)
    printf '{"title":"fix: self-test fixture (#4242)","body":"Refs kushin77/agent-orchestrator#4242\\\\nCloses #4242"}\n'
    ;;
  "pr merge"*)
    echo "gh: merged #4242 (shim)"
    ;;
  *)
    echo '{}'
    ;;
esac
SHIM
  chmod +x "$bin/gh"

  local out rc
  # Case 1 (mergeable-clean): mergeable=true, mergeable_state=clean -> dry run
  # proceeds, plan printed, nothing merged.
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_SELFTEST_MERGEABLE=true AO_SELFTEST_MERGEABLE_STATE=clean bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$(printf '%s' "$out" | grep -F 'DRY RUN')" ]; then
    echo "land --self-test: NOT-OK — mergeable-clean case did not pass cleanly (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 2 (mergeable-unstable): mergeable=true, mergeable_state=unstable ->
  # also proceeds. Named explicitly: under the adopted single-developer method
  # there is no required status check (AGENTS.md rule 10), so failing/pending
  # checks do not block a merge and `unstable` is landable.
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_SELFTEST_MERGEABLE=true AO_SELFTEST_MERGEABLE_STATE=unstable bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$(printf '%s' "$out" | grep -F 'DRY RUN')" ]; then
    echo "land --self-test: NOT-OK — mergeable-unstable case was not treated as landable (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 3 (not-mergeable): mergeable=false -> refused, and the refusal NAME
  # carries the real state (`dirty`, from mergeable_state), never a bare
  # boolean. Asserting the NAME (not just the rc) is the point: naming a
  # boolean is the defect.
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_SELFTEST_MERGEABLE=false AO_SELFTEST_MERGEABLE_STATE=dirty bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 1 ] || [ -z "$(printf '%s' "$out" | grep -F 'pr-not-mergeable:dirty')" ]; then
    echo "land --self-test: NOT-OK — not-mergeable case did not refuse by name carrying the real state (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 4 (mergeable-uncomputed): mergeable=null -> CANNOT-ASSESS (rc 2),
  # never a pass and never a refusal -- GitHub has not finished computing.
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_SELFTEST_MERGEABLE=null AO_SELFTEST_MERGEABLE_STATE=unknown bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 2 ] || [ -z "$(printf '%s' "$out" | grep -F 'CANNOT-ASSESS')" ]; then
    echo "land --self-test: NOT-OK — uncomputed-mergeability case was not CANNOT-ASSESS rc=2 (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 5 (not-open): a closed PR is still refused by name.
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_SELFTEST_STATE=closed bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 1 ] || [ -z "$(printf '%s' "$out" | grep -F 'pr-not-open:closed')" ]; then
    echo "land --self-test: NOT-OK — not-open case did not refuse by name (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 6 (apply): mergeable=true with apply -> merges (the reclaim step is
  # best-effort/no-op here since there is no real worktree for the fixture
  # branch; only the merge is asserted).
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_LAND_APPLY=1 bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$(printf '%s' "$out" | grep -F 'merged #4242')" ]; then
    echo "land --self-test: NOT-OK — apply case did not merge (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  rm -rf "$tmp"
  if [ "$failures" -eq 0 ]; then
    echo "land --self-test: OK — mergeable (clean and unstable) proceeds, not-mergeable refused by name carrying the real state, uncomputed mergeability CANNOT-ASSESS, not-open refused, apply merged"
    return 0
  fi
  echo "land --self-test: NOT-OK — $failures self-test case(s) failed" >&2
  return 1
}

if [ "${1:-}" = "--self-test" ]; then
  run_self_test
  exit $?
fi

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

pr_number="${1:-}"
case "$pr_number" in
  '' )
    usage >&2
    exit 2
    ;;
  *[!0-9]*)
    printf 'land: CANNOT-ASSESS — not a PR number: %s\n' "$pr_number" >&2
    exit 2
    ;;
esac

if ! command -v gh >/dev/null 2>&1; then
  echo "land: CANNOT-ASSESS — gh (GitHub CLI) not found" >&2
  exit 2
fi

repo="${AO_REPO:-$(git remote get-url origin 2>/dev/null | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
if [ -z "$repo" ]; then
  echo "land: CANNOT-ASSESS — cannot resolve the repository for #$pr_number (set AO_REPO)" >&2
  exit 2
fi

# --- MERGEABLE check (not delegated to check-squash-message.sh, which only
# judges the rendered message) --------------------------------------------
view_json="$(gh api "repos/$repo/pulls/$pr_number" --jq '{state, mergeable, mergeable_state, headRefName: .head.ref}' 2>&1)"
gh_rc=$?
if [ "$gh_rc" -ne 0 ]; then
  echo "land: CANNOT-ASSESS — reading PR #$pr_number failed: $view_json" >&2
  exit 2
fi
pr_state="$(printf '%s' "$view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state") or "")' 2>/dev/null)"
# REST's `mergeable` is a JSON boolean (or null while GitHub computes it) --
# NOT the GraphQL enum string ("MERGEABLE"/"CONFLICTING") that this predicate
# used to compare against, which is why every mergeable PR was refused (#1856).
# Keep the three states distinct; `or ""` would collapse false and null.
pr_mergeable="$(printf '%s' "$view_json" | python3 -c 'import json,sys; v=json.load(sys.stdin).get("mergeable"); print("true" if v is True else "false" if v is False else "null")' 2>/dev/null)"
pr_mergeable_state="$(printf '%s' "$view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("mergeable_state") or "")' 2>/dev/null)"
pr_branch="$(printf '%s' "$view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("headRefName") or "")' 2>/dev/null)"
if [ -z "$pr_state" ]; then
  echo "land: CANNOT-ASSESS — could not parse state/mergeable for #$pr_number" >&2
  exit 2
fi
if [ "${pr_state,,}" != "open" ]; then
  echo "land: REFUSED — pr-not-open:$pr_state — #$pr_number is not open; nothing merged" >&2
  exit 1
fi
# mergeable:true is landable regardless of mergeable_state -- under the adopted
# single-developer method there is no required status check (AGENTS.md rule 10),
# so `unstable` (failing/pending checks) does not block a merge.
case "$pr_mergeable" in
  true) ;;
  false)
    # Refuse BY NAME, carrying the real state (e.g. dirty/CONFLICTING) from
    # mergeable_state -- never a bare boolean.
    echo "land: REFUSED — pr-not-mergeable:${pr_mergeable_state:-UNKNOWN} — #$pr_number is not mergeable (mergeable=false, state=${pr_mergeable_state:-unknown}); nothing merged" >&2
    exit 1
    ;;
  *)
    echo "land: CANNOT-ASSESS — GitHub has not finished computing mergeability for #$pr_number (mergeable=${pr_mergeable:-unreadable}); nothing merged" >&2
    exit 2
    ;;
esac

# --- trailer guard (the shared script; not reimplemented here) ------------
guard="$root/scripts/check-squash-message.sh"
if [ ! -f "$guard" ]; then
  echo "land: CANNOT-ASSESS — the squash-message guard is missing: $guard" >&2
  exit 2
fi
echo "land: checking the rendered squash message for #$pr_number (scripts/check-squash-message.sh)"
if bash "$guard" --pr "$pr_number"; then
  guard_rc=0
else
  guard_rc=$?
fi
case "$guard_rc" in
  0) ;;
  1)
    echo "land: REFUSED — squash-message-would-drop-trailer — #$pr_number's rendered squash message would fail after merge; nothing was merged" >&2
    exit 1
    ;;
  2)
    echo "land: CANNOT-ASSESS — the squash-message guard reached no verdict for #$pr_number; nothing was merged" >&2
    exit 2
    ;;
  *)
    printf 'land: CANNOT-ASSESS — the squash-message guard returned an unrecognised exit code %s for #%s; nothing was merged\n' \
      "$guard_rc" "$pr_number" >&2
    exit 2
    ;;
esac

if [ "${AO_LAND_APPLY:-0}" != "1" ]; then
  echo "land: DRY RUN — #$pr_number is OPEN/MERGEABLE and carries the ticket trailer; would run: gh pr merge $pr_number --squash --delete-branch, then reclaim the lane worktree/branch (scripts/prune-worktrees.sh --branches --apply) (AO_LAND_APPLY=1 to execute)"
  exit 0
fi

echo "land: merging #$pr_number (gh pr merge --squash --delete-branch); close-out is automatic via the PR's own Closes #$pr_number trailer"
if ! merge_out="$(gh pr merge "$pr_number" --squash --delete-branch 2>&1)"; then
  printf 'land: NOT-OK — gh pr merge failed for #%s: %s\n' "$pr_number" "$merge_out" >&2
  exit 1
fi
echo "$merge_out"

# --- reclaim: remove the now-landed lane worktree and its local branch ----
# Best-effort and never fatal to a successful merge: the merge is the thing
# that matters, and a stale worktree is caught by the next prune pass anyway.
prune="$root/scripts/prune-worktrees.sh"
if [ -f "$prune" ]; then
  echo "land: reclaiming worktree/branch for $pr_branch (scripts/prune-worktrees.sh --branches --apply)"
  bash "$prune" --branches --apply || echo "land: NOTE — reclaim pass reported an issue; re-run scripts/prune-worktrees.sh --branches --apply by hand" >&2
else
  echo "land: NOTE — scripts/prune-worktrees.sh not found; reclaim the lane worktree/branch by hand" >&2
fi

echo "land: OK — #$pr_number landed"
exit 0
