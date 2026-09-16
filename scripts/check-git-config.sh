#!/usr/bin/env bash
# check-git-config.sh — git-config boilerplate gate (issue #624, EPIC #616).
#
# WHY THIS EXISTS
#   Measured 2026-09-14 (issue #608 gap analysis,
#   docs/GIT-TEMPLATES-GAP-ANALYSIS.md): `.gitattributes` did not exist, and
#   `git config commit.template` was unset (measured rc 1) — the commit
#   template (`.gitmessage`, issue #5) is advisory until it is, and nothing
#   noticed the drift. This gate makes both facts mechanical instead of
#   conventional.
#
# WHAT IS CHECKED (three checks, each able to fail on its own)
#   1. ATTRIBUTES — the tracked `.gitattributes` exists and is sane: it
#      declares a normalization rule (`text=auto`) and marks at least one
#      generated/state path (mirroring scripts/check-gitignore.sh's
#      declared runtime-state roots) as `linguist-generated`.
#   2. TEMPLATE — `git config commit.template` is set and resolves (relative
#      to the repo root, symlinks included) to this repo's own `.gitmessage`.
#      Unset, or pointed at any other file, is NOT-OK — a template that does
#      not resolve to the repo's file is not the governed one.
#   3. IDENTITY — the lane worktree's OWN config sets both `user.name` and
#      `user.email` — global/system inheritance does NOT count. When
#      `extensions.worktreeConfig` is enabled (this repo's linked worktrees),
#      "own config" is `git config --worktree` (`.git/worktrees/<id>/config.worktree`
#      — `--local` reads the SHARED main-repo config, not the lane's own file,
#      under that extension). Otherwise it is `git config --local`. A worktree
#      that inherits identity only from `~/.gitconfig` fails this check: that
#      identity did not travel with the lane and is not what a clone of this
#      worktree would carry.
#
# BOOTSTRAP MODE
#   `bash scripts/check-git-config.sh --bootstrap` sets `commit.template` to
#   this repo's `.gitmessage` when it is unset or wrong, and reports the
#   exact `git config` invocation it ran on stdout — never silently. It does
#   NOT invent `user.name`/`user.email`: those are a person's identity, not a
#   default this script can supply, so check 3 still fails after bootstrap if
#   they are absent and the verdict says so. After making its change (if any)
#   bootstrap mode re-runs the three checks and exits with their verdict.
#
# NEGATIVE CONTROL
#   AO_GIT_CONFIG_ROOT=<dir> assesses <dir> instead of the tree this script
#   lives in, so the gate can be provoked against a scratch repo (unset
#   template, missing .gitattributes, no identity) without touching this
#   checkout. The override is announced on stderr and echoed in the verdict.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. No network access.
#   CANNOT-ASSESS (2) is reserved for an input that cannot be read at all —
#   no git, a root that is not a directory, or a root that is not a git work
#   tree. A missing `.gitattributes`, an unset template, or absent identity
#   are definitively false properties, so they are NOT-OK (1), never 2.
#
# NOT YET WIRED INTO `make verify`: `scripts/verify.sh` iterates an explicit
# check list (see scripts/check-gitignore.sh's own note on this); wiring a
# new `scripts/check-*.sh` into it is a separate lane. Until then this gate
# is run directly, per this issue's own Verify section.
#
# Usage: bash scripts/check-git-config.sh [--bootstrap]
set -u

bootstrap=0
for arg in "$@"; do
  case "$arg" in
  --bootstrap) bootstrap=1 ;;
  *)
    echo "check-git-config: CANNOT-ASSESS — unknown argument: $arg" >&2
    exit 2
    ;;
  esac
done

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$script_root"
overridden=0
if [ -n "${AO_GIT_CONFIG_ROOT:-}" ]; then
  root="$AO_GIT_CONFIG_ROOT"
  overridden=1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "check-git-config: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
if [ ! -d "$root" ]; then
  echo "check-git-config: CANNOT-ASSESS — root is not a directory: $root" >&2
  exit 2
fi
if [ "$(git -C "$root" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
  echo "check-git-config: CANNOT-ASSESS — root is not a git work tree: $root" >&2
  exit 2
fi

if [ "$overridden" = "1" ]; then
  printf 'check-git-config: NOTE — root overridden by AO_GIT_CONFIG_ROOT: %s\n' "$root" >&2
fi

if [ "$bootstrap" = "1" ]; then
  echo "== bootstrap =="
  if [ -f "$root/.gitmessage" ]; then
    current_template="$(git -C "$root" config --get commit.template 2>/dev/null || true)"
    want="$root/.gitmessage"
    resolved=""
    if [ -n "$current_template" ]; then
      case "$current_template" in
      /*) candidate="$current_template" ;;
      *) candidate="$root/$current_template" ;;
      esac
      resolved="$(realpath -e "$candidate" 2>/dev/null || true)"
    fi
    want_resolved="$(realpath -e "$want" 2>/dev/null || echo "$want")"
    bootstrap_scope="--local"
    if [ "$(git -C "$root" config --get extensions.worktreeConfig 2>/dev/null || true)" = "true" ]; then
      bootstrap_scope="--worktree"
    fi
    if [ -z "$current_template" ] || [ "$resolved" != "$want_resolved" ]; then
      git -C "$root" config "$bootstrap_scope" commit.template .gitmessage
      printf '  SET   commit.template -> .gitmessage (git -C %s config %s commit.template .gitmessage)\n' \
        "$root" "$bootstrap_scope"
    else
      printf '  OK    commit.template already resolves to .gitmessage — no change\n'
    fi
  else
    printf '  SKIP  no .gitmessage at repo root — cannot bootstrap a template that does not exist\n' >&2
  fi
  echo
fi

fail=0

# --- 1. attributes -----------------------------------------------------
echo "== 1. .gitattributes exists and is sane =="
attrs="$root/.gitattributes"
if [ ! -f "$attrs" ]; then
  printf '  FAIL  .gitattributes (absent)\n' >&2
  fail=$((fail + 1))
else
  if grep -qE '^\*[[:space:]]+text=auto([[:space:]]|$)' "$attrs"; then
    printf '  OK    normalization rule present (* text=auto)\n'
  else
    printf '  FAIL  .gitattributes has no "* text=auto" normalization rule\n' >&2
    fail=$((fail + 1))
  fi
  if grep -qE 'linguist-generated=true' "$attrs"; then
    printf '  OK    at least one generated/state path is marked linguist-generated\n'
  else
    printf '  FAIL  .gitattributes marks no path linguist-generated\n' >&2
    fail=$((fail + 1))
  fi
fi

# --- 2. commit.template -------------------------------------------------
echo
echo "== 2. commit.template resolves to this repo's .gitmessage =="
gitmessage="$root/.gitmessage"
if [ ! -f "$gitmessage" ]; then
  printf '  FAIL  %s/.gitmessage (absent — nothing for commit.template to resolve to)\n' "$root" >&2
  fail=$((fail + 1))
else
  template="$(git -C "$root" config --get commit.template 2>/dev/null || true)"
  if [ -z "$template" ]; then
    printf '  FAIL  commit.template is unset\n' >&2
    fail=$((fail + 1))
  else
    case "$template" in
    /*) candidate="$template" ;;
    *) candidate="$root/$template" ;;
    esac
    resolved="$(realpath -e "$candidate" 2>/dev/null || true)"
    want_resolved="$(realpath -e "$gitmessage" 2>/dev/null || echo "$gitmessage")"
    if [ -n "$resolved" ] && [ "$resolved" = "$want_resolved" ]; then
      printf '  OK    commit.template=%s resolves to %s\n' "$template" "$want_resolved"
    else
      printf '  FAIL  commit.template=%s does not resolve to %s\n' "$template" "$want_resolved" >&2
      fail=$((fail + 1))
    fi
  fi
fi

# --- 3. identity ---------------------------------------------------------
echo
echo "== 3. this worktree's own config sets user.name and user.email =="
own_scope="--local"
if [ "$(git -C "$root" config --get extensions.worktreeConfig 2>/dev/null || true)" = "true" ]; then
  own_scope="--worktree"
fi
user_name="$(git -C "$root" config "$own_scope" --get user.name 2>/dev/null || true)"
user_email="$(git -C "$root" config "$own_scope" --get user.email 2>/dev/null || true)"
printf '  NOTE  own-config scope: %s\n' "$own_scope"
if [ -n "$user_name" ]; then
  printf '  OK    user.name=%s (%s)\n' "$user_name" "$own_scope"
else
  printf '  FAIL  user.name is unset in this worktree'"'"'s own config (%s) — global/system inheritance does not count\n' "$own_scope" >&2
  fail=$((fail + 1))
fi
if [ -n "$user_email" ]; then
  printf '  OK    user.email=%s (%s)\n' "$user_email" "$own_scope"
else
  printf '  FAIL  user.email is unset in this worktree'"'"'s own config (%s) — global/system inheritance does not count\n' "$own_scope" >&2
  fail=$((fail + 1))
fi

# --- verdict ---------------------------------------------------------------
echo
if [ "$fail" -ne 0 ]; then
  printf 'check-git-config: FAIL — %s problem(s); git-config boilerplate is not governed\n' "$fail" >&2
  exit 1
fi
if [ "$overridden" = "1" ]; then
  printf 'check-git-config: OK — .gitattributes sane, commit.template governed, identity set (root overridden: %s)\n' "$root"
else
  printf 'check-git-config: OK — .gitattributes sane, commit.template governed, identity set\n'
fi
exit 0
