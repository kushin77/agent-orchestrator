#!/usr/bin/env bash
# discover-checks.sh — self-wiring check-discovery layer for scripts/verify.sh (#698).
#
# Sourced (never executed) by scripts/verify.sh. It exposes one function:
#
#   discover_check_scripts
#
# which prints, one per line, a `name|bash scripts/check-<name>.sh` entry for
# every `scripts/check-*.sh` on disk, in a stable (LC_ALL=C sorted) order. The
# check NAME is the script basename with the `check-` prefix and `.sh` suffix
# stripped (`check-gate-coverage.sh` -> `gate-coverage`).
#
# This ends the #559 sole-writer serialization: a NEW `scripts/check-*.sh` is
# wired the moment it lands, with no hand-edit to scripts/verify.sh. The
# explicit checks array in verify.sh stays the source of truth for the entries
# whose NAME or command differs from the filename convention (renamed checks,
# `*.py` checks, tracker scripts, pytest suites); verify.sh appends only the
# discovered entries whose script is not already referenced there.
#
# DENYLIST — a check can be disabled BY NAME, never silently:
#   scripts/check-denylist.txt (one entry per line; blank lines and `#` comments
#   ignored). An entry matches either the derived check name (`gate-coverage`)
#   or the script basename (`check-gate-coverage.sh`). A denylisted check is
#   skipped AND reported on stderr, so the summary can never hide it.
#
# Source contract: this file only defines functions and sets two variables; it
# performs no work when sourced, so it is safe to source under `set -u`.
#
# ---knowledge---
# module_id: scripts.discover-checks
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: []
# derives_from: null
# owner_sme: platform-sme
# tier: L0
# interfaces: [discover_check_scripts]
# invariants: ""
# gotchas: ""
# related: ["#559", "#698"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
_discover_root="$(find_repo_root)"

# The denylist path. Overridable via CHECK_DENYLIST (an absolute path) for tests,
# matching the SG_* seam pattern used by the other gate scripts.
if [ -n "${CHECK_DENYLIST:-}" ]; then
  DENYLIST_FILE="$CHECK_DENYLIST"
else
  DENYLIST_FILE="$_discover_root/scripts/check-denylist.txt"
fi

discover_check_scripts() {
  local script base name
  local -a denylist=()
  if [ -f "$DENYLIST_FILE" ]; then
    mapfile -t denylist < <(sed -E 's/[[:space:]]+$//' "$DENYLIST_FILE" | grep -vE '^\s*(#|$)' || true)
  fi

  local denylisted_names=""
  while IFS= read -r -d '' script; do
    base="$(basename "$script")"
    name="${base#check-}"
    name="${name%.sh}"
    if [ "${#denylist[@]}" -gt 0 ]; then
      local skip=0 item
      for item in "${denylist[@]}"; do
        if [ "$item" = "$name" ] || [ "$item" = "$base" ]; then
          skip=1
          break
        fi
      done
      if [ "$skip" -eq 1 ]; then
        denylisted_names="${denylisted_names}${denylisted_names:+, }$name"
        continue
      fi
    fi
    printf '%s|bash scripts/%s\n' "$name" "$base"
  done < <(find "$_discover_root/scripts" -maxdepth 1 -type f -name 'check-*.sh' -print0 | LC_ALL=C sort -z)

  if [ -n "$denylisted_names" ]; then
    printf 'discover-checks: denylisted (disabled by name, never silently): %s\n' \
      "$denylisted_names" >&2
  fi
}
