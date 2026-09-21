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
# No verify/attest step in this path -- those are advisory and run at another
# venue (AGENTS.md rule 7). This script is code-only landing.
#
# Refuses BY NAME (exit 1) when the trailer guard fails or the PR is not
# MERGEABLE. CANNOT-ASSESS (exit 2) when a precondition (gh, AO_REPO/origin,
# a readable PR) cannot be met.
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
(squash-message-would-drop-trailer) or pr-not-mergeable:<state> before any
merge. Dry run by default. Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
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
  # A fixture PR (#4242) with a clean trailer; state/mergeable are overridable
  # via env so the same shim proves both the refusal and the success path.
  cat >"$bin/gh" <<SHIM
#!/usr/bin/env bash
args="\$*"
case "\$args" in
  *"pulls/4242"*mergeable*)
    printf '{"state":"%s","mergeable":"%s","headRefName":"issue-4242-selftest"}\n' \\
      "\${AO_SELFTEST_STATE:-open}" "\${AO_SELFTEST_MERGEABLE:-MERGEABLE}"
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
  # Case 1: not mergeable -> refused by name, nothing merged.
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_SELFTEST_MERGEABLE=CONFLICTING bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 1 ] || [ -z "$(printf '%s' "$out" | grep -F 'pr-not-mergeable:CONFLICTING')" ]; then
    echo "land --self-test: NOT-OK — not-mergeable case did not refuse by name (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 2: mergeable, dry run -> OK, plan printed, nothing merged.
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$(printf '%s' "$out" | grep -F 'DRY RUN')" ]; then
    echo "land --self-test: NOT-OK — dry-run case did not pass cleanly (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  # Case 3: mergeable, apply -> merges (reclaim step is best-effort/no-op here
  # since there is no real worktree for the fixture branch; only the merge is
  # asserted).
  out="$(cd "$tmp" && AO_REPO=kushin77/agent-orchestrator PATH="$bin:$PATH" AO_LAND_APPLY=1 bash "$root/scripts/land.sh" 4242 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$(printf '%s' "$out" | grep -F 'merged #4242')" ]; then
    echo "land --self-test: NOT-OK — apply case did not merge (rc=$rc)" >&2
    echo "$out" >&2
    failures=$((failures + 1))
  fi

  rm -rf "$tmp"
  if [ "$failures" -eq 0 ]; then
    echo "land --self-test: OK — not-mergeable refused by name, dry run planned, apply merged"
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
view_json="$(gh api "repos/$repo/pulls/$pr_number" --jq '{state, mergeable, headRefName: .head.ref}' 2>&1)"
gh_rc=$?
if [ "$gh_rc" -ne 0 ]; then
  echo "land: CANNOT-ASSESS — reading PR #$pr_number failed: $view_json" >&2
  exit 2
fi
pr_state="$(printf '%s' "$view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state") or "")' 2>/dev/null)"
pr_mergeable="$(printf '%s' "$view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("mergeable") or "")' 2>/dev/null)"
pr_branch="$(printf '%s' "$view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("headRefName") or "")' 2>/dev/null)"
if [ -z "$pr_state" ]; then
  echo "land: CANNOT-ASSESS — could not parse state/mergeable for #$pr_number" >&2
  exit 2
fi
if [ "${pr_state,,}" != "open" ]; then
  echo "land: REFUSED — pr-not-open:$pr_state — #$pr_number is not open; nothing merged" >&2
  exit 1
fi
if [ "$pr_mergeable" != "MERGEABLE" ]; then
  echo "land: REFUSED — pr-not-mergeable:${pr_mergeable:-UNKNOWN} — #$pr_number is not MERGEABLE; nothing merged" >&2
  exit 1
fi

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
