#!/usr/bin/env bash
# ============================================================================
# scripts/lane-workspace-self-test.sh — GR-12 adversarial self-test for
# ops/lane-workspace.sh (ADR-0036 / GR-30).
#
# Plants each of the four violations the helper must refuse, asserts RC!=0
# on the LIVE helper, then re-runs the same scenario against a COPY of the
# helper with that one guard deleted, asserting the violation now PASSES —
# proving the guard is load-bearing, not incidental. The live script is never
# weakened.
#
# Exit: 0 all eight assertions correct · 1 any assertion wrong.
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="$ROOT/ops/lane-workspace.sh"
FAIL=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
badr() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=1; }

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# --- Build a minimal fleet fixture: bare "origin" + a local mirror checkout,
# default branch "main", a feature branch parked with different content. -----
BARE="$WORK/origin.git"
MIRROR="$WORK/repos/fixture-repo"
mkdir -p "$WORK/repos"
git init --bare -q "$BARE"

git init -q "$MIRROR"
git -C "$MIRROR" config user.email test@example.com
git -C "$MIRROR" config user.name test
git -C "$MIRROR" checkout -qb main
echo "main content" > "$MIRROR/marker.txt"
git -C "$MIRROR" add marker.txt
git -C "$MIRROR" commit -qm "main commit"
git -C "$MIRROR" remote add origin "$BARE"
git -C "$MIRROR" push -q -u origin main
git -C "$BARE" symbolic-ref HEAD refs/heads/main
git -C "$MIRROR" symbolic-ref -q refs/remotes/origin/HEAD >/dev/null 2>&1 || \
  git -C "$MIRROR" remote set-head origin main

# A parked feature branch whose content differs from main — this is the
# "stale mirror read" shape from the RCA: the mirror is NOT sitting on main.
git -C "$MIRROR" checkout -qb parked-feature
echo "feature content" > "$MIRROR/marker.txt"
git -C "$MIRROR" commit -qam "feature commit"
git -C "$MIRROR" push -q origin parked-feature
git -C "$MIRROR" checkout -q main

# A fake CMR hub fixture so the helper's registry/lock paths are isolated.
HUB="$WORK/hub"
mkdir -p "$HUB/scripts/lib" "$HUB/ops/.state"
cp "$ROOT/scripts/lib/lock.sh" "$HUB/scripts/lib/lock.sh"
git init -q "$HUB"
git -C "$HUB" config user.email test@example.com
git -C "$HUB" config user.name test
: > "$HUB/README.md"
git -C "$HUB" add README.md
git -C "$HUB" commit -qm init

# --- Fixture caller cwd: a LINKED worktree, never the invoking process's own
# cwd. This is the environment-independence fix (found in review): guards 1/
# 3/4 must exercise the helper from a location the guard-2 cwd check ALLOWS,
# regardless of whether `make lane-workspace-self-test` itself is invoked from
# a primary checkout (e.g. /home/akushnir/cmr, as `make verify` normally runs)
# or from a linked worktree. Only guard 2's own scenario intentionally uses a
# primary checkout as cwd, because that IS the violation under test — every
# other guard's cwd is fixed to this fixture so the self-test's verdict does
# not depend on the caller's own location.
CALLER_WT="$WORK/caller-wt"
git -C "$HUB" worktree add -q -b caller-fixture "$CALLER_WT" >/dev/null 2>&1

run_helper() {
  local script="$1"; shift
  env -i PATH="$PATH" HOME="$HOME" \
    CMR_LANE_MIRROR_BASE="$WORK/repos" \
    CMR_LANE_ALLOW_CLONE=0 \
    "$@" \
    bash "$script" fixture-repo "$1_lane_$$" 2>"$WORK/stderr.$$" ; local rc=$?
  return $rc
}

# The real hub root the helper derives things from is `$SELF/..`; we can't
# relocate that for the live script, so instead we invoke the helper with a
# copy placed inside a throwaway hub dir tree, mirroring $HUB layout.
mkdir -p "$HUB/ops"
cp "$HELPER" "$HUB/ops/lane-workspace.sh"

weaken_copy() {
  # $1 = output path, $2 = sed program removing/neutering one guard
  cp "$HUB/ops/lane-workspace.sh" "$1"
  sed -i "$2" "$1"
}

echo "== ops/lane-workspace.sh self-test (ADR-0036 / GR-30) =="

# ---------------------------------------------------------------------------
echo "-- guard 1: missing session marker --"
if ! ( cd "$CALLER_WT" && env -i PATH="$PATH" HOME="$HOME" CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$HUB/ops/lane-workspace.sh" fixture-repo lane-g1 ) 2>"$WORK/g1.err"; then
  pass "live helper refuses with no CMR_SESSION_ID (RC!=0)"
else
  badr "live helper accepted a run with no session marker"
fi
[[ -d "$MIRROR/.claude/worktrees/lane-g1" ]] && badr "guard 1: worktree was created despite refusal (orphan)" || pass "guard 1: no worktree left behind"

weaken_copy "$WORK/weak-g1.sh" 's/^\[\[ -n "\$SESSION_ID" \]\] || fail.*/SESSION_ID="\${SESSION_ID:-unset}"  # guard 1 removed for self-test/'
if ( cd "$CALLER_WT" && env -i PATH="$PATH" HOME="$HOME" CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$WORK/weak-g1.sh" fixture-repo lane-g1-weak ) >/dev/null 2>"$WORK/g1w.err"; then
  pass "weakened copy (guard removed) now ACCEPTS the same violation — guard is load-bearing"
else
  badr "weakened copy still refused — guard 1 test does not isolate the guard: $(cat "$WORK/g1w.err")"
fi
git -C "$MIRROR" worktree remove --force "$MIRROR/.claude/worktrees/lane-g1-weak" >/dev/null 2>&1 || true
git -C "$MIRROR" branch -D lane/lane-g1-weak >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
echo "-- guard 2: cwd inside a shared (primary) checkout --"
if ! ( cd "$MIRROR" && env -i PATH="$PATH" HOME="$HOME" CMR_SESSION_ID=s2 CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$HUB/ops/lane-workspace.sh" fixture-repo lane-g2 ) 2>"$WORK/g2.err"; then
  pass "live helper refuses when cwd is the primary checkout (RC!=0)"
else
  badr "live helper accepted a run from inside a primary checkout"
fi

weaken_copy "$WORK/weak-g2.sh" '/cwd_gitdir="\$cwd_commondir"/{N;/PRIMARY (shared) checkout/{N;N;d}}'
# simpler: strip the whole guard block between markers
awk '/# --- Guard 2:/{skip=1} skip && /^fi$/{skip=0; next} skip{next} {print}' "$HUB/ops/lane-workspace.sh" > "$WORK/weak-g2.sh"
if ( cd "$MIRROR" && env -i PATH="$PATH" HOME="$HOME" CMR_SESSION_ID=s2 CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$WORK/weak-g2.sh" fixture-repo lane-g2-weak ) >/dev/null 2>"$WORK/g2w.err"; then
  pass "weakened copy (guard removed) now ACCEPTS a run from inside the primary checkout"
else
  badr "weakened copy still refused — guard 2 test does not isolate the guard: $(cat "$WORK/g2w.err")"
fi
git -C "$MIRROR" worktree remove --force "$MIRROR/.claude/worktrees/lane-g2-weak" >/dev/null 2>&1 || true
git -C "$MIRROR" branch -D lane/lane-g2-weak >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
echo "-- guard 3: fetch failure (mirror we cannot prove is current) --"
BAD_MIRROR="$WORK/repos/fixture-repo-badfetch"
git clone -q "$MIRROR" "$BAD_MIRROR" 2>/dev/null
git -C "$BAD_MIRROR" remote set-url origin "$WORK/does-not-exist.git"
if ! ( cd "$CALLER_WT" && env -i PATH="$PATH" HOME="$HOME" CMR_SESSION_ID=s3 CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$HUB/ops/lane-workspace.sh" fixture-repo-badfetch lane-g3 ) 2>"$WORK/g3.err"; then
  pass "live helper refuses when the mirror's fetch fails (RC!=0) — never provisions from an unverified mirror"
else
  badr "live helper provisioned from a mirror whose fetch failed"
fi

awk '/^log "fetching origin/{skip=1} skip && /^fi$/{skip=0; next} skip{next} {print}' "$HUB/ops/lane-workspace.sh" > "$WORK/weak-g3.sh"
if ( cd "$CALLER_WT" && env -i PATH="$PATH" HOME="$HOME" CMR_SESSION_ID=s3 CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$WORK/weak-g3.sh" fixture-repo-badfetch lane-g3-weak ) >/dev/null 2>"$WORK/g3w.err"; then
  pass "weakened copy (fetch-check removed) now ACCEPTS a mirror with a broken remote"
else
  badr "weakened copy still refused — guard 3 test does not isolate the guard: $(cat "$WORK/g3w.err")"
fi
git -C "$BAD_MIRROR" worktree remove --force "$BAD_MIRROR/.claude/worktrees/lane-g3-weak" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
echo "-- guard 4: base resolved from the mirror's parked branch, not origin/HEAD --"
PARKED_MIRROR="$WORK/repos/fixture-repo-parked"
git clone -q "$BARE" "$PARKED_MIRROR" 2>/dev/null
git -C "$PARKED_MIRROR" checkout -qb parked-feature origin/parked-feature
if ( cd "$CALLER_WT" && env -i PATH="$PATH" HOME="$HOME" CMR_SESSION_ID=s4 CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$HUB/ops/lane-workspace.sh" fixture-repo-parked lane-g4 ) >"$WORK/g4.out" 2>"$WORK/g4.err"; then
  WT4="$(cat "$WORK/g4.out")"
  if [[ -f "$WT4/marker.txt" ]] && grep -q "main content" "$WT4/marker.txt"; then
    pass "live helper based the worktree on origin/HEAD (main), ignoring the mirror's parked branch"
  else
    badr "live helper based the worktree on the parked branch's content, not origin/HEAD"
  fi
else
  badr "live helper failed unexpectedly on a mirror parked on a feature branch: $(cat "$WORK/g4.err")"
fi

awk '{
  if ($0 ~ /^DEFAULT_REF="\$\(git -C "\$MIRROR_ROOT" symbolic-ref/) { print "DEFAULT_REF=\"refs/remotes/origin/$(git -C \"$MIRROR_ROOT\" rev-parse --abbrev-ref HEAD)\""; skip=1; next }
  if (skip && $0 ~ /^fi$/) { skip=0; next }
  if (skip) next
  print
}' "$HUB/ops/lane-workspace.sh" > "$WORK/weak-g4.sh"
if ( cd "$CALLER_WT" && env -i PATH="$PATH" HOME="$HOME" CMR_SESSION_ID=s4 CMR_LANE_MIRROR_BASE="$WORK/repos" CMR_LANE_ALLOW_CLONE=0 \
    bash "$WORK/weak-g4.sh" fixture-repo-parked lane-g4-weak ) >"$WORK/g4w.out" 2>"$WORK/g4w.err"; then
  WT4W="$(cat "$WORK/g4w.out")"
  if [[ -f "$WT4W/marker.txt" ]] && grep -q "feature content" "$WT4W/marker.txt"; then
    pass "weakened copy (origin/HEAD resolution replaced by mirror's checked-out branch) now bases off the PARKED branch"
  else
    badr "weakened copy did not reproduce the parked-branch violation — guard 4 test does not isolate the guard"
  fi
else
  badr "weakened copy failed to run: $(cat "$WORK/g4w.err")"
fi

echo
if [[ "$FAIL" -eq 0 ]]; then
  echo "lane-workspace-self-test: ALL PASS (4 guards each proven load-bearing)"
else
  echo "lane-workspace-self-test: FAIL — see above"
fi
exit "$FAIL"
