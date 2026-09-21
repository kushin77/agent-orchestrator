#!/usr/bin/env bash
# Prints the next free ADR number (issue #1672) — one line, zero-padded to 4
# digits — so two lanes minting an ADR in the same run ask this instead of
# guessing off the highest file they each happen to see.
#
# ---knowledge---
# module_id: scripts.adr-next
# system: scripts
# app: scripts
# solution_class: template
# patterns: [avoid-double-guess]
# derives_from: null
# owner_sme: docs-sme
# tier: L1
# interfaces: [stdout: next free ADR-NNNN]
# invariants: "output is always zero-padded to 4 digits and one line"
# gotchas: ""
# related: ["#1672"]
# do_not_duplicate: null
# ---knowledge---
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
