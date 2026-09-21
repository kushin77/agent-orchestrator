#!/usr/bin/env bash
# Prints the next free ADR number (issue #1672) — one line, zero-padded to 4
# digits — so two lanes minting an ADR in the same run ask this instead of
# guessing off the highest file they each happen to see.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
max=0
for f in "$root"/docs/decision-records/ADR-[0-9][0-9][0-9][0-9]-*.md; do
  [ -e "$f" ] || continue
  n="$(basename "$f" | sed -E 's/^ADR-([0-9]{4})-.*/\1/')"
  n=$((10#$n))
  [ "$n" -gt "$max" ] && max=$n
done
printf 'ADR-%04d\n' "$((max + 1))"
