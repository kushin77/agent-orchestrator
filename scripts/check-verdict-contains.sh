#!/usr/bin/env bash
#
# check-verdict-contains -- a verdict test must not be able to kill its own producer.
#
# WHAT IT CHECKS
#   * `printf '%s' "$report" | grep -qF -- "$needle"` is NOT a containment test.
#     `grep -q` exits on its FIRST match; the producer is then killed by SIGPIPE
#     while still writing; and `set -o pipefail` promotes that 141 to the status of
#     the whole pipeline. The condition is the size of the report relative to the
#     64 KiB pipe buffer, so the defect is LATENT -- it appears only once the report
#     grows past that. Negated (`if ! ... | grep -q`) it is a false red: the check
#     reports ABSENT for text that is PRESENT. Positive it is worse, because the
#     branch that proves a control works is the branch that gets skipped, and the
#     check fails OPEN.
#     #843 removed it from check-session-isolation.sh; #852 from
#     check-chronological-dispatch.sh, check-cto-overlay.sh,
#     check-fleet-durability-rules.sh and check-github-lifecycle.sh.
#   * the repository still carries legacy occurrences (measured 2026-09-15: 112
#     sites in 51 files, all under scripts/). They are recorded in
#     scripts/verdict-contains-legacy.tsv. Recording a legacy site is an explicit
#     reviewed act and is never automatic: a NEW occurrence is refused BY NAME
#     first. The record is SHRINK-ONLY -- --record will lower it and will refuse to
#     raise it.
#
# HOW IT PROVES ITSELF (a control that cannot fail is a formality)
#   1. it reproduces the defect: a ~1 MB report that CONTAINS the needle is reported
#      absent by the piped idiom, while grep reading the same bytes exits 0;
#   2. the native test gets that input right;
#   3. the native test still reports absent for text that IS absent (vacuity);
#   4. the scanner finds a planted occurrence, and does NOT count one that lives in
#      a comment (vacuity both ways);
#   5. the comparison -- the very function the repo run uses -- refuses a planted
#      occurrence the record omits, and accepts the same tree once the record names
#      it. A refusal is a refusal about the RECORD, not about the tree.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-verdict-contains.sh [--root DIR] [--baseline FILE]
#        bash scripts/check-verdict-contains.sh --record
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
baseline="$root/scripts/verdict-contains-legacy.tsv"
record=0

while [ $# -gt 0 ]; do
  case "$1" in
    --root) root="${2:?--root needs a directory}"; shift 2 ;;
    --baseline) baseline="${2:?--baseline needs a file}"; shift 2 ;;
    --record) record=1; shift ;;
    *) printf 'check-verdict-contains: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "check-verdict-contains: CANNOT-ASSESS -- git is not on PATH" >&2
  exit 2
fi

# This script's own absolute path, so a control can exercise the --record path the
# same way a lane would, instead of re-implementing it.
self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"

# The idiom under test: a pipe into a quiet `grep`. `[|]` rather than `\|` so the
# pattern means the same thing in every awk; `[^|]*` between the command and the
# flag catches `grep -F -q` as well as `grep -qF`, and `--quiet` as well as `-q`.
# The counter is deliberately CONSERVATIVE: it can over-count (it also matches the
# idiom inside quoted fixture data and inside a printed message), and it must never
# under-count -- over-counting is recorded and reviewed, whereas a miss would let a
# real occurrence through, which is the whole failure mode this check exists for.
idiom_re='[|][[:space:]]*grep[[:space:]][^|]*(-[[:alnum:]]*q|--quiet)'

contains() { # contains <haystack> <needle> -- bash-native; cannot kill its producer
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

count_sites() { # count_sites <file> -> offending tests in it (argv is a file, not a pipe)
  awk -v pat="$idiom_re" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 ~ pat            { n++ }
    END { print n + 0 }
  ' "$1" 2>/dev/null
}

# Both the scan and the record are "<count>\t<path>", so one comparison reads both.
scan_tree() { # scan_tree <dir> -> "<count>\t<path>" for every .sh that has any
  local dir="$1" f n files
  if [ -e "$dir/.git" ]; then
    # --others --exclude-standard as well as --cached: `git ls-files` alone lists
    # only TRACKED files, so a brand-new check carrying the idiom would be invisible
    # until it was committed -- the occurrence would be caught by the next lane
    # instead of by its author. Ignored paths (.research clones, build output) stay
    # out, which is what --exclude-standard is for.
    files="$(git -C "$dir" ls-files --cached --others --exclude-standard '*.sh')"
  else
    files="$(cd "$dir" && find . -name '*.sh' -type f | sed 's|^\./||' | sort)"
  fi
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -f "$dir/$f" ] || continue
    n="$(count_sites "$dir/$f")"
    [ "$n" -gt 0 ] && printf '%s\t%s\n' "$n" "$f"
  done <<< "$files"
  return 0
}

read_record() { # read_record <file> -> "<count>\t<path>", comments dropped
  [ -f "$1" ] || return 0
  awk -F'\t' '!/^[[:space:]]*#/ && NF >= 2 { print $1 "\t" $2 }' "$1"
}

recorded_for() { # recorded_for <path> -> the count the record allows (0 if unlisted)
  read_record "$2" |
    awk -F'\t' -v want="$1" '$2 == want { print $1; found = 1 } END { if (!found) print 0 }'
}

actual_for() { # actual_for <path> <scan-output>
  printf '%s\n' "$2" |
    awk -F'\t' -v want="$1" '$2 == want { print $1; found = 1 } END { if (!found) print 0 }'
}

# ONE implementation, used by the repo run and by the control that proves it:
#   compare_tree <scan-output> <record-file> -> "NEW\t<path>\t<actual>\t<recorded>"
#                                               "SHRUNK\t<path>\t<recorded>\t<actual>"
compare_tree() {
  local scanned="$1" base="$2" f actual recorded
  {
    printf '%s\n' "$scanned" | cut -f2
    read_record "$base" | cut -f2
  } | sort -u | sed '/^[[:space:]]*$/d' |
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      actual="$(actual_for "$f" "$scanned")"
      recorded="$(recorded_for "$f" "$base")"
      if [ "$actual" -gt "$recorded" ]; then
        printf 'NEW\t%s\t%s\t%s\n' "$f" "$actual" "$recorded"
      elif [ "$actual" -lt "$recorded" ]; then
        printf 'SHRUNK\t%s\t%s\t%s\n' "$f" "$recorded" "$actual"
      fi
    done
}

# ---------------------------------------------------------------------------
# --record: shrink-only by construction. It writes the measurement, and refuses
# outright if recording it would RAISE any number.
# ---------------------------------------------------------------------------
if [ "$record" -eq 1 ]; then
  scanned="$(scan_tree "$root")"
  grown=""
  total=0
  files=0
  while IFS=$'\t' read -r n f; do
    [ -n "${f:-}" ] || continue
    was="$(recorded_for "$f" "$baseline")"
    if [ "$n" -gt "$was" ]; then
      grown="$grown"$'\n'"  $f: $was -> $n"
    fi
    total=$((total + n))
    files=$((files + 1))
  done <<< "$scanned"

  # Shrink-only applies to an EXISTING record. Writing the first one IS the review
  # act; without this the bootstrap could never happen, because an absent record
  # makes every existing occurrence look like growth.
  if [ -n "$grown" ] && [ -f "$baseline" ]; then
    printf 'check-verdict-contains: REFUSED -- the record is shrink-only, and this would GROW it:%s\n' "$grown" >&2
    printf 'Use a bash-native containment test (see the header) instead, then re-run.\n' >&2
    exit 1
  fi
  [ -f "$baseline" ] || printf 'check-verdict-contains: writing the FIRST record for this tree\n'

  {
    printf '%s\n' \
      '# Shrink-only legacy record for the piped-`grep -q` idiom (#852).' \
      '#' \
      '# Format: <count><TAB><path>, highest count first. A comment cannot run, so' \
      '# comment lines here are not counted as occurrences.' \
      '#' \
      '# WHY these are recorded rather than fixed: `grep -q` exits on its first match,' \
      '# the producer is then killed by SIGPIPE, and `set -o pipefail` promotes that' \
      '# 141 to the status of the whole pipeline -- so a report larger than the 64 KiB' \
      '# pipe buffer reports ABSENT for text that is PRESENT. Negated that is a false' \
      '# red; positive the control is skipped and the check fails OPEN.' \
      '#' \
      '# Recording is an EXPLICIT REVIEWED ACT and is never automatic: a new occurrence' \
      '# is refused by name first. Fixing one is always welcome -- delete or lower its' \
      '# line and re-run with --record. Lowering the record is the only change that' \
      '# does not need a review of the code it describes.' \
      '#' \
      '# scripts/check-verdict-contains.sh records 3, and none of them is a verdict' \
      '# test: one is the provocation in section 1, which must exist for that control' \
      '# to be able to fail, and two are the planted fixture in section 3 -- one of' \
      '# which is the line that is SUPPOSED to read as a comment (it begins with a' \
      '# quote, so the counter sees it, which is the conservative direction).' \
      "# measured: $(date -u +%Y-%m-%dT%H:%M:%SZ) -- $total site(s) in $files file(s)"
    printf '%s' "$scanned" | sort -k1,1nr -k2,2
  } > "$baseline"
  printf 'check-verdict-contains: recorded %s legacy site(s) in %s file(s) -> %s\n' \
    "$total" "$files" "$baseline"
  exit 0
fi

# ---------------------------------------------------------------------------
fail=0
controls=0
ok() { printf '  OK    %s\n' "$1"; controls=$((controls + 1)); }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

printf 'verdict-contains: a verdict test must not kill its own producer (#852)\n'

tmp="$(mktemp -d)" || { echo "check-verdict-contains: CANNOT-ASSESS -- mktemp failed" >&2; exit 2; }
trap 'rm -rf "$tmp"' EXIT

printf '\n== 1. the mechanism ==\n'
needle="commit-missing-ticket-trailer"
big="$(printf "${needle}\n%.0s" $(seq 1 35000))"   # > 1 MB, far past any pipe buffer
printf '%s' "$big" > "$tmp/report.txt"
bytes="$(wc -c < "$tmp/report.txt" | tr -d ' ')"

grep -qF -- "$needle" "$tmp/report.txt"; file_rc=$?        # no pipe to kill
printf '%s' "$big" | grep -qF -- "$needle"; idiom_rc=$?     # the idiom under test
contains "$big" "$needle"; native_rc=$?

printf '  report %s bytes; needle written on the first of 35000 lines\n' "$bytes"
if [ "$file_rc" -eq 0 ]; then
  ok "grep reading the same bytes from a file: rc=0 (the needle IS present)"
else
  bad "the control premise is broken: grep reading the file returned $file_rc"
fi
if [ "$idiom_rc" -ne 0 ]; then
  ok "the piped idiom reports rc=$idiom_rc for text that is PRESENT -- reproduced"
else
  bad "the provocation did NOT reproduce at $bytes bytes (rc=0) -- this control proves nothing"
fi

printf '\n== 2. the native test ==\n'
if [ "$native_rc" -eq 0 ]; then
  ok "contains() finds the needle the idiom lost: rc=0"
else
  bad "contains() did not find text that is present (rc=$native_rc)"
fi
if ! contains "$big" "absent-from-this-report-zzz"; then
  ok "contains() still reports ABSENT for text that is absent (vacuity)"
else
  bad "contains() matched text that is not in the report -- it could never fail"
fi

printf '\n== 3. the scanner ==\n'
mkdir -p "$tmp/fixt/scripts"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if ! printf "%s" "$out" | grep -qF -- "$marker"; then echo missing; fi' \
  > "$tmp/fixt/scripts/one.sh"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  '# printf "%s" "$out" | grep -qF -- "$marker"   <- a comment cannot run' \
  'echo clean' \
  > "$tmp/fixt/scripts/comment-only.sh"
planted="$(count_sites "$tmp/fixt/scripts/one.sh")"
comment_only="$(count_sites "$tmp/fixt/scripts/comment-only.sh")"
if [ "$planted" -eq 1 ]; then
  ok "one planted occurrence is found (count=1)"
else
  bad "the scanner found $planted occurrence(s) in a file with exactly 1"
fi
if [ "$comment_only" -eq 0 ]; then
  ok "the same text inside a comment is NOT counted (count=0)"
else
  bad "the scanner counted comment text ($comment_only) -- it would not survive its own header"
fi

printf '\n== 4. the comparison refuses a NEW occurrence ==\n'
fixt_scan="$(scan_tree "$tmp/fixt")"
printf '0\tscripts/one.sh\n0\tscripts/comment-only.sh\n' > "$tmp/fixt-omitting.tsv"
printf '1\tscripts/one.sh\n0\tscripts/comment-only.sh\n' > "$tmp/fixt-naming.tsv"
omitting="$(compare_tree "$fixt_scan" "$tmp/fixt-omitting.tsv")"
naming="$(compare_tree "$fixt_scan" "$tmp/fixt-naming.tsv")"
if contains "$omitting" 'NEW	scripts/one.sh'; then
  ok "an occurrence the record omits is refused by name"
  printf '        %s\n' "$(printf '%s' "$omitting" | tr '\t' ' ')"
else
  bad "a planted NEW occurrence was not refused (compare said: ${omitting:-nothing})"
fi
if ! contains "$naming" 'NEW'; then
  ok "the same tree passes once the record names it -- the refusal is about the RECORD"
else
  bad "a recorded occurrence was refused anyway (compare said: $naming)"
fi

# The shrink-only refusal itself. --record is the only way the record changes, so a
# record that could be raised on demand would be a formality: prove it refuses,
# against the fixture whose record omits the planted occurrence.
bash "$self" --root "$tmp/fixt" --baseline "$tmp/fixt-omitting.tsv" --record \
  > "$tmp/fixt-record.log" 2>&1
fixt_record_rc=$?
fixt_record_log="$(cat "$tmp/fixt-record.log")"
if [ "$fixt_record_rc" -ne 0 ] && contains "$fixt_record_log" 'REFUSED'; then
  ok "--record REFUSES to raise the record (rc=$fixt_record_rc)"
  printf '        %s\n' "$(printf '%s' "$fixt_record_log" | tail -2 | tr '\n' ' ')"
else
  bad "--record accepted a growth (rc=$fixt_record_rc) -- the record is not shrink-only"
  printf '%s\n' "$fixt_record_log" | tail -3 | sed 's/^/        /' >&2
fi

printf '\n== 5. the repository against its record ==\n'
if [ ! -f "$baseline" ]; then
  bad "no record at ${baseline#"$root"/} -- run: bash scripts/check-verdict-contains.sh --record"
  printf '\ncheck-verdict-contains: FAIL (%s finding(s))\n' "$fail" >&2
  exit 1
fi

scanned="$(scan_tree "$root")"
compared="$(compare_tree "$scanned" "$baseline")"
new="$(printf '%s\n' "$compared" | awk -F'\t' '$1 == "NEW"')"
shrunk="$(printf '%s\n' "$compared" | awk -F'\t' '$1 == "SHRUNK"')"

if [ -n "$new" ]; then
  while IFS=$'\t' read -r _ f actual recorded; do
    [ -n "${f:-}" ] || continue
    bad "a NEW occurrence: $f has $actual, the record allows $recorded"
  done <<< "$new"
  printf '        Use a bash-native containment test (see the header) instead.\n' >&2
else
  ok "no file exceeds its recorded count"
fi

if [ -n "$shrunk" ]; then
  printf '  NOTE  the record can be lowered -- re-run with --record:\n'
  while IFS=$'\t' read -r _ f was now; do
    [ -n "${f:-}" ] || continue
    printf '        %s: %s -> %s\n' "$f" "$was" "$now"
  done <<< "$shrunk"
fi

total=0
files=0
while IFS=$'\t' read -r n f; do
  [ -n "${f:-}" ] || continue
  total=$((total + n))
  files=$((files + 1))
done <<< "$scanned"
printf '  legacy still recorded: %s site(s) in %s file(s)\n' "$total" "$files"

printf '\n'
if [ "$fail" -ne 0 ]; then
  printf 'check-verdict-contains: FAIL (%s finding(s); %s control(s) passed)\n' "$fail" "$controls" >&2
  exit 1
fi
printf 'check-verdict-contains: OK -- the idiom is reproduced as a false verdict, the native test is proven both ways, and the record is shrink-only (%s control(s); %s legacy site(s) in %s file(s) remain recorded)\n' \
  "$controls" "$total" "$files"
exit 0
