#!/usr/bin/env bash
# Docs/foundation gate for `make verify` (GR-12): required files present,
# markdown relative links resolve, product text files are free of trailing
# whitespace, and code files carry no unfinished markers. Every branch exits
# nonzero on failure — no-false-green (fleet doctrine).
#
# Legacy extraction artifacts (MIGRATION_NOTES.md, VALIDATION.md,
# .github/workflows/*) are preserved as-is and excluded from the whitespace
# scan; they are not product docs. .github/workflows IS yaml-parsed by the
# yaml-lint check.
#
# THE MARKER RULE, AND THE TRAP IT SPRANG (issue #804)
# The marker branch used to end in a word boundary with NO leading one, so it
# matched the TAIL of any run of three or more capital X — and `mktemp` REQUIRES
# a template ending in at least three X. The canonical scratch idiom
#
#     work="$(mktemp -d /tmp/cbp.XXXXXX)" || exit 2
#
# therefore failed `docs-lint` in every file this scanner reads, with a message
# that names a marker rather than a placeholder — so the flag read as a real
# defect and the only apparent fix was to delete the template. A lane that hit
# it worked around the gate with `python3 -c 'tempfile.mkdtemp()'`, which is not
# what a shell author reaches for, and the repo's own scratch guidance
# recommended the form the gate refused. The rule now carries a LEADING word
# boundary on the token branch: every `mktemp` template is left alone, and a
# real marker is still refused. Both halves are provoked — `--self-test` below,
# and scripts/check-marker-scan.sh, which mutates a COPY of this file back to
# the defective rule and requires the verdict to move.
#
# NAMED BOUNDARY, measured rather than hidden: a run of EXACTLY three X is still
# refused. Token for token it is the standalone marker the rule must refuse, so
# the pattern cannot tell the two apart; the convention is therefore the
# canonical SIX-X template (docs/SCRATCH-SPACE-DISCIPLINE.md).
#
# THE RULE'S OWN LITERAL IS ASSEMBLED FROM FRAGMENTS below, and so is every
# marker spelled in this prose (the fragment `TO""DO` in the code below): this
# file is one of the files the rule reads, so a marker written plainly here
# would flag the scanner itself. That is the defect #804 measured, not a
# nuisance — #804's first fix removed the template and kept a comment explaining
# the trap, and the COMMENT kept the gate red.
#
# Usage:
#   bash scripts/check-docs.sh                    the full docs gate (make verify)
#   bash scripts/check-docs.sh --markers FILE...   the marker rule ALONE, over
#                                                  exactly the named files
#   bash scripts/check-docs.sh --self-test         prove the rule fires BOTH ways
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

fail=0

# --- the unfinished-marker rule, in ONE place (issue #804) ------------------
# The literal is assembled from fragments because this file is one of the files
# the rule reads — see the header. The LEADING word boundary on the token branch
# is the fix: a boundary BEFORE the run of three capital X means only a
# STANDALONE token matches, so the tail of a longer run — every `mktemp`
# template — is left alone.
mk_pattern="(TO""DO|FIX""ME|HA""CK)\\b|\\bXX""X\\b"

# The rule, applied to ONE file: $1 = file, $2 = pattern (default: the shipping
# rule). A function rather than an inline grep so the provocation drives the SAME
# code path the gate runs — a proof that exercises a copy proves nothing about
# the path that runs.
marker_hit() {
  grep -qE "${2:-$mk_pattern}" "$1"
}

# The rule over a file list, printing this gate's own verdict vocabulary.
# $1 = pattern, rest = files. Sets marker_hits; returns 1 when any file was hit.
marker_hits=0
marker_scan_files() {
  local pattern="$1"; shift
  local f
  marker_hits=0
  for f in "$@"; do
    [ -f "$f" ] || continue
    if marker_hit "$f" "$pattern"; then
      printf '  FAIL  %s (unfinished marker)\n' "$f" >&2
      marker_hits=$((marker_hits + 1))
    fi
  done
  if [ "$marker_hits" -ne 0 ]; then
    printf 'unfinished markers: %s file(s) FAILED\n' "$marker_hits" >&2
    return 1
  fi
  echo "unfinished markers: OK"
  return 0
}

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/check-docs.sh                     the full docs gate (make verify)
  bash scripts/check-docs.sh --markers FILE...   the unfinished-marker rule alone,
                                                 over exactly the named files
  bash scripts/check-docs.sh --self-test         prove the rule fires BOTH ways
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

# --- the provocation: BOTH halves, with a mutant behind each (issue #804) ---
# Asserting only "a real marker is still refused" would pass a rule that matches
# NOTHING; asserting only "a template is accepted" would pass the defective
# trailing-only rule. Each half therefore has a mutant that must MOVE the
# verdict, so neither assertion can be satisfied vacuously.
marker_self_test() {
  # Every marker written into a fixture is assembled here, so this file does not
  # spell one literally (it is one of the files the rule reads).
  local M_MARK_1='TO''DO' M_MARK_2='FIX''ME' M_MARK_3='HA''CK' M_TOKEN='XX''X'
  local d out rc2 rc=0 missing="" f
  d="$(mktemp -d /tmp/ao-docs-markers.XXXXXX)" || {
    echo 'check-docs: CANNOT-ASSESS — no scratch directory for the self-test' >&2
    return 2
  }
  trap "rm -rf '$d'" EXIT

  echo "== the marker rule, provoked both ways =="

  # Fixture A — the canonical template, as the line an author writes AND as the
  # line a comment explaining the trap contains: #804 measured that the
  # explanation tripped the rule too.
  {
    printf 'work="$(mktemp -d /tmp/cbp.XXXXXX)" || exit 2\n'
    printf '# the canonical template, spelled in a comment on purpose: %s\n' \
      'mktemp -d /tmp/cbp.XXXXXX'
  } > "$d/template.sh"

  # Fixture B — one real marker per spelling, each in its OWN file, so "refused
  # BY NAME" is per marker rather than per batch.
  printf '# %s: planted by --self-test\n' "$M_MARK_1" > "$d/one.sh"
  printf '# %s: planted by --self-test\n' "$M_MARK_2" > "$d/two.sh"
  printf '# %s: planted by --self-test\n' "$M_MARK_3" > "$d/three.sh"

  # Fixture C — the NAMED BOUNDARY: a run of exactly three X, which is still
  # refused (see the header). Measured and reported, never asserted.
  printf 'mktemp -d /tmp/x.%s\n' "$M_TOKEN" > "$d/token-three.sh"

  # Half 1: the template is NOT flagged.
  if marker_hit "$d/template.sh"; then
    printf 'check-docs: FAIL — the canonical six-X template is STILL refused: the trailing-only rule is back\n' >&2
    rc=1
  else
    echo '  OK  the canonical six-X template is accepted (the line and its comment)'
  fi

  # Half 2: every real marker IS refused, by name.
  # The containment test is bash-native and reads the report from a variable:
  # `printf '%s' "$out" | grep -qF` would make `grep` exit on its first match and
  # SIGPIPE the producer, and `pipefail` promotes that 141 to the pipeline's status
  # -- so this half would report a marker ABSENT while it is present, once the
  # report outgrows the pipe buffer (the defect #852 ratchets shut).
  out="$(marker_scan_files "$mk_pattern" "$d/one.sh" "$d/two.sh" "$d/three.sh" 2>&1)"; rc2=$?
  for f in one.sh two.sh three.sh; do
    case "$out" in
      *"$d/$f"*) ;;
      *) missing="$missing $f" ;;
    esac
  done
  if [ "$rc2" -eq 0 ]; then
    printf 'check-docs: FAIL — a real marker was NOT refused; the rule matches nothing\n' >&2
    rc=1
  elif [ -n "$missing" ]; then
    printf 'check-docs: FAIL — refused but NOT NAMED:%s\n' "$missing" >&2
    rc=1
  else
    echo '  OK  each of the three marker spellings is refused, by name'
  fi

  # Half 1's mutant: the defect #804 measured must refuse the template, or half 1
  # cannot fail and proves nothing.
  local defect="($M_MARK_1|$M_MARK_2|$M_MARK_3|$M_TOKEN)\\b"
  if marker_hit "$d/template.sh" "$defect"; then
    echo '  OK  the DEFECTIVE rule refuses the template — half 1 is load-bearing'
  else
    printf 'check-docs: FAIL — the defective rule accepts the template; half 1 cannot fail\n' >&2
    rc=1
  fi

  # Half 2's mutant: a rule that matches nothing must ACCEPT them, or the
  # refusal above comes from something other than the rule under test.
  local vacuous='ZZQ_this_rule_matches_nothing'
  out="$(marker_scan_files "$vacuous" "$d/one.sh" "$d/two.sh" "$d/three.sh" 2>&1)"; rc2=$?
  if [ "$rc2" -ne 0 ]; then
    printf 'check-docs: FAIL — a rule matching nothing still refused a marker; half 2 cannot fail\n' >&2
    rc=1
  else
    echo '  OK  a rule matching nothing accepts them — half 2 is load-bearing'
  fi

  # The boundary, reported rather than hidden.
  if marker_hit "$d/token-three.sh"; then
    echo '  NOTE  boundary (not a defect): a run of exactly three X is still refused — it is'
    echo '        token-for-token the standalone marker the rule must refuse. Use the canonical'
    echo '        six-X template (docs/SCRATCH-SPACE-DISCIPLINE.md).'
  else
    echo '  NOTE  the three-X boundary has MOVED: a bare three-X run is now accepted, which is'
    echo '        wider than docs/SCRATCH-SPACE-DISCIPLINE.md documents — update that doc and'
    echo '        scripts/check-marker-scan.sh together if that is deliberate.'
  fi

  if [ "$rc" -eq 0 ]; then
    echo 'check-docs: self-test OK — the marker rule fires BOTH ways and each half is load-bearing'
  fi
  return "$rc"
}

# --- verb dispatch (issue #804) ---------------------------------------------
# The rule is provokable on its own: `--self-test` proves it fires both ways, and
# `--markers` applies it to exactly the named files — the artefact-level mutation
# in scripts/check-marker-scan.sh needs the RULE without this repository's
# foundation checks, which no scratch tree can satisfy.
case "${1:-}" in
  --markers)
    shift
    if [ "$#" -eq 0 ]; then
      printf 'check-docs: --markers needs at least one FILE (see --help)\n' >&2
      exit 2
    fi
    for f in "$@"; do
      if [ ! -f "$f" ]; then
        printf 'check-docs: CANNOT-ASSESS — --markers: no such file: %s\n' "$f" >&2
        exit 2
      fi
    done
    echo "== unfinished markers in code (named files) =="
    marker_scan_files "$mk_pattern" "$@" && exit 0
    exit 1
    ;;
  --self-test)
    marker_self_test
    exit $?
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  '')
    ;;
  *)
    printf 'check-docs: unknown argument: %s (see --help)\n' "$1" >&2
    exit 2
    ;;
esac

# The tree the repository owns: tracked files plus untracked-but-not-ignored
# ones. Gitignored paths are runtime state (`.verify/`, caches, scratch) that no
# commit records, and a gate must not read them — otherwise its verdict depends
# on an artifact the repository does not own, and a clean checkout can fail where
# a dirty one passes. The landing driver's own `.verify/` report reddened this
# check exactly that way and blocked every automated landing (issue #764).
# `vendor/` is a pinned submodule, listed by git as a gitlink, so its contents
# are outside the tree by construction.
repo_files() {
  git ls-files --cached --others --exclude-standard -- "$@"
}

# --- 1. Required foundation + pillar files ---------------------------------
echo "== foundation files =="
required=(
  AGENTS.md CLAUDE.md .cursorrules .gitmessage .gitleaks.toml
  .pre-commit-config.yaml CONTRIBUTING.md RELEASING.md Makefile README.md
  .gitignore
  docs/ARCHITECTURE.md docs/EXECUTION-PLAN.md docs/GOVERNANCE.md docs/README.md
  registry/README.md gateway/README.md engine/README.md guardrails/README.md
  telemetry/README.md identity/README.md control-plane/README.md portal/README.md
  infra/README.md
)
for f in "${required[@]}"; do
  if [ -f "$f" ]; then
    printf '  OK    %s\n' "$f"
  else
    printf '  FAIL  %s (missing)\n' "$f" >&2
    fail=$((fail + 1))
  fi
done

# --- 2. Markdown relative-link resolution ----------------------------------
echo "== markdown links =="
md_fail=0
while IFS= read -r md; do
  dir="$(dirname "$md")"
  while IFS= read -r target; do
    [ -z "$target" ] && continue
    case "$target" in
      http://*|https://*|mailto:*|ftp://*|tel:*|data:*|irc:*|\#*) continue ;;
    esac
    # strip inline title, then any fragment
    target="${target%% *}"
    target="${target%%\"*}"
    target="${target%%#*}"
    [ -z "$target" ] && continue
    if [ -e "$dir/$target" ]; then
      :
    else
      printf '  FAIL  %s -> %s (not found)\n' "$md" "$target" >&2
      md_fail=$((md_fail + 1))
    fi
  done < <(grep -oE '\]\([^)]*\)' "$md" | sed -E 's/^\]\((.*)\)$/\1/')
done < <(repo_files '*.md' | LC_ALL=C sort)
if [ "$md_fail" -ne 0 ]; then
  printf 'markdown links: %s broken link(s)\n' "$md_fail" >&2
  fail=$((fail + md_fail))
else
  echo "markdown links: OK"
fi

# --- 3. Trailing whitespace (product text files) ---------------------------
echo "== trailing whitespace =="
ws_fail=0
while IFS= read -r f; do
  if grep -qE '[[:blank:]]+$' "$f"; then
    printf '  FAIL  %s (trailing whitespace)\n' "$f" >&2
    ws_fail=$((ws_fail + 1))
  fi
done < <(repo_files '*.md' '*.sh' '*.py' '*.yaml' '*.yml' '*.toml' \
  'Makefile' '.gitmessage' '.cursorrules' \
  ':(exclude)MIGRATION_NOTES.md' ':(exclude)VALIDATION.md' \
  ':(exclude).github/**' \
  | LC_ALL=C sort)
if [ "$ws_fail" -ne 0 ]; then
  printf 'trailing whitespace: %s file(s) FAILED\n' "$ws_fail" >&2
  fail=$((fail + ws_fail))
else
  echo "trailing whitespace: OK"
fi

# --- 4. Unfinished markers in code -----------------------------------------
echo "== unfinished markers in code =="
mapfile -t mk_files < <(repo_files '*.sh' '*.py' '*.go' | LC_ALL=C sort)
if [ "${#mk_files[@]}" -gt 0 ]; then
  # The rule and its verdict vocabulary live in marker_scan_files above, so the
  # gate, `--markers` and the provocation all exercise ONE implementation.
  marker_scan_files "$mk_pattern" "${mk_files[@]}" || fail=$((fail + marker_hits))
else
  echo "unfinished markers: OK"
fi

# --- Summary ----------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  printf 'docs: %s problem(s)\n' "$fail" >&2
  exit 1
fi
echo "docs: OK"
