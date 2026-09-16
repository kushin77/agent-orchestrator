#!/usr/bin/env bash
# ============================================================================
# scripts/checkout-bootstrap.sh — the checkout's freshness remedy, INSTALLED
# OUTSIDE the checkout (issue #780, AO-GR-25)
#
# bootstrap-version: 2
# ============================================================================
#
# THE DEFECT THIS EXISTS FOR (measured 2026-09-15 ~01:4x, issue #780)
#
# #773 gave the watchdog the right remedy for a stale checkout: when a rung's
# commit IS the local HEAD while `origin/master` is ahead, the drift is the
# CHECKOUT and the remedy is `git fetch` + `git merge --ff-only` — never a
# respawn, because a respawn re-executes the same stale tree. The remedy never
# ran, because cron ran the checkout's OWN copy of the code:
#
#     */2 * * * * cd <checkout> && /usr/bin/python3 fleet/watchdog.py run
#
# so a stale checkout ran a stale watchdog, and the fast-forward that would have
# fixed it lived in the code that stale copy could not see. Measured: the sister
# ran `592b132` for ~5.5 hours while `origin/master` was `3a44f27`, missing every
# fix merged that day (#733 runner preflight, #739 drift, #768, #770, #773). The
# remedy was applied by a HUMAN — exactly the step #780 removes.
#
# THE SHAPE OF THE FIX: the remedy is INSTALLED OUTSIDE THE CHECKOUT
#
# `checkout-bootstrap.sh --install` copies THIS file to a pinned path that is not
# inside any checkout — `$HOME/.ao-fleet/checkout-bootstrap.sh`, overridable with
# `AO_FLEET_BOOTSTRAP` or `--path` — and it installs the bytes carried by the
# REMOTE (`origin/master:scripts/checkout-bootstrap.sh`), never the bytes in the
# tree it was invoked from. A stale tree therefore cannot install a stale remedy.
#
#     bash scripts/checkout-bootstrap.sh --install          # once per host
#     bash ~/.ao-fleet/checkout-bootstrap.sh <checkout>      # the remedy, from outside
#     bash ~/.ao-fleet/checkout-bootstrap.sh <checkout> python3 fleet/watchdog.py run
#
# A pinned copy that has itself fallen behind re-executes the copy carried by the
# fetched remote before it does any work (`self-refresh` below), so the executing
# remedy is the remote's, not the installed one — the one property a file inside
# the checkout cannot have. The version stamp above is reported on every remedy
# line (`[bootstrap vN]`), so which revision actually ran is measured, not assumed.
#
# A fast-forward is NOT always possible, and the measured reason is uncommitted
# work (issue #780, comment 2026-09-15 20:52Z): the checkout's own runtime state
# (`.board/snapshot.json`, `governance/lessons/ledger.jsonl`) is TRACKED, so the
# fleet's normal operation re-dirties the tree and `--ff-only` refuses forever.
# The remedy therefore has an explicit, BOUNDED policy for a dirty tree: try the
# fast-forward; if it is refused AND the tree is dirty, `git stash push -u` with a
# RECORDED reason (the stash ref is named in the output, so nobody's work is
# parked somewhere no one looks); retry once. Nothing is ever discarded, and a
# stash that itself fails is a refusal by name, never a `--force`.
#
# CONTRACT
#
#     checkout-bootstrap.sh <checkout-root> [--ref <remote/branch>] [--quiet] [-- command...]
#     checkout-bootstrap.sh --install [<checkout-root>] [--path <pinned-path>] [--ref ...]
#     checkout-bootstrap.sh --version
#
# With a command: report, move the checkout if it can, `cd` into it and `exec` the
# command — so the fleet runs on the code that was just fetched and cron's process
# tree is unchanged (cron -> the pass, exactly as before). The command runs
# REGARDLESS of the freshness verdict, because a watch that stops watching is a
# worse failure than the drift it could not repair; the verdict is on stdout and in
# the log either way.
#
# WHAT IT REPORTS — one line naming BOTH commits, on every branch:
#
#     [bootstrap] checkout-current 3a44f27 == origin/master 3a44f27 [bootstrap v2]
#     [bootstrap] checkout-ahead: a1b2c3d is ahead of origin/master 3a44f27 by its own commits — leaving it alone
#     [bootstrap] checkout-behind: fast-forwarded 592b132 -> 3a44f27 (origin/master 3a44f27) [bootstrap v2]
#     [bootstrap] checkout-behind: fast-forwarded 592b132 -> 3a44f27 (...). uncommitted work preserved: refs/stash 1a2b3c4 (recover: git -C <root> stash pop 1a2b3c4)
#     [bootstrap] checkout-diverged: 592b132 is not an ancestor of origin/master 3a44f27 — refusing to move [bootstrap v2]
#     [bootstrap] checkout-behind: fast-forward REFUSED (592b132 -> 3a44f27): <git's reason> [bootstrap v2]
#     [bootstrap] checkout-freshness: CANNOT-ASSESS — git fetch origin failed in <root> (not a drift verdict)
#
# A DIVERGED checkout is refused BY NAME rather than guessed at: `--ff-only`
# cannot resolve it and a bootstrap that merged or reset one would destroy
# whatever the divergence was. "I could not look" (no HEAD, no remote ref, no
# network) is CANNOT-ASSESS and NEVER `current` — the #739 fail-closed rule
# (AO-GR-25), applied to the checkout itself.
#
# Every line is also appended to `<checkout>/.fleet/checkout-bootstrap.log`
# (runtime state, gitignored) so a checkout-behind condition is a visible finding
# with both commits named rather than a silent condition seen only in a terminal.
#
# Exit codes (docs/QA-GATE.md tri-state)
#   0  the checkout is current — already at the remote, or brought forward
#   1  NOT-OK          — it could not be brought forward (refused / diverged)
#   2  CANNOT-ASSESS   — the checkout or its remote ref could not be read at all
#      (with a command, the exit code is the command's: the verdict is the line)
#
# Self-contained on purpose: bash + git + coreutils, no repo imports, no network
# beyond `git fetch`. It may be run from anywhere, including from a checkout that
# is far behind — every git call is `git -C <root>`.
set -uo pipefail

SELF_REL="scripts/checkout-bootstrap.sh"
VERSION="2"
DEFAULT_REF="origin/master"
DEFAULT_PINNED="${HOME:-/tmp}/.ao-fleet/checkout-bootstrap.sh"

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

note() { printf '[bootstrap] %s\n' "$*"; }
# The first line of a git error, for a one-line finding (never reflowed).
first_line() { local text="$1"; printf '%s' "${text%%$'\n'*}"; }

mode="remedy"
root=""
ref="$DEFAULT_REF"
quiet=0
path=""
cmd=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install) mode="install" ;;
    --version) mode="version" ;;
    --path)
      [ "$#" -ge 2 ] || { note "CANNOT-ASSESS — --path needs a value"; exit 2; }
      path="$2"
      shift
      ;;
    --ref)
      [ "$#" -ge 2 ] || { note "CANNOT-ASSESS — --ref needs a value"; exit 2; }
      ref="$2"
      shift
      ;;
    --quiet) quiet=1 ;;
    --help|-h)
      printf 'usage: checkout-bootstrap.sh <checkout-root> [--ref <remote/branch>] [--quiet] [-- command...]\n'
      printf '       checkout-bootstrap.sh --install [<checkout-root>] [--path <pinned-path>]\n'
      exit 0
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do cmd+=("$1"); shift; done
      continue
      ;;
    -*)
      note "CANNOT-ASSESS — unknown option $1 (see --help)"
      exit 2
      ;;
    *)
      if [ -z "$root" ]; then root="$1"; else cmd+=("$1"); fi
      ;;
  esac
  shift
done

if [ "$mode" = "version" ]; then
  printf 'checkout-bootstrap %s\n' "$VERSION"
  exit 0
fi

TMPD="/tmp/ao-checkout-bootstrap.$PPID.$(date +%s%N)"
mkdir -p "$TMPD" || { note "CANNOT-ASSESS — cannot create a scratch directory"; exit 2; }

if [ -n "$ref" ]; then remote_name="${ref%%/*}"; else remote_name="origin"; fi

# --- finish: hand the shell to the command, or exit with the verdict ----------
#
# The command is exec'd (not backgrounded, not retried): cron's process tree is
# unchanged, and the fleet's liveness never depends on the remedy having worked.
finish() {
  local verdict="$1"
  if [ "${#cmd[@]}" -gt 0 ]; then
    cd "$root" || { note "CANNOT-ASSESS — cannot cd into $root"; exit 2; }
    exec "${cmd[@]}"
  fi
  exit "$verdict"
}

# --- record: the durable, visible finding ------------------------------------
#
# `.fleet/` is runtime state (gitignored by the repo's own discipline): the
# record must never become a tracked-file change that re-dirties the tree the
# remedy is trying to move. Best-effort — a read-only checkout must not turn a
# successful fast-forward into a failure.
record() {
  local line="$1"
  mkdir -p "$root/.fleet" 2>/dev/null || return 0
  printf '%s [bootstrap v%s] %s\n' "$(date -u +%FT%TZ)" "$VERSION" "$line" \
    >> "$root/.fleet/checkout-bootstrap.log" 2>/dev/null || true
}

emit() {
  local verdict="$1"
  local text="$2"
  if [ "$quiet" != "1" ]; then printf '[bootstrap] %s [bootstrap v%s]\n' "$text" "$VERSION"; fi
  record "$text"
  finish "$verdict"
}

# ============================================================================
# --install: pin the remedy OUTSIDE the checkout, from the REMOTE's bytes
# ============================================================================
if [ "$mode" = "install" ]; then
  [ -n "$root" ] || root="."
  [ -n "$path" ] || path="${AO_FLEET_BOOTSTRAP:-$DEFAULT_PINNED}"
  if ! git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
    note "CANNOT-ASSESS — $root is not a git checkout (no .git)"
    exit 2
  fi
  # A blocked network is not fatal here: the remote ref already fetched answers.
  git -C "$root" fetch --quiet --prune "$remote_name" 2>/dev/null
  source_of="the local copy ($0) — $ref carries no $SELF_REL"
  remote_blob="$(git -C "$root" rev-parse "$ref:$SELF_REL" 2>/dev/null)"
  if [ -n "$remote_blob" ]; then
    if git -C "$root" show "$ref:$SELF_REL" > "$TMPD/pinned.sh" 2>/dev/null; then
      source_of="the remote $ref (blob ${remote_blob:0:12})"
    else
      note "CANNOT-ASSESS — cannot read $ref:$SELF_REL from $root"
      exit 2
    fi
  else
    cp -a "$0" "$TMPD/pinned.sh" || { note "CANNOT-ASSESS — cannot stage this copy"; exit 2; }
  fi
  mkdir -p "$(dirname "$path")" 2>/dev/null || { note "CANNOT-ASSESS — cannot create $(dirname "$path")"; exit 2; }
  install -m 0755 "$TMPD/pinned.sh" "$path" 2>/dev/null || { note "CANNOT-ASSESS — cannot write $path"; exit 2; }
  installed_blob="$(git hash-object "$path" 2>/dev/null)"
  note "installed $path (blob ${installed_blob:0:12}, from $source_of) [bootstrap v$VERSION]"
  exit 0
fi

# ============================================================================
# the remedy
# ============================================================================
[ -n "$root" ] || root="."
# A normal checkout has a `.git` DIRECTORY; a linked worktree has a `.git` FILE
# pointing at the main repository. Both are checkouts this must be able to move.
if [ ! -d "$root" ] || [ ! -e "$root/.git" ]; then
  emit 2 "checkout-freshness: CANNOT-ASSESS — $root is not a git checkout (no .git)"
fi

before="$(git -C "$root" rev-parse --short HEAD 2>/dev/null)"
if [ -z "$before" ]; then
  emit 2 "checkout-freshness: CANNOT-ASSESS — HEAD is unreadable in $root (never a healthy verdict)"
fi

if ! git -C "$root" fetch --quiet --prune "$remote_name" 2>/dev/null; then
  emit 2 "checkout-freshness: CANNOT-ASSESS — git fetch $remote_name failed in $root (the baseline is unreadable — not a drift verdict)"
fi

remote_sha="$(git -C "$root" rev-parse --short "$ref" 2>/dev/null)"
if [ -z "$remote_sha" ]; then
  emit 2 "checkout-freshness: CANNOT-ASSESS — $ref is unreadable in $root between $before and $ref (not a drift verdict)"
fi

if [ "$before" = "$remote_sha" ]; then
  emit 0 "checkout-current $before == $ref $remote_sha"
fi

behind=0
if git -C "$root" merge-base --is-ancestor HEAD "$ref" 2>/dev/null; then behind=1; fi

# --- self-refresh: the remedy that RUNS is the remote's, not this copy's ------
#
# This is the half a file inside the checkout cannot have. When the checkout is
# behind, the remote's copy is the newer one by construction, so if this copy's
# blob differs from `$ref:$SELF_REL`, stage the remote's bytes and re-exec them.
# `AO_BOOTSTRAP_STAGED` makes the staged copy do the work instead of refreshing
# again; if the remote carries no such path (a tree from before this landed),
# this copy simply carries on — it is the best available, and it says so.
if [ "$behind" = "1" ] && [ -z "${AO_BOOTSTRAP_STAGED:-}" ]; then
  remote_blob="$(git -C "$root" rev-parse "$ref:$SELF_REL" 2>/dev/null)"
  self_blob="$(git hash-object "$0" 2>/dev/null)"
  if [ -n "$remote_blob" ] && [ "$remote_blob" != "$self_blob" ] \
     && git -C "$root" show "$ref:$SELF_REL" > "$TMPD/fresh.sh" 2>/dev/null; then
    note "self-refresh: this copy is blob ${self_blob:0:12}; $ref carries ${remote_blob:0:12} — running the fetched copy"
    reexec=("$root" --ref "$ref")
    [ "$quiet" = "1" ] && reexec+=(--quiet)
    if [ "${#cmd[@]}" -gt 0 ]; then reexec+=(-- "${cmd[@]}"); fi
    export AO_BOOTSTRAP_STAGED=1
    exec bash "$TMPD/fresh.sh" "${reexec[@]}"
  fi
fi

if [ "$behind" != "1" ]; then
  # A checkout that carries the remote's tip *plus* its own commits is a BRANCH,
  # not a stale checkout — a lane worktree is exactly this. It is healthy, and
  # moving it is the one thing that would destroy the lane's work, so it is left
  # alone and named as what it is (never reported as a repair that failed).
  if git -C "$root" merge-base --is-ancestor "$ref" HEAD 2>/dev/null; then
    emit 0 "checkout-ahead: $before is ahead of $ref $remote_sha by its own commits — leaving it alone (current == origin/master is false for a branch, and that is not staleness)"
  fi
  emit 1 "checkout-diverged: $before is not an ancestor of $ref $remote_sha — refusing to move (a fast-forward cannot resolve it)"
fi

# --- the fast-forward, and the bounded dirty-tree policy it needs ------------
merge_out="$(git -C "$root" merge --ff-only "$ref" 2>&1)"
merge_rc=$?
if [ "$merge_rc" -eq 0 ]; then
  after="$(git -C "$root" rev-parse --short HEAD 2>/dev/null)"
  emit 0 "checkout-behind: fast-forwarded $before -> $after ($ref $remote_sha)"
fi

# Measured (issue #780 comment): the remedy that only ever tries `--ff-only` can
# never win, because the checkout's own runtime state is tracked and re-dirties
# the tree between every attempt. The policy is explicit and bounded: stash the
# uncommitted work under a NAMED reason, retry ONCE, and name the stash so the
# work is recoverable. Never `--force`, never `reset --hard`, never discard.
dirty="$(git -C "$root" status --porcelain -uall 2>/dev/null)"
stash_detail=""
if [ -n "$dirty" ]; then
  reason="ao-checkout-bootstrap v$VERSION: $before -> $remote_sha ($ref) was blocked by uncommitted work; stashed so the fast-forward could run"
  stash_out="$(git -C "$root" stash push -u -m "$reason" 2>&1)"
  stash_rc=$?
  if [ "$stash_rc" -ne 0 ]; then
    emit 1 "checkout-behind: fast-forward REFUSED ($before -> $remote_sha): $(first_line "$merge_out") — and the working tree is dirty; stashing it failed too: $(first_line "$stash_out")"
  fi
  stash_sha="$(git -C "$root" rev-parse --short refs/stash 2>/dev/null)"
  stash_detail=" uncommitted work preserved: refs/stash ${stash_sha} (recover with: git -C $root stash pop ${stash_sha})"
  merge_out="$(git -C "$root" merge --ff-only "$ref" 2>&1)"
  merge_rc=$?
fi

if [ "$merge_rc" -ne 0 ]; then
  emit 1 "checkout-behind: fast-forward REFUSED ($before -> $remote_sha $ref): $(first_line "$merge_out")"
fi

after="$(git -C "$root" rev-parse --short HEAD 2>/dev/null)"
emit 0 "checkout-behind: fast-forwarded $before -> $after ($ref $remote_sha).$stash_detail"
