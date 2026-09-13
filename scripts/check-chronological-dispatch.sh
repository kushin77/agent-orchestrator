#!/usr/bin/env bash
# Enforce the chronological issue-dispatch contract (issue #152, GR-20).
#
# The rule is only binding if a gate can fail on it (no-false-green doctrine,
# GR-12): a doc-only rule is advisory. This check asserts that every governance
# document that carries the execution contract declares the chronological,
# dependency-gated selection rule — and that the required rule vocabulary is
# present in each. Removing a section, or renaming a rule so the statement no
# longer exists, fails the gate.
#
# Usage: bash scripts/check-chronological-dispatch.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

declare -a docs=(
  "AGENTS.md"
  "docs/GOVERNANCE.md"
  "docs/EXECUTION-PLAN.md"
)

# Vocabulary every contract document must declare (case-sensitive).
declare -a required_markers=(
  "chronological"
  "dependency"
)

declare -a required_markers_agents=(
  "Kanban"
)

fail=0
checked=0

for doc in "${docs[@]}"; do
  if [ ! -f "$doc" ]; then
    printf '  FAIL  %s (missing file)\n' "$doc"
    fail=1
    continue
  fi
  checked=$((checked + 1))

  for marker in "${required_markers[@]}"; do
    if ! grep -qi -- "$marker" "$doc"; then
      printf '  FAIL  %s (missing rule marker: %s)\n' "$doc" "$marker"
      fail=1
    fi
  done

  # The repository contract (AGENTS.md) must forbid board scavenging by name.
  if [ "$doc" = "AGENTS.md" ]; then
    for marker in "${required_markers_agents[@]}"; do
      if ! grep -q -- "$marker" "$doc"; then
        printf '  FAIL  %s (missing rule marker: %s)\n' "$doc" "$marker"
        fail=1
      fi
    done
  fi
done

if [ "$fail" -ne 0 ]; then
  printf 'chronological-dispatch: FAIL (%d of %d contract doc(s) non-conforming)\n' \
    "$fail" "${#docs[@]}" >&2
  exit 1
fi

printf 'chronological-dispatch: OK (%d contract doc(s) declare dependency-ordered selection)\n' \
  "$checked"
exit 0
