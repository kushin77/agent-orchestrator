#!/usr/bin/env bash
# json-lint gate for `make verify` (GR-12): python3 -m json.tool on every
# *.json outside vendor/ and .git. Exits nonzero on any invalid file.
#
# ---knowledge---
# module_id: scripts.check-json
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [no-false-green]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK]
# invariants: ""
# gotchas: ""
# related: []
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 1

failed=0
count=0

while IFS= read -r f; do
  count=$((count + 1))
  if python3 -m json.tool "$f" >/dev/null 2>&1; then
    printf '  OK    %s\n' "$f"
  else
    printf '  FAIL  %s\n' "$f" >&2
    failed=$((failed + 1))
  fi
done < <(git ls-files '*.json' | grep -v '^vendor/' || true)

if [ "$failed" -ne 0 ]; then
  printf 'json: %s of %s file(s) FAILED\n' "$failed" "$count" >&2
  exit 1
fi
printf 'json: OK (%s file(s))\n' "$count"
