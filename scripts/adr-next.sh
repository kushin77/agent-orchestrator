#!/usr/bin/env bash
# Prints the next free ADR number (issue #1672) — one line, zero-padded to 4
# digits — so two lanes minting an ADR in the same run ask this instead of
# guessing off the highest file they each happen to see.
#
# ---knowledge---
# module_id: scripts.adr-next
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: [declared-authority, allocate-only]
# derives_from: null
# owner_sme: architecture-sme
# tier: L0
# interfaces: [prints the next free ADR number, zero-padded to 4]
# invariants: "two lanes minting an ADR in the same run are given the same answer rather than each guessing"
# gotchas: ""
# related: ["#1672"]
# do_not_duplicate: null
# ---knowledge---
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
max=0
for f in "$root"/docs/decision-records/ADR-[0-9][0-9][0-9][0-9]-*.md; do
  [ -e "$f" ] || continue
  n="$(basename "$f" | sed -E 's/^ADR-([0-9]{4})-.*/\1/')"
  n=$((10#$n))
  [ "$n" -gt "$max" ] && max=$n
done
printf 'ADR-%04d\n' "$((max + 1))"
