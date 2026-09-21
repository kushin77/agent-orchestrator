#!/bin/bash
# scripts/lib/common.sh — shared helpers for scripts/*.sh (issue #1496).
#
# ---knowledge---
# module_id: scripts.lib.common
# system: scripts
# app: lib
# solution_class: class
# patterns: [shared-helper-library]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [find_repo_root, contains, log, die]
# invariants: "find_repo_root must be called from a script's top level, not from inside another function (BASH_SOURCE[1] is the caller)"
# gotchas: "contains() is bash-native on purpose — grep -qF can SIGPIPE a still-writing producer"
# related: ["#1496"]
# do_not_duplicate: null
# ---knowledge---
#
# Source it, then call the functions:
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"
#   root="$(find_repo_root)"
#
# Scope: this is step 1 of the #1496 cleanup — the 3 helpers with the most
# duplicate implementations across scripts/*.sh, migrated into the 10
# scripts with the most duplication. The remaining scripts are tracked in
# the #1496 follow-up issue, not done here.

# find_repo_root — repo root as scripts/.. relative to the *calling* script's
# own location (BASH_SOURCE[1] is the caller, since this call is one frame
# deeper than the caller's own former `cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd`).
# Call this directly from a script's top level (not from inside another
# function), matching how the 188 duplicate call sites used it.
find_repo_root() {
  # Bash-builtin dirname (no external `dirname`): operator.sh's honesty check
  # (#763) strips PATH down to nothing to prove the operator path fails on a
  # missing `tmux` by name rather than dying on a missing coreutil first.
  local src="${BASH_SOURCE[1]}"
  local dir="${src%/*}"
  [ "$dir" = "$src" ] && dir="."
  cd "$dir/.." && pwd
}

# contains <haystack> <needle> — bash-native substring test; used instead of
# `grep -qF` so a quiet grep can't SIGPIPE a still-writing producer.
contains() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# log <message> — stderr, prefixed with the calling script's basename.
log() {
  echo "[$(basename "${BASH_SOURCE[1]:-$0}")] $*" >&2
}

# die <message> — log then exit 1.
die() {
  log "$*"
  exit 1
}
