#!/usr/bin/env bash
# check-prune-worktrees.sh — the reaper's rules, provoked on a throwaway repo (#830).
#
# `scripts/prune-worktrees.sh` is the only thing that reclaims lane worktrees and
# lane branches, and until this check landed it had a unit suite but no GATE:
# nothing in `make verify` exercised it, so every rule it states was decorative.
# Its safety rests on what it KEEPS, so the KEEPs are provoked as loudly as the
# REMOVEs — a rule that cannot fail is a formality (GR-12). The liveness predicate
# (#1159) is provoked by actually HOLDING a tree open (an fd under it, with the
# holder's cwd elsewhere), and by MUTANTS of the tool that revert each half of the
# predicate — so the arm is proven to catch the regression it exists for, rather
# than merely passing while the code says what it says.
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
holder_pids=()
holder_pid=""
cleanup() { # cleanup — release the fixtures' holders by PID, then the scratch tree
  release_holders
  rm -rf "$work"
}
trap cleanup EXIT

fails=0
ok() { printf '  OK    %s\n' "$1"; }
bad() {
  printf '  FAIL  %s\n' "$1"
  fails=$((fails + 1))
}

# `bash -n` cannot see an unbalanced quote in a label, and the labels below are
# printed verbatim on every arm, so they are kept to one line each.
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

declare_runtime_state_with_prefixes() { # declare_runtime_state_with_prefixes <repo> <paths...> -- <prefixes...>
  local repo="$1"
  shift
  local paths=() prefixes=() target=paths arg
  for arg in "$@"; do
    if [ "$arg" = "--" ]; then
      target=prefixes
      continue
    fi
    if [ "$target" = "paths" ]; then
      paths+=("$arg")
    else
      prefixes+=("$arg")
    fi
  done
  local body="" prefix_body="" path prefix
  for path in "${paths[@]}"; do body="$body\"$path\", "; done
  for prefix in "${prefixes[@]}"; do prefix_body="$prefix_body\"$prefix\", "; done
  mkdir -p "$repo/governance/isolation"
  {
    printf 'MACHINE_MANAGED_PATHS: tuple[str, ...] = (%s)\n' "$body"
    printf 'MACHINE_MANAGED_PREFIXES: tuple[str, ...] = (%s)\n' "$prefix_body"
  } >"$repo/governance/isolation/worktree.py"
}

reap() { # reap <repo> <args...>
  local repo="$1"
  shift
  (cd "$repo" && bash "$tool" "$@") 2>&1
}

reap_with() { # reap_with <tool-path> <repo> <args...> — run a MUTANT of the tool
  local mutant="$1" repo="$2"
  shift 2
  (cd "$repo" && bash "$mutant" "$@") 2>&1
}

sha_of() { # sha_of <file>
  sha256sum "$1" | awk '{print $1}'
}

# Copy the tool, apply a ONE-TOKEN edit to it, and prove the edit changed bytes:
# a mutant that changes nothing proves nothing, and a moved anchor must be a loud
# FAIL rather than a silent pass. rc 0 mutated / 3 the edit was a NO-OP /
# 4 the mutant could not be built.
make_mutant() { # make_mutant <dst> <sed-expr>
  local dst="$1" expr="$2"
  cp "$tool" "$dst" 2>/dev/null || return 4
  [ "$(sha_of "$dst")" = "$sha_pristine" ] || return 4
  sed -i -E "$expr" "$dst" 2>/dev/null || return 4
  [ "$(sha_of "$dst")" != "$sha_pristine" ] || return 3
  return 0
}

# A process whose cwd is ELSEWHERE (+$PWD of the check), holding ONE open file
# descriptor under <path>. Its pid is left in $holder_pid and recorded for the
# cleanup trap: holders are killed by PID, never by a pattern, because a pattern
# would take other lanes' workers with them.
hold_open() { # hold_open <path>
  ( exec 9<"$1" && while :; do sleep 5; done ) &
  holder_pid=$!
  holder_pids+=("$holder_pid")
}

# A process whose cwd is INSIDE <dir>, holding nothing open under it.
hold_cwd() { # hold_cwd <dir>
  ( cd "$1" 2>/dev/null || exit 1; while :; do sleep 5; done ) &
  holder_pid=$!
  holder_pids+=("$holder_pid")
}
release_holders() { # release_holders — kill the fixtures' holders by the pids we recorded
  local pid
  [ "${#holder_pids[@]}" -gt 0 ] || return 0
  for pid in "${holder_pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  holder_pids=()
}

holder_cwd() { # holder_cwd <pid> — where a holder's cwd actually is, measured not assumed
  readlink "/proc/$1/cwd" 2>/dev/null || echo UNREADABLE
}

holder_open_paths() { # holder_open_paths <pid> — every path that pid holds open
  find "/proc/$1/fd" -mindepth 1 -maxdepth 1 -printf '%l\n' 2>/dev/null
}

# A forked child applies its redirection (and its `cd`) AFTER the fork returns, so
# the fixture's evidence is observed with a BOUNDED POLL rather than assumed to be
# there on the next line: asserting immediately makes the arm a race, and a gate
# that fails at random teaches nothing.
wait_for_open() { # wait_for_open <pid> <path-under-the-tree>
  local pid="$1" want="$2" i=0 targets
  while [ "$i" -lt 200 ]; do
    targets="$(holder_open_paths "$pid")"
    case "$targets" in
      *"$want"*) return 0 ;;
    esac
    i=$((i + 1))
    sleep 0.05
  done
  return 1
}

wait_for_cwd() { # wait_for_cwd <pid> <dir-prefix>
  local pid="$1" want="$2" i=0 cwd
  while [ "$i" -lt 200 ]; do
    cwd="$(holder_cwd "$pid")"
    case "$cwd" in
      "$want"*) return 0 ;;
    esac
    i=$((i + 1))
    sleep 0.05
  done
  return 1
}

clean_lane() { # clean_lane <repo> <dir> — a clean, preserved, unclaimed worktree
  git_ "$1" worktree add -q --detach "$2" origin/master
}

add_tracked_dir() { # add_tracked_dir <repo> <name> — TRACKED, so a worktree carrying it stays clean
  local repo="$1" name="$2"
  mkdir -p "$repo/$name"
  printf 'seed\n' >"$repo/$name/keep.txt"
  git_ "$repo" add -A
  git_ "$repo" commit -qm "seed $name"
  git_ "$repo" push -qu origin master
  git_ "$repo" fetch -q origin
}

oneline() { # oneline <text> — a report rendered on ONE line, so an --apply report stays readable
  printf '%s' "$1" | tr '\n' '|'
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

echo "== prune-worktrees: declared PREFIXES — .fleet/ runtime state (#1265) =="

prefix_repo="$work/prefix"
make_repo "$prefix_repo"
declare_runtime_state_with_prefixes "$prefix_repo" ".board/focus.json" -- ".fleet/"

fleet_only="$work/lane-fleet-only"
dirty_lane "$prefix_repo" "$fleet_only" ".fleet/x"
reap "$prefix_repo" --apply >"$work/out.fleet_only.txt" 2>&1
if [ -d "$fleet_only" ]; then
  bad "a worktree dirty ONLY with untracked .fleet/x was kept — the prefix declaration is not read"
else
  ok "a worktree dirty only with untracked .fleet/x is machine-managed and reapable"
fi

fleet_plus_real="$work/lane-fleet-plus-real"
dirty_lane "$prefix_repo" "$fleet_plus_real" ".fleet/x" "lane-work.txt"
reap "$prefix_repo" --apply >"$work/out.fleet_plus_real.txt" 2>&1
if [ -d "$fleet_plus_real" ]; then
  ok ".fleet/ plus a real modified/untracked file still keeps its worktree"
else
  bad "a worktree with .fleet/ plus real lane work was removed — the prefix exemption is too wide"
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
# Bash-native substring test (issue #1145): a quiet grep exits on its first
# match and can SIGPIPE its producer, so under pipefail it can report a
# needle that IS present as absent. Same input, no producer to kill.
if [ "$rc" -eq 1 ] && [[ "$report" == *"STALE  branch issue-42-lane"* ]]; then
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
# Bash-native substring test (issue #1145), same reason as above.
if [[ "$held_out" == *"issue-44-lane"* ]]; then
  bad "a branch a worktree is standing on was treated as a candidate"
elif git_ "$held" rev-parse --verify --quiet issue-44-lane >/dev/null 2>&1; then
  ok "a branch checked out in a worktree is not a candidate at all"
else
  bad "a checked-out branch was deleted"
fi

echo "== prune-worktrees: liveness is cwd OR an open file under the tree (#1159) =="
# The predicate the reaper landed with was `/proc/*/cwd` ALONE. A peer driving a
# worktree from the SHARED shell keeps its cwd elsewhere and holds the tree open
# through file descriptors, so a LIVE tree was classified removable (10 live
# `.claude/worktrees/agent-*` trees plus 4 scratch trees, measured on the real
# tree) — and `--apply` now runs unattended from cron. Each fixture below holds a
# tree open and asserts KEEP; the mutant that reverts the predicate must then
# REMOVE it, which is what makes each KEEP a control rather than a coincidence.

sha_pristine="$(sha_of "$tool")"

# --- control 1: the tree's ONLY liveness evidence is an open file descriptor ---
fd_repo="$work/fd-only"
make_repo "$fd_repo"
fd_lane="$work/lane-fd-only"
clean_lane "$fd_repo" "$fd_lane"
hold_open "$fd_lane/README.md"
fd_holder="$holder_pid"

# The premise, ASSERTED not assumed: if the holder's cwd were under the tree, a
# KEEP would be explained by the OLD predicate too and this arm would prove
# nothing. Both halves are printed, so a mismatch is visible instead of inferred.
if wait_for_open "$fd_holder" "$fd_lane/README.md"; then
  ok "fixture armed: pid $fd_holder holds $fd_lane/README.md open"
else
  bad "fixture NOT armed: pid $fd_holder holds no path under the tree, so this arm proves nothing (ACTUAL: $(oneline "$(holder_open_paths "$fd_holder")"))"
fi
fd_holder_cwd="$(holder_cwd "$fd_holder")"
case "$fd_holder_cwd" in
  "$fd_lane"|"$fd_lane"/*)
    bad "premise fails: the fd holder (pid $fd_holder) has its cwd UNDER the tree ($fd_holder_cwd)" ;;
  *)
    ok "premise holds: holder pid $fd_holder cwd=$fd_holder_cwd, which is not under the tree" ;;
esac

fd_report="$(reap "$fd_repo" --apply)"
if [ -d "$fd_lane" ] && [[ "$fd_report" == *"in use by a live process"* ]]; then
  ok "a tree whose only liveness evidence is an open fd (cwd elsewhere) is KEEP"
else
  bad "a tree a live process holds open was not kept: present=$([ -d "$fd_lane" ] && echo yes || echo no) ACTUAL: $(oneline "$fd_report")"
fi

# The mutant that reverts the predicate to cwd-only must RED this arm.
mutant_cwd_only="$work/mutant-cwd-only.sh"
mutant_rc=0
make_mutant "$mutant_cwd_only" 's/^LIVE_SOURCES="cwd fd"$/LIVE_SOURCES="cwd"/' || mutant_rc=$?
case "$mutant_rc" in
  0)
    ok "mutant A built: LIVE_SOURCES=\"cwd\" (the pre-#1159 predicate); sha256 $sha_pristine -> $(sha_of "$mutant_cwd_only")" ;;
  3)
    bad "mutant A is a NO-OP: the LIVE_SOURCES anchor moved, so nothing was reverted and nothing is proven" ;;
  *)
    bad "mutant A could not be built (rc=$mutant_rc)" ;;
esac
if [ "$mutant_rc" -eq 0 ]; then
  mutant_a_report="$(reap_with "$mutant_cwd_only" "$fd_repo" --apply)"
  if [ -d "$fd_lane" ]; then
    bad "the cwd-only mutant KEPT the fd-held tree, so this control cannot catch the regression it exists for (ACTUAL: $(oneline "$mutant_a_report"))"
  else
    ok "the cwd-only mutant REMOVES the fd-held tree — the open-file half is load-bearing (ACTUAL: $(oneline "$mutant_a_report"))"
  fi
  cp "$tool" "$mutant_cwd_only"
  restored_sha="$(sha_of "$mutant_cwd_only")"
  if [ "$restored_sha" = "$sha_pristine" ]; then
    ok "the mutant's own path restored from the tool, byte-identical (sha256 $restored_sha)"
  else
    bad "the restore is NOT byte-identical: $restored_sha != $sha_pristine"
  fi
fi

# --- control 2: the "under" half — a cwd INSIDE the tree, not equal to it ---
sub_repo="$work/subdir-cwd"
make_repo "$sub_repo"
add_tracked_dir "$sub_repo" "sub"
sub_lane="$work/lane-subdir-cwd"
clean_lane "$sub_repo" "$sub_lane"
hold_cwd "$sub_lane/sub"
sub_holder="$holder_pid"
if wait_for_cwd "$sub_holder" "$sub_lane/sub"; then
  ok "premise holds: holder pid $sub_holder cwd=$(holder_cwd "$sub_holder") — UNDER the tree, not equal to it"
else
  bad "premise fails: holder pid $sub_holder cwd=$(holder_cwd "$sub_holder"), which is not under the tree"
fi

sub_report="$(reap "$sub_repo" --apply)"
if [ -d "$sub_lane" ]; then
  ok "a tree a live process holds by a SUBDIRECTORY cwd is KEEP (ACTUAL: $(oneline "$sub_report"))"
else
  bad "a tree with a live process in a subdirectory of it was removed (ACTUAL: $(oneline "$sub_report"))"
fi

mutant_exact="$work/mutant-match-exact.sh"
exact_rc=0
make_mutant "$mutant_exact" 's/^LIVE_MATCH="under"$/LIVE_MATCH="exact"/' || exact_rc=$?
case "$exact_rc" in
  0)
    ok "mutant B built: LIVE_MATCH=\"exact\" (equality only); sha256 $sha_pristine -> $(sha_of "$mutant_exact")" ;;
  3)
    bad "mutant B is a NO-OP: the LIVE_MATCH anchor moved, so nothing was reverted and nothing is proven" ;;
  *)
    bad "mutant B could not be built (rc=$exact_rc)" ;;
esac
if [ "$exact_rc" -eq 0 ]; then
  mutant_b_report="$(reap_with "$mutant_exact" "$sub_repo" --apply)"
  if [ -d "$sub_lane" ]; then
    bad "the exact-match mutant KEPT the subdirectory-cwd tree (ACTUAL: $(oneline "$mutant_b_report"))"
  else
    ok "the exact-match mutant REMOVES it — the boundary-'under' half is load-bearing (ACTUAL: $(oneline "$mutant_b_report"))"
  fi
fi

# --- control 3: a held tree must not keep its NAME-PREFIX neighbour, and a
# genuinely stale, preserved, unclaimed tree must STILL be reaped --------------
anchor_repo="$work/anchor"
make_repo "$anchor_repo"
anchor_lane="$work/lane-anchor"
neighbour_lane="$work/lane-anchor-neighbour"
clean_lane "$anchor_repo" "$anchor_lane"
clean_lane "$anchor_repo" "$neighbour_lane"
hold_open "$anchor_lane/README.md"
if wait_for_open "$holder_pid" "$anchor_lane/README.md"; then
  ok "over-widening fixture armed: pid $holder_pid holds $anchor_lane/README.md open"
else
  bad "over-widening fixture NOT armed: nothing holds that tree, so the KEEP below would prove nothing"
fi
anchor_report="$(reap "$anchor_repo" --apply)"
if [ -d "$anchor_lane" ]; then
  ok "the held tree is KEEP beside an unheld twin (ACTUAL: $(oneline "$anchor_report"))"
else
  bad "the held tree was removed (ACTUAL: $(oneline "$anchor_report"))"
fi
if [ ! -d "$neighbour_lane" ]; then
  ok "a genuinely stale, preserved, unclaimed tree is STILL REAPED — nothing here refuses everything"
else
  bad "lane-anchor-neighbour was kept although nothing holds it: the predicate over-matches by name prefix"
fi
release_holders

# Nothing above may have touched the tool itself: the mutants are copies.
if [ "$(sha_of "$tool")" = "$sha_pristine" ]; then
  ok "the tool is unchanged by this check (sha256 $sha_pristine)"
else
  bad "this check MODIFIED the tool: $(sha_of "$tool") != $sha_pristine"
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
  echo "check-prune-worktrees: OK — liveness is cwd OR an open file under the tree, declared runtime state cannot pin a worktree, and a lane branch is reaped only when its content is provably on master"
  exit 0
fi
echo "check-prune-worktrees: NOT-OK — $fails rule(s) broken" >&2
exit 1
