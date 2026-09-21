#!/usr/bin/env bash
# check-shell-patterns.sh — the shell-pattern doctrine, enforced (issue #621, EPIC #616).
#
# WHAT THIS IS
#   The mechanical half of `docs/SHELL-PATTERNS.md`. Every pattern the doctrine
#   grades ENFORCED is refused here **by name** — the pattern id, its label, the
#   file and line that carries it, and the code the rule matched. A pattern the
#   doctrine can only DECLARE (because enforcing it today would be red on
#   `master`) is not enforced here at all: it is named in the doctrine's gap
#   table with the measured sites and the issue that retires them. Declared and
#   enforced are never confused (GR-12, AO-GR-4).
#
# WHY IT IS SHAPED LIKE THIS
#   A gate that cannot fail is a formality, so this gate proves itself on EVERY
#   run — before it looks at the repository at all:
#
#     1. a planted violation of each pattern is refused, BY NAME;
#     2. a file with no violation produces NO finding (vacuity — a rule that
#        matches everything must not pass this half);
#     3. the same shape written in a COMMENT is NOT refused: a comment cannot
#        run, so it cannot be an occurrence — and without this half the rule
#        would red the prose that documents it (issue #804 measured that trap
#        one level up, in the marker rule);
#     4. for EVERY pattern, a MUTANT OF THIS SCRIPT — the same file with that
#        one detector replaced by a rule that matches nothing — must STOP
#        refusing its own plant, while still refusing another pattern's. So each
#        refusal is shown to come from the rule under test, and not from the
#        file existing, and no detector can be inert while the gate stays green.
#
#   The provocation drives the SAME scan function the repository run uses
#   (`scan_paths`), so what is proven is the code path that runs — never a copy.
#
# THE CHECKER IS SCANNED LIKE EVERY OTHER SHELL FILE
#   Every shipped shape is assembled from fragments here (`fx_cleanup_sig="tr""ap"`),
#   so this checker's own source cannot be its own finding, and the self-test
#   asserts exactly that. A checker that spells the shape it forbids either
#   hides it or reds itself; fragmenting is the fix (CMR SHELL-PATTERNS §7).
#
# EXIT CONTRACT (the repo's honesty tri-state, guardrails/honesty)
#   0  OK              no occurrence in the tree and the self-test passed
#   1  NOT-OK          an occurrence refused by name, or the self-test failed
#   2  CANNOT-ASSESS   git/awk missing, no shell file to scan, no scratch dir,
#                      or a bad invocation — never a pass
#
# Usage:
#   bash scripts/check-shell-patterns.sh                 the gate (self-test + tree)
#   bash scripts/check-shell-patterns.sh --self-test     the provocation alone
#   bash scripts/check-shell-patterns.sh --files F...    scan exactly these files
#   bash scripts/check-shell-patterns.sh --list          the pattern table
#
# WRITING IS NOT THIS LANE'S JOB
#   Being refused here is how you learn the shape; the fix (the good shape, side
#   by side with the bad one) is in `docs/SHELL-PATTERNS.md`, and the repair is
#   local to the file that was refused.
#
# ---knowledge---
# module_id: scripts.check-shell-patterns
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, named-refusal]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#616", "#621", "#804"]
# do_not_duplicate: null
# ---knowledge---
set -u

self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2

# One global scratch variable and one EXIT trap, the shape this doctrine asks
# for: armed once, fired once, `|| true`-safe so a cleanup failure cannot mask
# the gate's own exit code.
TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

# --- the shapes, assembled from fragments -----------------------------------
# A literal spelled whole here would be found by the detector that looks for
# it, so every shape is split, and the PLANTS in the self-test are built from
# these same fragments — the provocation and the detector cannot drift apart.
fx_cleanup_sig="tr""ap"
fx_return_sig="RET""URN"
fx_sep_assign="IF""S="
fx_by_name_sig="pk""ill"
fx_by_name_alt="kill""all"
fx_capture_word="lo""cal"
fx_fetch_tool="cu""rl"
fx_shell_interp="ba""sh"
fx_tab="$(printf '\t')"
fx_gh_tool="g""h"
fx_quote="[\"']"
fx_spacequote="[ \"']"
fx_wordchar="[^[:alnum:]_]"
fx_ws="[[:space:]]"
fx_sq="'"
fx_mktemp="mkt""emp"
fx_dq='"'
fx_cd_w="c""d"

# --- patterns, ONE line per pattern (a mutation rewrites exactly one) -------
# p_re[k]  the detector (POSIX ERE; no backslash, so it survives `awk -v`)
# p_lbl[k] the label a refusal is reported BY
# p_why[k] the failure the shape causes, in the refusal output
p_re=()
p_lbl=()
p_why=()
load_patterns() {
  p_re[1]="^[[:space:]]*${fx_cleanup_sig}[[:space:]].*[^A-Za-z]${fx_return_sig}([^A-Za-z]|\$)"
  p_lbl[1]="trap-return"
  p_why[1]="a cleanup trap armed on the RETURN pseudo-signal fires again on every later function return, and under set -u the captured scratch path is out of scope by then: the script dies after the real work"

  p_re[2]="^${fx_ws}*${fx_sep_assign}[^;|&]*\$"
  p_lbl[2]="ifs-collapse"
  p_why[2]="a standalone field-separator assignment collapses adjacent tabs and empty fields, so a TSV row is mis-split and a field silently shifts"

  p_re[3]="(^|${fx_wordchar})${fx_sep_assign}${fx_quote}?,${fx_spacequote}?${fx_ws}*read"
  p_lbl[3]="ifs-comma-read"
  p_why[3]="a comma list is not split by a single read: one read consumes the whole record and the extra names stay empty, so every later field is taken from the wrong value"

  p_re[4]="^${fx_ws}*(function${fx_ws}+)?(${fx_gh_tool}|git|grep|sed|awk|jq|${fx_fetch_tool}|wget|python3|mktemp|date|find|sort|tr|cat|ls)${fx_ws}*[(][)]"
  p_lbl[4]="binary-shadowing"
  p_why[4]="a function named after an external command shadows the binary for the whole shell: every later call reads the stub, and a check that reads a stubbed value reports green"

  p_re[5]="(^|${fx_wordchar})(${fx_by_name_sig}|${fx_by_name_alt})(${fx_ws}|\$)"
  p_lbl[5]="signal-by-name"
  p_why[5]="signalling by name matches every other lane's worker on this shared box: a gate's worker dies mid-run and a NEIGHBOUR's gate reports a failure that is not its own (measured: one such call matched 14 processes box-wide)"

  p_re[6]="^${fx_ws}*${fx_capture_word}${fx_ws}+[A-Za-z_][A-Za-z0-9_]*=${fx_quote}?[$][(]"
  p_lbl[6]="capture-in-declaration"
  p_why[6]="declaring and capturing in one statement masks the command's exit status, so a failed command looks successful and the branch that reports it never runs"

  p_re[7]="(${fx_fetch_tool}|wget)[^|]*[|]${fx_ws}*(${fx_shell_interp}|sh)(${fx_ws}|\$)"
  p_lbl[7]="fetch-piped-to-shell"
  p_why[7]="piping a download straight into a shell executes whatever the network answers with: no review, no pin, no digest, and the failure is a last-write-wins install"

  p_re[8]="(^${fx_ws}*|[;&|]${fx_ws}*|[$][(]${fx_ws}*|(then|do|else|if)${fx_ws}+)${fx_gh_tool}${fx_ws}+issue${fx_ws}+view${fx_ws}+[0-9]"
  p_lbl[8]="bare-issue-view"
  p_why[8]="a bare issue read goes through the deprecated classic-Projects query and returns empty or stale output on these repos, so the title and body a lane acts on are the wrong ones; the fix is REST: gh api repos/<owner>/<repo>/issues/<n> (AGENTS.md, Environment)"

  p_re[9]="(^|${fx_wordchar})${fx_mktemp}${fx_ws}+-d(${fx_ws}*[)]|${fx_ws}+[^${fx_sq}${fx_dq}/[:space:]])"
  p_lbl[9]="scratch-no-template"
  p_why[9]="a scratch directory created with mktemp's own default lands in the shared, periodically-cleaned TMPDIR, which is not private and can vanish mid-run, so the gate dies part-way through; the fix is an explicit /tmp/<name>. template, with the X-run assembled by printf so no literal marker token sits in the source"

  p_re[10]="^${fx_ws}*${fx_cd_w}${fx_ws}+[^&|]*\$"
  p_lbl[10]="unguarded-cd"
  p_why[10]="a cd whose failure is not handled leaves the script operating on whatever the caller's working directory happened to be, so every later relative path is read from the wrong place and the gate reports on a tree it never entered; the fix is cd <dir> || exit 2"

  np=10
}
load_patterns

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/check-shell-patterns.sh                 the gate: self-test, then the tree
  bash scripts/check-shell-patterns.sh --self-test     the provocation alone
  bash scripts/check-shell-patterns.sh --files F...    scan exactly these files
  bash scripts/check-shell-patterns.sh --list          the pattern table
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

# --- the ONE implementation of the rule -------------------------------------
# scan_paths <file>... -> "<path>:<line>|<k>|<code>" for every occurrence, in
# the code part of the line only. Awk, single pass, no pipeline: the pipe is
# where this repo's sharpest shell trap lives (#852/#866), and a doctrine gate
# has no business shipping the shape it warns about.
scan_paths() {
  [ "$#" -gt 0 ] || return 0
  awk -v n="$np" \
    -v p1="${p_re[1]}" -v p2="${p_re[2]}" -v p3="${p_re[3]}" -v p4="${p_re[4]}" \
    -v p5="${p_re[5]}" -v p6="${p_re[6]}" -v p7="${p_re[7]}" -v p8="${p_re[8]}" \
    -v p9="${p_re[9]}" -v p10="${p_re[10]}" \
    -v sq="'" -v dq="\"" '
    BEGIN { P[1] = p1; P[2] = p2; P[3] = p3; P[4] = p4; P[5] = p5; P[6] = p6; P[7] = p7; P[8] = p8; P[9] = p9; P[10] = p10 }
    # strip(s) — the code part of a line: from an unquoted `#` that starts a
    # comment (line start, or preceded by blank/; & | () to end of line.
    function strip(s,   i, c, q, out, last) {
      q = ""; out = ""
      for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (q != "") { if (c == q) q = ""; out = out c; continue }
        if (c == dq || c == sq) { q = c; out = out c; continue }
        if (c == "#") {
          if (out == "") break
          last = substr(out, length(out), 1)
          if (last == " " || last == "\t" || last == ";" || last == "&" || last == "|" || last == "(") break
        }
        out = out c
      }
      return out
    }
    {
      code = strip($0)
      for (k = 1; k <= n; k++) {
        if (P[k] != "" && code ~ P[k]) printf "%s:%d|%d|%s\n", FILENAME, FNR, k, code
      }
    }
  ' "$@"
}

# --- the plants: one violating file per pattern, built from the fragments ---
# Each plant carries exactly ONE violation so "refused by name" is per pattern
# rather than per batch. The comments in the plants name the pattern, which the
# comment half of the self-test then relies on.
plant() { # plant <dir> <k>
  local d="$1" k="$2"
  case "$k" in
    # One violation per file, so "refused by name" is per pattern rather than
    # per batch; the shapes come from the fragments above, never spelled here.
    1) printf 'run_one() {\n  %s "rm -rf x" %s\n}\n' "$fx_cleanup_sig" "$fx_return_sig" > "$d/plant-1.sh" ;;
    2) printf '%s%s\nread -r a b\n' "$fx_sep_assign" "$fx_tab" > "$d/plant-2.sh" ;;
    3) printf '  while %s%s,%s read -r one two three; do :; done\n' "$fx_sep_assign" "$fx_sq" "$fx_sq" > "$d/plant-3.sh" ;;
    4) printf '%s () { :; }\n' "$fx_gh_tool" > "$d/plant-4.sh" ;;
    5) printf '%s -f "bash scripts/check-x.sh"\n' "$fx_by_name_sig" > "$d/plant-5.sh" ;;
    6) printf '%s value=%s(id)\n' "$fx_capture_word" '$' > "$d/plant-6.sh" ;;
    7) printf '%s https://example.invalid/i.sh | %s\n' "$fx_fetch_tool" "$fx_shell_interp" > "$d/plant-7.sh" ;;
    8) printf '%s issue view 621\n' "$fx_gh_tool" > "$d/plant-8.sh" ;;
    9) printf '%s -d)\n' "$fx_mktemp" > "$d/plant-9.sh" ;;
    10) printf '%s "$root"\n' "$fx_cd_w" > "$d/plant-10.sh" ;;
    *) return 2 ;;
  esac
}

# --- the provocation: both ways, and load-bearing --------------------------
self_test() {
  local d rc=0 k out found mine other_out
  local mutant other=1
  TMPD="$(mktemp -d /tmp/ao621-shell-patterns.XXXXXX)" || {
    echo "check-shell-patterns: CANNOT-ASSESS — no scratch directory for the self-test" >&2
    return 2
  }
  d="$TMPD"

  for k in $(seq 1 "$np"); do plant "$d" "$k" || return 2; done
  # A file with nothing to refuse, and the same shape as plant 5 written where
  # a comment can hold it: neither may be refused (vacuity, both directions).
  printf '#!/usr/bin/env bash\nset -u\necho %s\n' '"nothing to see"' > "$d/clean.sh"
  printf '# the shape this line documents: %s -f x\n' "$fx_by_name_sig" > "$d/commented.sh"

  echo "== the patterns, provoked =="

  out="$(scan_paths "$d"/plant-*.sh)"; rc2=$?
  if [ "$rc2" -ne 0 ]; then
    echo "check-shell-patterns: FAIL — the scan itself failed (rc=$rc2)" >&2
    return 1
  fi
  for k in $(seq 1 "$np"); do
    found=0
    while IFS= read -r line; do
      case "$line" in "$d/plant-$k.sh:"*) found=1 ;; esac
    done <<< "$out"
    if [ "$found" -ne 1 ]; then
      printf 'check-shell-patterns: FAIL — %s (%s) was NOT refused; the detector matches nothing\n' \
        "SP-$k" "${p_lbl[$k]}" >&2
      rc=1
    fi
  done
  [ "$rc" -eq 0 ] && echo "  OK    each of the $np planted violations is refused, by name"

  out="$(scan_paths "$d/clean.sh")" || true
  if [ -n "$out" ]; then
    printf 'check-shell-patterns: FAIL — a clean file was refused (%s); the rule over-matches\n' \
      "$(printf '%s' "$out" | head -1)" >&2
    rc=1
  else
    echo "  OK    a file with no violation produces no finding"
  fi

  out="$(scan_paths "$d/commented.sh")" || true
  if [ -n "$out" ]; then
    printf 'check-shell-patterns: FAIL — a shape written in a COMMENT was refused; the rule reads prose\n' >&2
    rc=1
  else
    echo "  OK    the same shape inside a comment is not an occurrence (a comment cannot run)"
  fi

  # The checker's own source: it carries every fragment and every regex, so if
  # any of them were spelled whole this is where it would show.
  out="$(scan_paths "$self")" || true
  if [ -n "$out" ]; then
    printf 'check-shell-patterns: FAIL — this checker is its own finding:\n%s\n' "$out" >&2
    rc=1
  else
    echo "  OK    the checker's own source carries no occurrence (shapes are assembled from fragments)"
  fi

  # Each detector, load-bearing: a mutant with THAT ONE detector replaced by a
  # rule matching nothing must stop refusing its plant, and must still refuse a
  # different one — otherwise the refusal above came from the file existing, or
  # the mutation broke the whole script rather than one rule.
  local mutant="$d/mutant.sh"
  mkdir -p "$d/lib"
  cp "$(dirname "$self")/lib/common.sh" "$d/lib/common.sh"
  for k in $(seq 1 "$np"); do
    other=$((k == np ? 1 : k + 1))
    sed -e "s|^  p_re\[$k\]=.*|  p_re[$k]='ZZQ_matches_nothing'|" "$self" > "$mutant"
    if cmp -s "$self" "$mutant"; then
      printf 'check-shell-patterns: FAIL — the mutant for SP-%s is byte-identical to this script; the mutation proved nothing\n' "$k" >&2
      rc=1
      continue
    fi
    # BOTH streams: a refusal is written to stderr (this gate's convention, and
    # the repo's), so a stdout-only capture would read every refusal as silence
    # and this half would be vacuous — the exact formality it is here to deny.
    mine="$(bash "$mutant" --files "$d/plant-$k.sh" 2>&1)" || true
    case "$mine" in
      *"SP-$k "*)
        printf 'check-shell-patterns: FAIL — SP-%s still refused its plant with its own detector disabled: the refusal is not that rule\n' "$k" >&2
        rc=1
        ;;
    esac
    other_out="$(bash "$mutant" --files "$d/plant-$other.sh" 2>&1)" || true
    case "$other_out" in
      *"SP-$other "*) ;;
      *)
        printf 'check-shell-patterns: FAIL — the mutant for SP-%s stopped refusing SP-%s too; it broke the script instead of one rule\n' "$k" "$other" >&2
        rc=1
        ;;
    esac
  done
  if [ "$rc" -eq 0 ]; then
    echo "  OK    every detector is load-bearing: with it disabled, its own plant is accepted and others are not"
    echo "check-shell-patterns: self-test OK — $np patterns, refused by name, both ways"
  fi

  return "$rc"
}

# --- the repository run -----------------------------------------------------
# The tree the repository owns: tracked plus untracked-but-not-ignored, exactly
# as scripts/check-docs.sh reads it — a lane's just-delivered script is scanned
# before it is committed, and gitignored runtime state is never read (a gate
# must not depend on an artifact the repository does not own, #764).
repo_shell_files() {
  git ls-files --cached --others --exclude-standard -- '*.sh' 2>/dev/null | LC_ALL=C sort
}

# report_finding <path>:<line>|<k>|<code> — ONE formatter for every refusal, so
# the tree run and the provocation read identically: the id, the label, where,
# the code the rule matched, and why the shape fails.
report_finding() {
  local line="$1" k code
  k="${line#*|}"; k="${k%%|*}"; code="${line#*|*|}"
  printf '  REFUSED  SP-%s %s\n' "$k" "${p_lbl[$k]}" >&2
  printf '           %s\n' "${line%%|*}" >&2
  printf '           %s\n' "${code:0:160}" >&2
  printf '           why: %s\n' "${p_why[$k]}" >&2
}

scan_tree() {
  local line k path out code
  local -a files=()
  local -a findings=() corpus=()
  local refused=0 noted=0
  mapfile -t files < <(repo_shell_files)
  if [ "${#files[@]}" -eq 0 ]; then
    echo "check-shell-patterns: CANNOT-ASSESS — no shell file to scan (is this a git checkout?)" >&2
    return 2
  fi

  out="$(scan_paths "${files[@]}")" || return 2

  while IFS= read -r line; do
    [ -n "$line" ] || continue
    k="${line#*|}"; k="${k%%|*}"
    path="${line%%:*}"
    case "$path" in
      */fixtures/*)
        corpus+=("$line")
        noted=$((noted + 1))
        ;;
      *)
        findings+=("$line")
        refused=$((refused + 1))
        ;;
    esac
  done <<< "$out"

  printf '  scanned %s shell file(s)\n' "${#files[@]}"
  if [ "$noted" -ne 0 ]; then
    # A negative-control corpus is the DEFINITION of the violation, so its
    # files are reported and not counted — named on every run, never silent.
    printf '  NOTE  %s occurrence(s) under a fixtures/ path (a corpus fixture, reported not counted):\n' "$noted"
    for line in "${corpus[@]}"; do
      k="${line#*|}"; k="${k%%|*}"; code="${line#*|*|}"
      printf '        %s (SP-%s %s)\n' "${line%%|*}" "$k" "${code:0:80}"
    done
  fi
  if [ "$refused" -ne 0 ]; then
    for line in "${findings[@]}"; do
      report_finding "$line"
    done
    printf 'check-shell-patterns: NOT-OK — %s occurrence(s) refused above (docs/SHELL-PATTERNS.md)\n' "$refused" >&2
    return 1
  fi
  echo "check-shell-patterns: OK — $np patterns clean over ${#files[@]} shell file(s), ${noted} corpus fixture(s) reported"
  return 0
}

# --- verb dispatch ----------------------------------------------------------
hits=0
case "${1:-}" in
  --help | -h)
    usage
    exit 0
    ;;
  --list)
    for k in $(seq 1 "$np"); do printf 'SP-%s  %s — %s\n' "$k" "${p_lbl[$k]}" "${p_why[$k]}"; done
    exit 0
    ;;
  --files)
    shift
    if [ "$#" -eq 0 ]; then
      printf 'check-shell-patterns: CANNOT-ASSESS — --files needs at least one FILE (see --help)\n' >&2
      exit 2
    fi
    for f in "$@"; do
      if [ ! -f "$f" ]; then
        printf 'check-shell-patterns: CANNOT-ASSESS — --files: no such file: %s\n' "$f" >&2
        exit 2
      fi
    done
    out="$(scan_paths "$@")" || exit 2
    if [ -z "$out" ]; then
      echo "check-shell-patterns: OK — $np patterns clean over $# named file(s)"
      exit 0
    fi
    hits=0
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      hits=$((hits + 1))
      report_finding "$line"
    done <<< "$out"
    printf 'check-shell-patterns: NOT-OK — %s occurrence(s) refused above (docs/SHELL-PATTERNS.md)\n' "$hits" >&2
    exit 1
    ;;
  --self-test)
    self_test
    exit $?
    ;;
  '')
    ;;
  *)
    printf 'check-shell-patterns: CANNOT-ASSESS — unknown argument: %s (see --help)\n' "$1" >&2
    exit 2
    ;;
esac

if ! command -v git >/dev/null 2>&1; then
  echo "check-shell-patterns: CANNOT-ASSESS — git is not on PATH" >&2
  exit 2
fi
if ! command -v awk >/dev/null 2>&1; then
  echo "check-shell-patterns: CANNOT-ASSESS — awk is not on PATH" >&2
  exit 2
fi

cd "$root" || { echo "check-shell-patterns: CANNOT-ASSESS — cannot enter $root" >&2; exit 2; }

self_test || exit $?
scan_tree
exit $?
