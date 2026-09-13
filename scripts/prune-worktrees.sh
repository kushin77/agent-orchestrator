#!/usr/bin/env bash
# prune-worktrees.sh — reclaim stale lane worktrees, safely.
#
# Every lane gets a worktree, so they accumulate. Observed on this machine
# (2026-09-13): 11 stale worktrees under the 16 GiB /tmp tmpfs plus 8 under
# ~/ao-worktrees — the tmpfs ones are also the RAM hazard the repo's own
# conventions warn about, and the operator was left to clean them up by hand.
# That is a human step, so it becomes code.
#
# A worktree is REMOVABLE only when all three hold:
#   1. it is not the current worktree and not the repo's own checkout;
#   2. no live process has it as its cwd;
#   3. its HEAD commit is preserved outside it — reachable from origin/master or
#      contained in a remote branch. Anything unreachable is reported, never
#      deleted: unmerged work belongs to its lane.
#
# Default is a dry run. Pass --apply to remove.
#
# Exit-code contract: 0 OK / 1 NOT-OK (stale worktrees found in --check) /
# 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/prune-worktrees.sh            # report what would be removed
#   bash scripts/prune-worktrees.sh --apply    # remove the safe ones
#   bash scripts/prune-worktrees.sh --check    # exit 1 if any stale exist
set -uo pipefail

# The repo to operate on is the one this script is RUN IN, not the one it lives
# in: deriving it from $BASH_SOURCE made the tool ignore its cwd and act on the
# operator's checkout instead (caught by this script's own tests, which were
# passing for that reason).
root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$root" ]; then
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$root" || exit 2

apply=0
check=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    --check) check=1 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "prune-worktrees: unknown argument $arg" >&2; exit 2 ;;
  esac
done

if ! git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
  echo "prune-worktrees: CANNOT-ASSESS — not a git repository" >&2
  exit 2
fi

current="$root"

# cwd of every live process — a worktree in use must never be removed.
live_cwds="$(mktemp)"
trap 'rm -f "$live_cwds"' EXIT
for link in /proc/[0-9]*/cwd; do
  readlink "$link" 2>/dev/null || true
done > "$live_cwds"

preserved() { # preserved <sha> — is this commit reachable outside the worktree?
  local sha="$1"
  [ -z "$sha" ] && return 1
  if git -C "$root" merge-base --is-ancestor "$sha" origin/master 2>/dev/null; then
    return 0
  fi
  [ -n "$(git -C "$root" branch -r --contains "$sha" 2>/dev/null | head -1)" ]
}

stale=0
unsafe=0
while read -r path sha; do
  [ -z "$path" ] && continue
  case "$path" in
    "$current") continue ;;
  esac
  dirty="$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$dirty" != "0" ]; then
    printf '  KEEP   %s — %s uncommitted file(s)\n' "$path" "$dirty"
    unsafe=$((unsafe + 1))
    continue
  fi
  if grep -qxF -- "$path" "$live_cwds"; then
    printf '  KEEP   %s — in use by a live process\n' "$path"
    unsafe=$((unsafe + 1))
    continue
  fi
  if ! preserved "$sha"; then
    printf '  KEEP   %s — HEAD %s is not preserved on origin (unmerged lane work)\n' "$path" "$sha"
    unsafe=$((unsafe + 1))
    continue
  fi
  stale=$((stale + 1))
  if [ "$apply" -eq 1 ]; then
    git -C "$root" worktree remove --force "$path" >/dev/null 2>&1 \
      && printf '  REMOVED %s (HEAD %s preserved on origin)\n' "$path" "$sha" \
      || printf '  KEEP   %s — removal failed\n' "$path"
  else
    printf '  STALE  %s — removable (HEAD %s preserved on origin)\n' "$path" "$sha"
  fi
done < <(git -C "$root" worktree list --porcelain | awk '/^worktree /{p=$2} /^HEAD /{print p, $2}' | grep -v "^$current ")

git -C "$root" worktree prune 2>/dev/null || true

echo "prune-worktrees: $stale stale, $unsafe kept (dirty, in use, or unpreserved)"
if [ "$check" -eq 1 ] && [ "$stale" -gt 0 ]; then
  echo "prune-worktrees: NOT-OK — $stale stale worktree(s); run with --apply" >&2
  exit 1
fi
exit 0
