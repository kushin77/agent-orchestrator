#!/usr/bin/env bash
# ============================================================================
# scripts/lib/lock.sh — DR-043 shared advisory-lock helpers.
#
# Source this file; do not execute it directly.
#
#   source "$ROOT/scripts/lib/lock.sh"
#
# Provides:
#   lock_acquire <name> [wait-seconds] [tag]
#       -> acquire the lock, waiting up to N seconds (0 = immediate, default 0).
#          Returns 0 on success, 1 when held, 2 on usage.
#   lock_release <name> [tag]
#       -> release the lock (only if we own it). Returns 0.
#   lock_status <name> [tag]
#       -> report held/free. Returns 1 when held, 0 when free.
#
# The lock mechanism uses mkdir atomicity; a lock is a directory under the
# lock root (see below) containing a "holder" file with "PID EPOCH-TIMESTAMP".
#
# Configuration (environment variables):
#   CMR_LOCK_ROOT       — directory where locks live (required).
#   CMR_LOCK_STALE_AFTER — seconds before a lock is considered stale and
#                          can be stolen (default: 3600).
#   CMR_LOCK_TAG        — prefix for log messages; e.g. "upgrade", "queue",
#                          "gdc-feed" (default: "lock"). Override per-call
#                          by passing a third argument to lock_*().
#
# Surviving source: registry/upgrade/lock.sh (pre-DR-043) — the fullest
# implementation, supporting configurable wait + stale windows. This library
# unifies that with fleet/queue.sh and sync/gdc-feed.sh, which reimplemented
# the mechanism independently.
#
# Example:
#   export CMR_LOCK_ROOT="/tmp/locks"
#   export CMR_LOCK_TAG="myapp"
#   if lock_acquire "resource" 10; then
#     trap 'lock_release "resource"' EXIT
#     # do work
#   else
#     echo "failed to acquire lock" >&2
#     exit 1
#   fi
# ============================================================================

# Guard against double-sourcing.
if [ -n "${CMR_LOCK_LIB_SOURCED:-}" ]; then
  return 0 2>/dev/null || exit 0
fi
CMR_LOCK_LIB_SOURCED=1

# _lock_resolve <name> — normalize the lock name and return its full path.
_lock_resolve() {
  local name="${1:?lock name required}"
  # Sanitize: replace non-alphanumeric chars with underscore.
  name="${name//[^A-Za-z0-9._-]/_}"
  local root="${CMR_LOCK_ROOT:?CMR_LOCK_ROOT not set}"
  printf '%s/%s' "$root" "$name"
}

# _lock_age <dir> — return seconds since the lock was taken (from holder file
# epoch, or directory mtime as fallback). Prints 0 if it cannot be determined.
_lock_age() {
  local dir="$1"
  local ts
  ts="$(awk 'NF>=2 {print $2; exit}' "$dir/holder" 2>/dev/null)"
  if [[ -n "$ts" && "$ts" =~ ^[0-9]+$ ]]; then
    printf '%s' "$(( $(date +%s) - ts ))"
    return
  fi
  local mtime
  if ! mtime="$(stat -c %Y "$dir" 2>/dev/null)"; then
    printf '0'
    return
  fi
  printf '%s' "$(( $(date +%s) - mtime ))"
}

# lock_acquire <name> [wait-seconds] [tag] — take the lock, waiting up to N
# seconds when held. Returns 0 on success, 1 when held, 2 on usage error.
lock_acquire() {
  local name="${1:?lock name required}"
  local wait_secs="${2:-0}"
  local tag="${3:-${CMR_LOCK_TAG:-lock}}"
  local stale_after="${CMR_LOCK_STALE_AFTER:-3600}"

  if ! [[ "$wait_secs" =~ ^[0-9]+$ ]]; then
    echo "$tag: lock_acquire: wait-seconds must be a non-negative integer" >&2
    return 2
  fi

  local dir; dir="$(_lock_resolve "$name")"
  mkdir -p "$(dirname "$dir")"

  local waited=0
  while true; do
    if mkdir "$dir" 2>/dev/null; then
      printf '%s %s\n' "$$" "$(date +%s)" > "$dir/holder"
      echo "$tag: acquired lock $dir (pid $$)" >&2
      return 0
    fi
    local age; age="$(_lock_age "$dir")"
    if [[ -n "$age" && "$age" -gt "$stale_after" ]]; then
      echo "$tag: WARN stale lock $dir (age ${age}s > ${stale_after}s) — stealing" >&2
      rm -rf "$dir"
      continue
    fi
    if [[ "$waited" -lt "$wait_secs" ]]; then
      sleep 1
      waited=$((waited + 1))
      continue
    fi
    echo "$tag: locked by another run ($dir — holder $(cat "$dir/holder" 2>/dev/null || echo unknown))" >&2
    return 1
  done
}

# lock_release <name> [tag] — drop the lock, but only if we own it (holder PID == $$).
# Returns 0.
lock_release() {
  local name="${1:?lock name required}"
  local tag="${2:-${CMR_LOCK_TAG:-lock}}"
  local dir; dir="$(_lock_resolve "$name")"
  if [[ ! -d "$dir" ]]; then
    return 0
  fi
  local owner; owner="$(awk 'NF>=1 {print $1; exit}' "$dir/holder" 2>/dev/null)"
  if [[ "$owner" == "$$" ]]; then
    rm -rf "$dir"
    echo "$tag: released lock $dir" >&2
  else
    echo "$tag: lock $dir held by pid ${owner:-unknown} (not us — left in place)" >&2
  fi
  return 0
}

# lock_status <name> [tag] — report held/free. Returns 1 when held, 0 when free.
lock_status() {
  local name="${1:?lock name required}"
  local tag="${2:-${CMR_LOCK_TAG:-lock}}"
  local dir; dir="$(_lock_resolve "$name")"
  if [[ -d "$dir" ]]; then
    echo "$tag: held: $dir (holder: $(cat "$dir/holder" 2>/dev/null || echo unknown))" >&2
    return 1
  fi
  echo "$tag: free: $dir" >&2
  return 0
}
