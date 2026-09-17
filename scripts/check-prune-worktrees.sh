#!/usr/bin/env bash
# check-prune-worktrees.sh — the reaper's rules, provoked on a throwaway repo (#830).
#
# `scripts/prune-worktrees.sh` is the only thing that reclaims lane worktrees and
# lane branches, and until this check landed it had a unit suite but no GATE:
# nothing in `make verify` exercised it, so every rule it states was decorative.
# Its safety rests on what it KEEPS, so the KEEPs are provoked as loudly as the
# REMOVEs — a rule that cannot fail is a formality (GR-12).
#
# Everything runs against a throwaway repo built here; the real tree is never read
# or written.
#
# Tri-state: 0 the contract holds / 1 a rule is broken / 2 CANNOT-ASSESS.
set -uo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$root" ]; then
  echo "check-prune-worktrees: CANNOT-ASSESS — not inside a repository" >&2
  exit 2
fi
tool="$root/scripts/prune-worktrees.sh"
if [ ! -f "$tool" ]; then
  echo "check-prune-worktrees: CANNOT-ASSESS — no scripts/prune-worktrees.sh" >&2
  exit 2
fi

work="$(mktemp -d /tmp/ao830-check.XXXXXX)"
trap 'rm -rf "$work"' EXIT

fails=0
ok() { printf '  OK    %s\n' "$1"; }
bad() {
  printf '  FAIL  %s\n' "$1"
  fails=$((fails + 1))
}

git_() { git -C "$1" "${@:2}"; }

make_repo() { # make_repo <dir> — a repo with an origin-like remote and origin/master
  local dir="$1" origin="$1.origin.git"
  git init -q --bare "$origin"
  git init -q "$dir"
  git_ "$dir" config user.email check@example.com
  git_ "$dir" config user.name check
  git_ "$dir" config commit.gpgsign false
  printf 'seed\n' >"$dir/README.md"
  git_ "$dir" add -A
  git_ "$dir" commit -qm seed
  git_ "$dir" branch -M master
  git_ "$dir" remote add origin "$origin"
  git_ "$dir" push -qu origin master
  git_ "$dir" fetch -q origin
}

declare_runtime_state() { # declare_runtime_state <repo> <path>...
  local repo="$1"
  shift
  local body="" path
  for path in "$@"; do body="$body\"$path\", "; done
  mkdir -p "$repo/governance/isolation"
  printf 'MACHINE_MANAGED_PATHS: tuple[str, ...] = (%s)\n' "$body" \
    >"$repo/governance/isolation/worktree.py"
}

reap() { # reap <repo> <args...>
  local repo="$1"
  shift
  (cd "$repo" && bash "$tool" "$@") 2>&1
}

dirty_lane() { # dirty_lane <repo> <dir> <path>...
  local repo="$1" dir="$2"
  shift 2
  git_ "$repo" worktree add -q --detach "$dir" origin/master
  local path
  for path in "$@"; do
    mkdir -p "$(dirname "$dir/$path")"
    printf 'fleet-written\n' >"$dir/$path"
  done
}

echo "== prune-worktrees: item 3 — declared runtime state (#830) =="

repo="$work/declared"
make_repo "$repo"
declare_runtime_state "$repo" ".board/focus.json"

focus_only="$work/lane-focus"
dirty_lane "$repo" "$focus_only" ".board/focus.json"
reap "$repo" --apply >"$work/out.declared.txt" 2>&1
if [ -d "$focus_only" ]; then
  bad "a worktree dirty ONLY in declared runtime state was kept — the pile can never be reclaimed"
else
  ok "declared runtime state does not pin a worktree for ever"
fi

real_dirt="$work/lane-real"
dirty_lane "$repo" "$real_dirt" ".board/focus.json" "lane-work.txt"
reap "$repo" --apply >"$work/out.real.txt" 2>&1
if [ -d "$real_dirt" ]; then
  ok "uncommitted lane work still keeps its worktree (the exemption is narrow)"
else
  bad "a worktree with real uncommitted work was removed — the dirty guard is broken"
fi

undeclared="$work/undeclared"
make_repo "$undeclared"
undeclared_lane="$work/lane-undeclared"
dirty_lane "$undeclared" "$undeclared_lane" ".board/focus.json"
reap "$undeclared" --apply >"$work/out.undeclared.txt" 2>&1
if [ -d "$undeclared_lane" ]; then
  ok "an unreadable declaration excuses NOTHING (fail closed)"
else
  bad "an unreadable declaration still excused work — a failure must never widen what is discarded"
fi

rebound="$work/rebound"
make_repo "$rebound"
declare_runtime_state "$rebound" ".board/other.json"
rebound_focus="$work/lane-rebound-focus"
rebound_other="$work/lane-rebound-other"
dirty_lane "$rebound" "$rebound_focus" ".board/focus.json"
dirty_lane "$rebound" "$rebound_other" ".board/other.json"
reap "$rebound" --apply >"$work/out.rebound.txt" 2>&1
if [ -d "$rebound_focus" ] && [ ! -d "$rebound_other" ]; then
  ok "the declaration is READ from its owner, not copied into the reaper"
else
  bad "the reaper disagreed with the declaration it is supposed to read"
fi

echo "== prune-worktrees: item 2 — lane branches, by CONTENT not ancestry (#830) =="

branches="$work/branches"
make_repo "$branches"
squash_land() { # squash_land <repo> <branch> <file> <content>
  local repo="$1" branch="$2" file="$3" content="$4"
  git_ "$repo" checkout -q -b "$branch"
  printf '%s' "$content" >"$repo/$file"
  git_ "$repo" add -A
  git_ "$repo" commit -qm "$branch work"
  git_ "$repo" push -qu origin "$branch"
  git_ "$repo" checkout -q master
  printf '%s' "$content" >"$repo/$file"
  git_ "$repo" add -A
  git_ "$repo" commit -qm "$branch landed (squashed)"
  git_ "$repo" push -qu origin master
  git_ "$repo" fetch -q origin
}
squash_land "$branches" "issue-42-lane" "feature.txt" "landed
"

if git_ "$branches" merge-base --is-ancestor issue-42-lane origin/master 2>/dev/null; then
  bad "the premise is wrong: a squash-landed branch reads as an ancestor, so this check proves nothing"
else
  ok "the premise holds: ancestry says NOT merged while the content IS on master"
fi

report="$(reap "$branches" --check --branches)"
rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$report" | grep -q "STALE  branch issue-42-lane"; then
  ok "a landed branch that still exists is a finding (--check --branches rc=1)"
else
  bad "a landed lane branch was not reported (rc=$rc)"
fi

reap "$branches" --branches --apply >"$work/out.branches.txt" 2>&1
if git_ "$branches" rev-parse --verify --quiet issue-42-lane >/dev/null 2>&1; then
  bad "a landed lane branch was not reaped"
else
  ok "a landed lane branch is reaped by content equivalence, not ancestry"
fi

unlanded="$work/unlanded"
make_repo "$unlanded"
git_ "$unlanded" checkout -q -b issue-43-lane
printf 'nobody else has this\n' >"$unlanded/unmerged.txt"
git_ "$unlanded" add -A
git_ "$unlanded" commit -qm unlanded
git_ "$unlanded" push -qu origin issue-43-lane
git_ "$unlanded" checkout -q master
reap "$unlanded" --branches --apply >"$work/out.unlanded.txt" 2>&1
if git_ "$unlanded" rev-parse --verify --quiet issue-43-lane >/dev/null 2>&1; then
  ok "a branch whose change master does not hold is KEPT"
else
  bad "a branch with unmerged work was deleted — the content test is not fail-closed"
fi

held="$work/held"
make_repo "$held"
held_lane="$work/lane-held"
git_ "$held" worktree add -q -b issue-44-lane "$held_lane"
git_ "$held" push -qu origin issue-44-lane
held_out="$(reap "$held" --branches --apply)"
if printf '%s' "$held_out" | grep -q 'issue-44-lane'; then
  bad "a branch a worktree is standing on was treated as a candidate"
elif git_ "$held" rev-parse --verify --quiet issue-44-lane >/dev/null 2>&1; then
  ok "a branch checked out in a worktree is not a candidate at all"
else
  bad "a checked-out branch was deleted"
fi

echo "== prune-worktrees: the check is wired, not a formality =="
# `set +u` around the sourcing: the lister reads an optional `CHECK_DENYLIST`. The
# match is a `case` and not `grep -q` because `grep -q` exits on its first match,
# SIGPIPEs the lister, and — under this script's `pipefail` — reports the whole
# pipeline as failed, i.e. it would fail for a reason unrelated to the wiring.
discovered="$(
  set +u
  # shellcheck source=/dev/null
  . "$root/scripts/discover-checks.sh"
  discover_check_scripts 2>/dev/null
)"
case "$discovered" in
  *"prune-worktrees|bash scripts/check-prune-worktrees.sh"*)
    ok "scripts/discover-checks.sh wires this check into make verify" ;;
  *)
    bad "this check is not auto-discovered — nothing runs it" ;;
esac

if [ "$fails" -eq 0 ]; then
  echo "check-prune-worktrees: OK — declared runtime state cannot pin a worktree, and a lane branch is reaped only when its content is provably on master"
  exit 0
fi
echo "check-prune-worktrees: NOT-OK — $fails rule(s) broken" >&2
exit 1
