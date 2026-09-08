#!/usr/bin/env bash
# check-drift.sh — gate-contract drift detector (issue #29).
#
# The gate's coverage claim is only as truthful as the manifest that declares
# it. This check detects when the tree has drifted from what the gate believes
# it is testing (leaderboard "manifest drift after PR merges" shape):
#
#   * the gate is not inside a git worktree        -> CANNOT-ASSESS (exit 2)
#   * scripts/pytest-suites.txt is missing/empty   -> NOT-OK     (exit 1)
#   * a declared suite directory vanished           -> NOT-OK     (exit 1)
#     (coverage silently lost — a suite disappearing is the false-green risk)
#   * tracked tests dirs not declared               -> WARN       (exit 0)
#     (a new suite nobody registered — loud, non-fatal, so other lanes merging
#      a suite cannot silently break the top-level gate)
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0/1/2.
#
# Usage: bash scripts/check-drift.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

manifest="scripts/pytest-suites.txt"

if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "check-drift: CANNOT-ASSESS — not inside a git worktree (cannot attest a commit)" >&2
  exit 2
fi

if [ ! -f "$manifest" ]; then
  echo "check-drift: FAIL — manifest $manifest is missing (gate has no declared suites)" >&2
  exit 1
fi

mapfile -t suites < <(sed -E 's/[[:space:]]+$//' "$manifest" | grep -vE '^\s*(#|$)' || true)
if [ "${#suites[@]}" -eq 0 ]; then
  echo "check-drift: FAIL — manifest $manifest declares no suites (empty coverage claim)" >&2
  exit 1
fi

fail=0
for mod in "${suites[@]}"; do
  if [ ! -d "$mod/tests" ]; then
    echo "check-drift: FAIL — declared suite '$mod' has no tests/ dir (coverage silently lost)" >&2
    fail=$((fail + 1))
  fi
done

# Loud warnings for suites that exist but were never declared.
while IFS= read -r cf; do
  tdir="$(dirname "$cf")"
  mod="$(dirname "$tdir")"
  mod="${mod#./}"
  case "$mod" in
    guardrails/*|gateway/*|registry/*|identity/*|engine/*|telemetry/*)
      if ! grep -qx "$mod" "$manifest"; then
        echo "check-drift: WARN — '$mod' has a committed tests/ dir but is not declared in $manifest (register it or drop it)" >&2
      fi ;;
  esac
done < <(git ls-files '*/tests/conftest.py' 2>/dev/null | LC_ALL=C sort)

if [ "$fail" -gt 0 ]; then
  echo "check-drift: FAIL ($fail declared suite(s) missing)" >&2
  exit 1
fi
echo "check-drift: OK — ${#suites[@]} declared suite(s), manifest coherent with the tree"
exit 0

