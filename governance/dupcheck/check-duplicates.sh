#!/usr/bin/env bash
# check-duplicates.sh - automated canonical-copy / duplicate detection.
#
# agent-orchestrator consumes the fleet-standard "trio" (MODEL-PROFILES /
# SME-PROFILES / SOLUTION-CLASSES) and the agent-identity standards from their
# canonical homes (kushin77/CMR and kushin77/shared-governance) and must never
# fork them in-repo (ADR-0010). This helper makes that rule mechanical and can
# re-verify the documented byte-identical dprs = git-rca-workspace duplicate.
#
# Subcommands:
#   scan                 PASS if this repo carries NO forked copy of a protected
#                        canonical doc (i.e. none outside vendor/ and
#                        .research/); FAIL (exit 1) when a same-named copy
#                        appears. vendor/ is whitelisted because the pinned
#                        vendor/CMR submodule is the repo's own read-only
#                        mirror of the CMR trio.
#   compare <A> <B>      PASS (exit 0) if A and B are byte-identical (files, or
#                        dirs compared recursively, ignoring .git); FAIL
#                        (exit 1) otherwise. Demonstrates / re-verifies the
#                        dprs = git-rca-workspace finding.
#   demo-cases           Run `scan`, then `compare` the dprs vs
#                        git-rca-workspace clones. RESEARCH_BASE defaults to
#                        $root/.research and can be pointed at another checkout
#                        that holds the .research clones (e.g. the main
#                        agent-orchestrator checkout).
#
# Exit codes: 0 = PASS, 1 = FAIL (a fork or a byte difference was found),
# 2 = usage / path error. Every branch is an honest gate (no-false-green,
# AO-GR-4): scan genuinely fails when a fork exists, compare genuinely fails
# when the two paths differ.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root" || exit 1

# Protected canonical doc names (ADR-0010 canonical homes): a same-named file
# outside vendor/ and .research/ is a fork and fails `scan`.
protected_names=(
  MODEL-PROFILES.md
  SME-PROFILES.md
  SOLUTION-CLASSES.md
  agent-identity.md
  agent-identity-jwt.schema.json
  agent-action.schema.json
  agent-oidc-config.schema.json
  agent-task.schema.json
)

scan() {
  local forks=0
  local name
  local f
  for name in "${protected_names[@]}"; do
    while IFS= read -r f; do
      printf '  FORK  %s (protected canonical doc name)\n' "$f" >&2
      forks=$((forks + 1))
    done < <(find . -type f -name "$name" \
      -not -path './.git/*' \
      -not -path './vendor/*' \
      -not -path './.research/*' 2>/dev/null)
  done
  if [ "$forks" -ne 0 ]; then
    printf 'dupcheck scan: FAIL - %s forked copy(ies) of a protected canonical doc\n' "$forks" >&2
    return 1
  fi
  printf 'dupcheck scan: PASS - no forked copies of protected canonical docs (%s names checked)\n' "${#protected_names[@]}"
  return 0
}

compare() {
  local a="${1:-}"
  local b="${2:-}"
  if [ -z "$a" ] || [ -z "$b" ]; then
    printf 'usage: check-duplicates.sh compare <pathA> <pathB>\n' >&2
    return 2
  fi
  if [ ! -e "$a" ] || [ ! -e "$b" ]; then
    printf 'dupcheck compare: missing path (%s / %s)\n' "$a" "$b" >&2
    return 2
  fi
  if [ -d "$a" ] && [ -d "$b" ]; then
    if diff -r --exclude=.git "$a" "$b" >/dev/null 2>&1; then
      printf 'dupcheck compare: IDENTICAL (%s = %s)\n' "$a" "$b"
      return 0
    fi
  elif [ -f "$a" ] && [ -f "$b" ]; then
    if cmp -s "$a" "$b"; then
      printf 'dupcheck compare: IDENTICAL (%s = %s)\n' "$a" "$b"
      return 0
    fi
  else
    printf 'dupcheck compare: type mismatch (%s vs %s)\n' "$a" "$b" >&2
    return 2
  fi
  printf 'dupcheck compare: DIFFERENT (%s vs %s)\n' "$a" "$b" >&2
  return 1
}

demo_cases() {
  local rc=0
  scan || rc=1
  local base="${RESEARCH_BASE:-$root/.research}"
  local dprs="$base/fleet/dprs"
  local grw="$base/fleet/git-rca-workspace"
  if [ -d "$dprs" ] && [ -d "$grw" ]; then
    compare "$dprs" "$grw" || rc=1
  else
    printf 'dupcheck demo: dprs / git-rca-workspace clones not found under %s; skipping compare\n' "$base" >&2
  fi
  return "$rc"
}

case "${1:-}" in
  scan) scan ;;
  compare) compare "${2:-}" "${3:-}" ;;
  demo-cases) demo_cases ;;
  *)
    printf 'usage: check-duplicates.sh <scan|compare|demo-cases>\n' >&2
    exit 2
    ;;
esac
