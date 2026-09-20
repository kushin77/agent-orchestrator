#!/usr/bin/env bash
# check-gh-bounded.sh — refuse a RAW, unbounded gh retry/poll loop (issue #1550, AO-GR-21).
#
# THE DEFECT THIS EXISTS FOR
#   An ad-hoc shell loop `for try in 1..5; do gh issue create …; sleep 10; done` retried a
#   rate-limited GraphQL mutation with no backoff and no dead-letter, exhausting the box-wide
#   GraphQL budget and blocking every lane's merges. `scripts/check-runaway-guard.sh` (AO-GR-21)
#   bounds DISPATCHED directives; it does NOT see an ad-hoc shell loop. This does.
#
# WHAT IT REFUSES (by name)
#   A `for`/`until`/`while` loop whose body calls `gh` (or `"$GH"`) for issue/pr/api/repo AND
#   sleeps — a retry or poll loop a rate limit turns into an amplifier. Remedy:
#   `scripts/lib/gh-bounded.sh` (bounded budget + backoff + dead-letter; a RATE_LIMIT error
#   is NEVER retried).
#
# PROVEN (self-test, GR-12)
#   1. a PLANTED violation is refused BY NAME;
#   2. a clean file is NOT refused (the rule is not indiscriminate);
#   3. the same shape written in a COMMENT is NOT refused (a comment cannot loop).
#
# Exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# detect <file> — print "<file>:<line>" for each raw gh retry/poll loop, else nothing.
detect() {
  python3 - "$1" <<'PY'
import re, sys
path = sys.argv[1]
try:
    lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
except Exception:
    sys.exit(0)
gh = re.compile(r'(^|[^\w])(gh|\$\{?GH\}?)\s+(issue|pr|api|repo|search)\b')
loop = re.compile(r'^\s*(for|until|while)\b')
done = re.compile(r'(^|[^\w])done\b')
sleep = re.compile(r'(^|[^\w])sleep\b')
in_loop = False
start = 0
has_gh = has_sleep = False
for i, line in enumerate(lines, 1):
    s = line.strip()
    if s.startswith("#"):
        continue
    if not in_loop and loop.match(line):
        in_loop, start, has_gh, has_sleep = True, i, False, False
    if not in_loop:
        continue
    if gh.search(line):
        has_gh = True
    if sleep.search(line):
        has_sleep = True
    if done.search(line):
        if has_gh and has_sleep:
            print(f"{path}:{start}")
        in_loop = False
PY
}

# scan <dir...> — refuse if any shell file under the roots holds the shape.
scan() {
  local f hits=0
  while IFS= read -r f; do
    if out="$(detect "$f")" && [ -n "$out" ]; then
      printf 'check-gh-bounded: FAIL — raw gh retry/poll loop is refused by name (use scripts/lib/gh-bounded.sh)\n' >&2
      printf '        %s\n' "$out" >&2
      hits=$((hits + 1))
    fi
  done < <(find "$@" -type f -name '*.sh' 2>/dev/null)
  [ "$hits" -eq 0 ] || return 1
  return 0
}

fail=0

# --- self-test on fixtures (the control must be able to fail) ------------------
tmp="${TMPDIR:-/tmp}/check-gh-bounded-$$"
rm -rf "$tmp"
mkdir -p "$tmp" || exit 2
trap 'rm -rf "$tmp"' EXIT
printf '%s\n' 'for try in 1 2 3; do out=$(gh issue create -f title=x); sleep 10; done' >"$tmp/plant.sh"
printf '%s\n' 'echo hello' >"$tmp/clean.sh"
printf '%s\n' '# for try in 1 2 3; do gh issue create; sleep 10; done' >"$tmp/comment.sh"

if out="$(detect "$tmp/plant.sh")" && [ -n "$out" ]; then
  echo "  OK    planted raw gh retry loop refused by name"
else
  echo "  FAIL  the planted raw gh retry loop was NOT refused; the detector matches nothing" >&2
  fail=1
fi
if out="$(detect "$tmp/clean.sh")" && [ -z "$out" ]; then
  echo "  OK    a clean file is not refused"
else
  echo "  FAIL  a clean file was refused" >&2
  fail=1
fi
if out="$(detect "$tmp/comment.sh")" && [ -z "$out" ]; then
  echo "  OK    the shape in a COMMENT is not refused"
else
  echo "  FAIL  a comment was refused; the rule reads prose" >&2
  fail=1
fi

# --- the repository against the rule ------------------------------------------
if ! scan "$root/scripts" "$root/fleet" "$root/governance"; then
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "check-gh-bounded: OK — 0 raw gh retry/poll loops; self-test 3/3"
  exit 0
fi
exit 1
