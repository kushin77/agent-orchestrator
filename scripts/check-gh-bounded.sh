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
#   A loop that RETRIES or POLLS gh — an UNBOUNDED `while`/`until` loop, or a `for` loop over a
#   retry-named variable — whose body calls `gh` (or `"$GH"`) and `sleep`s. Remedy:
#   `scripts/lib/gh-bounded.sh` (bounded budget + backoff + dead-letter; a RATE_LIMIT error is
#   NEVER retried). A bounded `for` batch over a plain range (`for n in $(seq …); do gh api …`) is
#   NOT refused: it is one call per item, not a retry (reviewer false-positive, fixed).
#
# PROVEN (self-test, GR-12) — every arm asserted, incl. the arm that runs ON the repository
#   1. `detect` refuses a PLANTED violation by name AND prints its `path:line`;
#   2. `detect` does NOT refuse a clean file, nor the shape written in a COMMENT;
#   3. `scan` — the arm that runs on the repository — is proved to RETURN 1 on a scratch root
#      holding the violation (a mutant that reads nothing, or never fails, is caught here);
#   4. NOT refused: a bounded `for n in $(seq 1 20)` batch; a loop with gh but no sleep;
#      a loop that closes on a line merely CONTAINING the word `done`;
#   5. `scan` on a root with no shell files is CANNOT-ASSESS (rc 2), never a silent "0 loops".
#
# Exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- precondition: a missing interpreter is CANNOT-ASSESS, never three false FAILs ---
command -v python3 >/dev/null 2>&1 || {
  echo "check-gh-bounded: CANNOT-ASSESS — python3 not found (the detector cannot run)" >&2
  exit 2
}

# detect <file> — print "<file>:<line>" for each raw gh retry/poll loop, else nothing; exit 3
# if the file cannot be read (the caller names it rather than reporting it clean).
detect() {
  python3 - "$1" <<'PY'
import re, sys
path = sys.argv[1]
try:
    lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
except OSError:
    sys.exit(3)
loop = re.compile(r'^\s*(for|until|while)\b')
for_var = re.compile(r'^\s*for\s+(\w+)\s+in\b')
done_only = re.compile(r'^\s*done\s*;?\s*$')
gh = re.compile(r'(^|[^\w])(gh|\$\{?GH\}?)\s+[a-z]')          # ANY gh subcommand
sleep = re.compile(r'(^|[^\w])sleep\b')
retry_names = {"try", "attempt", "retry", "retries"}
in_loop = False
start = 0
kind = ""
var = ""
has_gh = has_sleep = False
for i, line in enumerate(lines, 1):
    s = line.strip()
    if s.startswith("#"):
        continue
    if not in_loop:
        m = loop.match(line)
        if m:
            in_loop, start, has_gh, has_sleep = True, i, False, False
            kind = m.group(1)
            mv = for_var.match(line)
            var = mv.group(1) if mv else ""
        continue
    if gh.search(line):
        has_gh = True
    if sleep.search(line):
        has_sleep = True
    if done_only.match(line):
        retry = kind in ("while", "until") or var in retry_names
        if has_gh and has_sleep and retry:
            print(f"{path}:{start}")
        in_loop = False
PY
}

# scan <dir...> — refuse if any shell file under the roots holds the shape. Prints the number
# of files read; CANNOT-ASSESS (rc 2) when that number is zero.
scan() {
  local f out hits=0 n=0 rc=0
  while IFS= read -r f; do
    n=$((n + 1))
    rc=0
    out="$(detect "$f")" || rc=$?
    [ "$rc" -eq 3 ] && continue   # unreadable file: not reported clean, not a loop
    [ -n "$out" ] || continue
    printf 'check-gh-bounded: FAIL — raw gh retry/poll loop is refused by name (use scripts/lib/gh-bounded.sh)\n' >&2
    printf '        %s\n' "$out" >&2
    hits=$((hits + 1))
  done < <(find "$@" -type f -name '*.sh' 2>/dev/null)
  echo "  scanned $n shell file(s) under: $*"
  if [ "$n" -eq 0 ]; then
    echo "check-gh-bounded: CANNOT-ASSESS — no shell files read (the scan would be vacuous)" >&2
    return 2
  fi
  [ "$hits" -eq 0 ] || return 1
  return 0
}

fail=0

# --- self-test on fixtures (the control must be able to fail) ------------------
scratch="${TMPDIR:-/tmp}/check-gh-bounded.$(date +%s%N).$$"
( umask 077; mkdir "$scratch" ) || exit 2
trap 'rm -rf "$scratch"' EXIT

# the forbidden shape is assembled from fragments so this checker does not itself carry it.
L='for'' try in 1 2 3; do'
G="out=\$(gh"' issue create -f title=x)'
S='sleep'' 10'
D='done'
printf '%s\n' "$L" "$G" "$S" "$D" >"$scratch/plant.sh"
printf '%s\n' 'echo hello' >"$scratch/clean.sh"
printf '%s\n' "# $L $G $S $D" >"$scratch/comment.sh"
printf '%s\n' 'for n in $(seq 1 20); do' '  gh api repos/o/r/issues/$n' '  sleep 1' 'done' >"$scratch/batch.sh"
printf '%s\n' 'while true; do' '  echo hi' '  sleep 5' 'done' >"$scratch/nosleepgh.sh"
printf '%s\n' 'for try in 1 2; do' '  echo "attempt $try done"' '  gh api x' '  sleep 1' 'done' >"$scratch/worddone.sh"

if out="$(detect "$scratch/plant.sh")" && [ -n "$out" ] && [ "$out" = "$scratch/plant.sh:1" ]; then
  echo "  OK    detect refuses the planted loop by name, at path:line"
else
  echo "  FAIL  the planted loop was not refused at path:line (got: '$out')" >&2
  fail=1
fi
if out="$(detect "$scratch/clean.sh")" && [ -z "$out" ]; then
  echo "  OK    a clean file is not refused"
else
  echo "  FAIL  a clean file was refused" >&2
  fail=1
fi
if out="$(detect "$scratch/comment.sh")" && [ -z "$out" ]; then
  echo "  OK    the shape in a COMMENT is not refused"
else
  echo "  FAIL  a comment was refused; the rule reads prose" >&2
  fail=1
fi
# the reviewer's false positives, each pinned as an arm
for fx in batch nosleepgh; do
  if out="$(detect "$scratch/$fx.sh")" && [ -z "$out" ]; then
    echo "  OK    $fx is not refused (no retry signal)"
  else
    echo "  FAIL  $fx was refused, but it is not a retry loop (got: '$out')" >&2
    fail=1
  fi
done
# a line merely CONTAINING the word `done` must not close the loop early (reviewer finding)
if out="$(detect "$scratch/worddone.sh")" && [ -n "$out" ]; then
  echo "  OK    a retry loop is still refused when its body contains the word done"
else
  echo "  FAIL  the word done closed the loop early, hiding a raw retry loop" >&2
  fail=1
fi

# scan-arm: the arm that runs on the repository must be PROVED to fail on a planted violation
if scan "$scratch" >/dev/null 2>&1; then
  echo "  FAIL  scan did not fail on a root holding the planted loop (rule-8 vacuity)" >&2
  fail=1
else
  rc=$?
  if [ "$rc" -eq 1 ]; then
    echo "  OK    scan returns 1 on a scratch root holding the planted loop"
  else
    echo "  FAIL  scan returned $rc (expected 1 NOT-OK) on a planted root" >&2
    fail=1
  fi
fi
# scan on a root with NO shell files is CANNOT-ASSESS, never a silent 0
if scan "$scratch/empty" >/dev/null 2>&1; then
  echo "  FAIL  scan returned 0 on an empty root (a silent '0 loops')" >&2
  fail=1
else
  rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "  OK    scan on an empty root is CANNOT-ASSESS (rc 2), not a silent 0"
  else
    echo "  FAIL  scan on an empty root returned $rc (expected 2 CANNOT-ASSESS)" >&2
    fail=1
  fi
fi

# --- helper contract (scripts/lib/gh-bounded.sh) ------------------------------
# The reviewer's finding: the helper shipped with 0 tests. Its contract is pinned here
# so the ONE sanctioned gh wrapper cannot silently drift from the harvested curve.
helper="$root/scripts/lib/gh-bounded.sh"

# 1. the backoff curve is min(base*2**(n-1), 300) — no shift, so it can never wrap negative
curve="$(bash -c '
  source "'"$helper"'"
  sleep(){ printf "%s " "$1"; }
  gh(){ return 1; }
  AO_GH_MAX_ATTEMPTS=6 AO_GH_BASE_DELAY=30 gh_bounded x 2>/dev/null
')"
if [ "$curve" = "30 60 120 240 300 " ]; then
  echo "  OK    helper backoff is min(base*2**(n-1), 300)"
else
  echo "  FAIL  helper backoff curve is '$curve' (want '30 60 120 240 300 ')" >&2
  fail=1
fi

# 2. a RATE_LIMIT error is NEVER retried: it dead-letters (rc 75) naming attempt 1
rlf="$scratch/ratelimit.err"
rl_rc=0
bash -c '
  source "'"$helper"'"
  sleep(){ :; }
  gh(){ printf "RATE_LIMIT exceeded\n" >&2; return 1; }
  AO_GH_MAX_ATTEMPTS=5 gh_bounded x
' >/dev/null 2>"$rlf" || rl_rc=$?
if [ "$rl_rc" -eq 75 ] && grep -q 'attempt 1/5 hit a RATE LIMIT' "$rlf"; then
  echo "  OK    a RATE_LIMIT error dead-letters on the first failure (rc 75, attempt 1/5)"
else
  echo "  FAIL  a RATE_LIMIT error gave rc=$rl_rc (want 75) — it must never be retried; stderr:" >&2
  sed 's/^/        /' "$rlf" >&2 2>/dev/null || true
  fail=1
fi

# 3. a misconfigured budget/base is REFUSED by name (rc 64), never a silent no-op
mc="$(bash -c '
  source "'"$helper"'"
  sleep(){ :; }
  gh(){ return 0; }
  AO_GH_MAX_ATTEMPTS=0 gh_bounded x >/dev/null 2>&1; printf "%s" "$?"
  AO_GH_MAX_ATTEMPTS=x gh_bounded x >/dev/null 2>&1; printf " %s" "$?"
  AO_GH_BASE_DELAY=-1 gh_bounded x >/dev/null 2>&1; printf " %s" "$?"
')"
if [ "$mc" = "64 64 64" ]; then
  echo "  OK    a misconfigured budget/base is REFUSED (rc 64)"
else
  echo "  FAIL  misconfig gave '$mc' (want '64 64 64')" >&2
  fail=1
fi

# 4. the helper is the SANCTIONED loop: it must NOT be refused by the detector itself.
#    (It resolves gh through `$gh_bin`, which the detector does not read as a raw gh call;
#     this arm pins that exemption so a rewrite to a bare `gh` cannot silently self-refuse.)
if out="$(detect "$helper")" && [ -z "$out" ]; then
  echo "  OK    the helper itself is not refused (it is the sanctioned bounded loop)"
else
  echo "  FAIL  the detector refuses the helper: '$out'" >&2
  fail=1
fi

# --- the repository against the rule ------------------------------------------
if ! scan "$root/scripts" "$root/fleet" "$root/governance"; then
  rc=$?
  [ "$rc" -eq 1 ] && fail=1
  [ "$rc" -eq 2 ] && exit 2
fi

if [ "$fail" -eq 0 ]; then
  echo "check-gh-bounded: OK — no raw gh retry/poll loop; self-test passed"
  exit 0
fi
exit 1
