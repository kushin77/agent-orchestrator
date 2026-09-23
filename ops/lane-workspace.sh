#!/usr/bin/env bash
# ============================================================================
# ops/lane-workspace.sh — ADR-0036 / GR-30 workspace provisioning helper.
#
# Provisions a lane's workspace as a `git worktree` off a LOCAL MIRROR instead
# of a network clone: seconds instead of a full fetch, no new credential copy
# (a worktree shares the mirror's .git), and an isolated tree a lane cannot
# confuse with someone else's.
#
# THE INVARIANT THIS ENFORCES (see docs/decision-records/ADR-0036-local-mirror-workspace-provisioning.md):
#   A local checkout is an object cache for reads and a base for worktrees —
#   never a write target.
#
#   bash ops/lane-workspace.sh <repo> <lane-name>
#
#   <repo>       repo name as checked out under /home/akushnir/<repo>, or the
#                CMR hub itself ("cmr" / "CMR").
#   <lane-name>  short lane identifier; becomes the worktree dirname and the
#                branch suffix lane/<lane-name>.
#
# On success: prints ONLY the absolute worktree path to stdout. Everything
# else — diagnostics, which source was used, guard failures — goes to stderr.
# Exit codes: 0 ok · 1 guard/usage failure · 2 environment failure (fetch,
# git, lock).
#
# ENV:
#   CMR_SESSION_ID          session identifier for the ownership row (required
#                            — standing owner mandate: anything an agent
#                            touches leaves a session marker). Falls back to
#                            CLAUDE_SESSION_ID.
#   CMR_LANE_MIRROR_BASE    where fleet repos are checked out (default
#                            /home/akushnir).
#   CMR_LANE_ALLOW_CLONE    1 (default) allow network-clone fallback when no
#                            local mirror exists; 0 refuses instead.
#   CMR_LOCK_ROOT           lock dir for the registry write (default
#                            <hub>/.claude/locks).
# ============================================================================
set -euo pipefail

log() { printf '[lane-workspace] %s\n' "$*" >&2; }
fail() { log "FAIL: $*"; exit "${2:-1}"; }

usage() {
  cat >&2 <<'EOF'
usage: bash ops/lane-workspace.sh <repo> <lane-name>
EOF
  exit 2
}

[ $# -eq 2 ] || usage
REPO="$1"
LANE="$2"
case "$REPO" in ''|*[!A-Za-z0-9._-]*) fail "invalid repo name: '$REPO'" 2;; esac
case "$LANE" in ''|*[!A-Za-z0-9._-]*) fail "invalid lane name: '$LANE'" 2;; esac

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB_ROOT="$(cd "$SELF/.." && pwd)"
# If we are ourselves inside a linked worktree of CMR, resolve the true hub
# root (the registry and lock root live there, not under the worktree).
common_dir="$(git -C "$HUB_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "$common_dir" ]]; then
  HUB_ROOT="$(dirname "$common_dir")"
fi

# --- Guard 1: missing session marker fails BEFORE anything is created. -----
SESSION_ID="${CMR_SESSION_ID:-${CLAUDE_SESSION_ID:-}}"
[[ -n "$SESSION_ID" ]] || fail "no session marker set (CMR_SESSION_ID / CLAUDE_SESSION_ID) — refusing to provision an unowned worktree"

# --- Guard 2: refuse if cwd is inside a shared (primary) checkout. ---------
# Same predicate as guardrails/hooks/primary-worktree-guard.sh: a primary
# worktree's git-dir and git-common-dir are the same path; a linked worktree's
# are not. Run this against the CALLER's cwd, not our own directory.
cwd_gitdir="$(git -C "$PWD" rev-parse --path-format=absolute --git-dir 2>/dev/null || true)"
cwd_commondir="$(git -C "$PWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "$cwd_gitdir" && "$cwd_gitdir" == "$cwd_commondir" ]]; then
  fail "cwd ($PWD) is a PRIMARY (shared) checkout, not a linked worktree — never provision from inside a shared checkout. cd out, or run from an existing lane worktree."
fi

MIRROR_BASE="${CMR_LANE_MIRROR_BASE:-/home/akushnir}"
ALLOW_CLONE="${CMR_LANE_ALLOW_CLONE:-1}"

# --- Resolve the repo: local mirror preferred, network clone fallback. -----
resolve_repo_root() {
  local name="$1"
  case "$name" in
    cmr|CMR) printf '%s' "$HUB_ROOT"; return 0;;
  esac
  local candidate="$MIRROR_BASE/$name"
  if [[ -d "$candidate/.git" ]]; then
    printf '%s' "$candidate"
    return 0
  fi
  return 1
}

SOURCE_KIND=""
MIRROR_ROOT=""
if MIRROR_ROOT="$(resolve_repo_root "$REPO")"; then
  SOURCE_KIND="mirror"
  log "using local mirror: $MIRROR_ROOT"
else
  [[ "$ALLOW_CLONE" == "1" ]] || fail "no local mirror for '$REPO' under $MIRROR_BASE and CMR_LANE_ALLOW_CLONE=0" 2
  log "no local mirror for '$REPO' under $MIRROR_BASE — falling back to network clone"
  CLONE_DIR="$MIRROR_BASE/.lane-clones/$REPO"
  if [[ ! -d "$CLONE_DIR/.git" ]]; then
    mkdir -p "$(dirname "$CLONE_DIR")"
    ORIGIN_URL="$(git -C "$HUB_ROOT" remote get-url origin 2>/dev/null | sed "s#/CMR\(\.git\)\{0,1\}\$#/$REPO.git#")"
    [[ -n "$ORIGIN_URL" ]] || fail "cannot infer clone URL for '$REPO' (no mirror, no derivable origin)" 2
    git clone "$ORIGIN_URL" "$CLONE_DIR" >&2 || fail "network clone of '$REPO' failed" 2
  fi
  MIRROR_ROOT="$CLONE_DIR"
  SOURCE_KIND="clone"
fi

# --- Fetch the mirror FIRST — a stale mirror reproduces the false-blocker bug.
log "fetching origin in $MIRROR_ROOT ..."
if ! git -C "$MIRROR_ROOT" fetch origin --quiet 2>&2; then
  fail "fetch of origin failed in $MIRROR_ROOT — refusing to provision from a mirror we could not verify is current" 2
fi

# --- Resolve the default branch strictly from origin/HEAD, never HEAD and
# never whatever branch the mirror happens to be parked on. -----------------
DEFAULT_REF="$(git -C "$MIRROR_ROOT" symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null || true)"
if [[ -z "$DEFAULT_REF" ]]; then
  git -C "$MIRROR_ROOT" remote set-head origin -a >&2 2>/dev/null || true
  DEFAULT_REF="$(git -C "$MIRROR_ROOT" symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null || true)"
fi
[[ -n "$DEFAULT_REF" ]] || fail "cannot resolve origin/HEAD default branch in $MIRROR_ROOT" 2
DEFAULT_BRANCH="${DEFAULT_REF#refs/remotes/origin/}"
BASE_REF="refs/remotes/origin/$DEFAULT_BRANCH"

# --- Guard 3: base must be current with origin/HEAD (post-fetch, this ref
# IS origin/HEAD by construction — the guard is that we resolved it from a
# freshly-fetched remote-tracking ref, never from the mirror's checked-out
# branch or a cached/parked ref). Assert it actually resolves to a commit.
BASE_SHA="$(git -C "$MIRROR_ROOT" rev-parse -q --verify "$BASE_REF" 2>/dev/null || true)"
[[ -n "$BASE_SHA" ]] || fail "post-fetch, $BASE_REF does not resolve in $MIRROR_ROOT — base branch not current" 2
log "base: $BASE_REF @ $BASE_SHA (source=$SOURCE_KIND)"

# --- Worktree target: under the MIRROR's own .claude/worktrees/, so #667's
# reaper (which scans off the mirror's own hub root) can see and reap it. ---
WT_ROOT="$MIRROR_ROOT/.claude/worktrees"
WT_PATH="$WT_ROOT/$LANE"
BRANCH="lane/$LANE"

if git -C "$MIRROR_ROOT" worktree list --porcelain 2>/dev/null | grep -qx "worktree $WT_PATH"; then
  log "worktree already exists for lane '$LANE' — reusing (idempotent)"
  printf '%s\n' "$WT_PATH"
  exit 0
fi

mkdir -p "$WT_ROOT"

if git -C "$MIRROR_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  fail "branch '$BRANCH' already exists in $MIRROR_ROOT but is not attached to a live worktree at $WT_PATH — resolve the collision (rename the lane or reuse the existing branch) before retrying" 2
fi

if ! git -C "$MIRROR_ROOT" worktree add -b "$BRANCH" "$WT_PATH" "$BASE_REF" >&2; then
  fail "git worktree add failed for '$LANE' off $BASE_REF" 2
fi

# --- Record ownership. Reuses the DR-043 shared shell lock (#560) and the
# existing #667 registry — one lifecycle, no second registry, no second lock. -
LOCK_LIB="$HUB_ROOT/scripts/lib/lock.sh"
REGISTRY="$HUB_ROOT/ops/.state/worktrees.tsv"
cleanup_on_registry_failure() {
  # If we created the worktree but could not durably record it, remove it —
  # a half-made worktree is exactly the "unowned" shape the hygiene gate
  # flags, and leaving it behind defeats the ownership guarantee.
  log "registry write failed — rolling back worktree $WT_PATH (preserve-first: nothing was committed to it yet, safe to remove)"
  git -C "$MIRROR_ROOT" worktree remove --force "$WT_PATH" >&2 2>/dev/null || true
  git -C "$MIRROR_ROOT" branch -D "$BRANCH" >&2 2>/dev/null || true
}

if [[ -r "$LOCK_LIB" ]]; then
  # shellcheck source=../scripts/lib/lock.sh
  source "$LOCK_LIB"
  export CMR_LOCK_ROOT="${CMR_LOCK_ROOT:-$HUB_ROOT/.claude/locks}"
  export CMR_LOCK_TAG="lane-workspace"
  if ! lock_acquire "worktrees-registry" 10; then
    cleanup_on_registry_failure
    fail "could not acquire worktrees.tsv lock" 2
  fi
  trap 'lock_release "worktrees-registry"' EXIT
fi

mkdir -p "$(dirname "$REGISTRY")"
if [[ ! -f "$REGISTRY" ]]; then
  {
    printf '# Worktree provenance registry (ADR-0029, GR-19 tier-1 row 5). Append-only.\n'
    printf '# Columns: path | branch | issue_id | session_id | first_seen_iso | agent_profile\n'
    printf 'path\tbranch\tissue_id\tsession_id\tfirst_seen_iso\tagent_profile\n'
  } > "$REGISTRY"
fi
NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$WT_PATH" "$BRANCH" "unset" "$SESSION_ID" "$NOW_ISO" "lane-workspace-helper" >> "$REGISTRY"; then
  cleanup_on_registry_failure
  fail "could not append to registry $REGISTRY" 2
fi

# --- Emit into the metrics ledger (#686), if it has landed. Never fatal, and
# never lets a metrics failure affect the exit code or stdout contract above.
METRICS_LIB="$HUB_ROOT/scripts/lib/metrics.sh"
if [[ -r "$METRICS_LIB" ]]; then
  # shellcheck source=../scripts/lib/metrics.sh
  source "$METRICS_LIB" 2>/dev/null || true
  if command -v metrics_emit >/dev/null 2>&1; then
    metrics_emit "lane-workspace-provision" "source" "$SOURCE_KIND" "$LANE" 2>/dev/null || true
  fi
fi

log "provisioned: $WT_PATH (branch $BRANCH, base $BASE_REF, source=$SOURCE_KIND, session=$SESSION_ID)"
printf '%s\n' "$WT_PATH"
