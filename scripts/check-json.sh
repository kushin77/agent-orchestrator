#!/usr/bin/env bash
# json-lint gate for `make verify` (GR-12): python3 -m json.tool on every
# *.json outside vendor/ and .git. Exits nonzero on any invalid file.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
done < <(find . -type f -name '*.json' \
  -not -path './.git/*' \
  -not -path './vendor/*' \
  -not -path './.research/*' \
  | LC_ALL=C sort)

if [ "$failed" -ne 0 ]; then
  printf 'json: %s of %s file(s) FAILED\n' "$failed" "$count" >&2
  exit 1
fi
printf 'json: OK (%s file(s))\n' "$count"
