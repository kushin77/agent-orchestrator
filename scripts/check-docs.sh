#!/usr/bin/env bash
# Docs/foundation gate for `make verify` (GR-12): required files present,
# markdown relative links resolve, product text files are free of trailing
# whitespace, code files carry no unfinished markers, and every tracked
# docs/*.md is indexed in docs/README.md (issue #629, EPIC #616). Every
# branch exits nonzero on failure — no-false-green (fleet doctrine).
#
# Legacy extraction artifacts (MIGRATION_NOTES.md, VALIDATION.md) are preserved
# as-is and excluded from the whitespace scan; they are not product docs, and
# the `.github/**` tree is excluded with them. This repository carries NO
# `.github/workflows/` directory — GitHub Actions is disabled fleet-wide
# (GR-15) — so there is no workflow file here for a CI parser to read
# (measured 2026-09-17).
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
#   bash scripts/check-docs.sh --self-test         prove the marker rule AND the
#                                                  docs-index rule (issue #629)
#                                                  each fire both ways
#   bash scripts/check-docs.sh --fix               regenerate the docs index
#                                                  table (issue #1672); never
#                                                  hand-edit docs/README.md's
#                                                  index — run `make docs-index`
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
  out="$(marker_scan_files "$mk_pattern" "$d/one.sh" "$d/two.sh" "$d/three.sh" 2>&1)"; rc2=$?
  for f in one.sh two.sh three.sh; do
    # Bash-native (#868, the #843/#852 idiom): `printf '%s' "$out" | grep -qF` is not
    # a containment test — `grep -q` exits on its first match, SIGPIPE kills the
    # producer, and `set -o pipefail` promotes that 141 to the whole pipeline, so a
    # large report makes this report a file that IS named as MISSING.
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

repo_files() {
  git ls-files --cached --others --exclude-standard -- "$@"
}


# --- 5. Docs index completeness (issue #629, EPIC #616) --------------------
# Every tracked docs/*.md must be reachable from docs/README.md, or it is
# discoverable only by accident (issue #608). Two escape hatches, both
# committed here rather than left to drift:
#   - deliberately unindexed DIRECTORIES (decision-records/, spikes/,
#     contracts/) whose members are indexed by their own local README, not
#     the top-level one;
#   - a per-FILE quarantine baseline for docs that predate this gate and are
#     not yet folded into the index, held open under issue #629 itself (this
#     gate is hermetic/offline by design — no live GitHub call to test each
#     entry's tracking issue for state, the `.verify/`-authority lesson of
#     issue #764 above applies just the same to a network call here) — so
#     "honored while open" is enforced by review, and by the STALE-ENTRY
#     checks below catching the file once it stops needing the exemption.
# The baseline is checked in BOTH directions: an entry for a file that no
# longer exists, or that has since been indexed, is a FAIL too — so the list
# is a measured snapshot, not a permanent amnesty, and cannot rot into a
# fiction (issue #629 acceptance criteria). "Indexed" means an inline
# `](target)` markdown link in docs/README.md; a reference-style link or a
# bare backticked path does not count.
idx_excluded_dirs=(
  docs/decision-records/
  docs/spikes/
  docs/contracts/
  docs/rca/
  docs/rollback/
)

# Quarantine baseline: tracked docs/*.md not yet indexed in docs/README.md.
# Introduced by issue #629 and EMPTIED of exemptions by issue #1206: the 31
# docs it held are now folded into docs/README.md, so no doc is exempt and
# every tracked docs/*.md must be reachable from the index. The array is kept
# (and still checked in BOTH directions, below) so that any future exemption
# has to be declared here rather than left silently out of the index.
idx_quarantine=(
)

# Every link target in docs/README.md, normalized to a repo-root-relative
# path (links are written relative to docs/, e.g. `ARCHITECTURE.md` or
# `../CONTRIBUTING.md`), so membership can be tested by exact path. Populates
# the global idx_indexed associative array; called once against the real
# docs/README.md, below.
declare -A idx_indexed
docs_index_build_indexed_map() {
  local target idx_abs idx_rel
  idx_indexed=()
  # Without this, an unreadable docs/README.md would come back as an empty
  # map and every doc would read as "not indexed" (loud) or, worse in
  # --self-test, as a vacuously clean baseline (silent) — the exact failure
  # mode the marker rule's own mutants exist to catch. CANNOT-ASSESS, not a
  # false OK.
  [ -r docs/README.md ] || {
    echo 'check-docs: CANNOT-ASSESS — docs/README.md unreadable; the index cannot be tested' >&2
    return 2
  }
  while IFS= read -r target; do
    [ -z "$target" ] && continue
    case "$target" in
      http://*|https://*|mailto:*|ftp://*|tel:*|data:*|irc:*|\#*) continue ;;
    esac
    target="${target%% *}"
    target="${target%%\"*}"
    target="${target%%#*}"
    [ -z "$target" ] && continue
    idx_abs="$(realpath -m "docs/$target" 2>/dev/null)" || continue
    idx_rel="${idx_abs#"$root"/}"
    idx_indexed["$idx_rel"]=1
  done < <(grep -oE '\]\([^)]*\)' docs/README.md | sed -E 's/^\]\((.*)\)$/\1/')
}

# Every tracked docs/*.md not indexed, not excluded-by-directory, and not in
# the quarantine list named by $1 (a nameref to an array). Prints FAILs to
# stderr and returns the count via idx_missing.
idx_missing=0
docs_index_missing() {
  local -n _quarantine="$1"
  local -A _quarantined=()
  local idx_q idx_f idx_skip idx_d
  for idx_q in "${_quarantine[@]+"${_quarantine[@]}"}"; do
    _quarantined["$idx_q"]=1
  done
  idx_missing=0
  while IFS= read -r idx_f; do
    [ "$idx_f" = "docs/README.md" ] && continue
    idx_skip=0
    for idx_d in "${idx_excluded_dirs[@]}"; do
      case "$idx_f" in "$idx_d"*) idx_skip=1; break ;; esac
    done
    [ "$idx_skip" -eq 1 ] && continue
    [ -n "${idx_indexed[$idx_f]:-}" ] && continue
    [ -n "${_quarantined[$idx_f]:-}" ] && continue
    printf '  FAIL  %s (tracked doc not indexed in docs/README.md)\n' "$idx_f" >&2
    idx_missing=$((idx_missing + 1))
  done < <(repo_files 'docs/*.md' 'docs/**/*.md' | LC_ALL=C sort -u)
}

# The baseline named by $1, checked in BOTH directions: a FAIL for any entry
# whose file no longer exists (gone), and a FAIL for any entry whose file IS
# now indexed (stale amnesty). Prints FAILs to stderr and returns the count
# via idx_stale.
idx_stale=0
docs_index_stale() {
  local -n _q="$1"
  local idx_q
  idx_stale=0
  for idx_q in "${_q[@]+"${_q[@]}"}"; do
    if [ ! -f "$idx_q" ]; then
      printf '  FAIL  %s (quarantine baseline entry no longer exists; remove it)\n' "$idx_q" >&2
      idx_stale=$((idx_stale + 1))
      continue
    fi
    if [ -n "${idx_indexed[$idx_q]:-}" ]; then
      printf '  FAIL  %s (quarantine baseline entry is now indexed; remove it)\n' "$idx_q" >&2
      idx_stale=$((idx_stale + 1))
    fi
  done
}

# The provocation (issue #629): both stale-baseline directions, over the REAL
# indexed map, so this exercises the same idx_indexed this gate computes —
# not a copy. A "gone" mutant (file that does not exist) and a "now indexed"
# mutant (docs/ARCHITECTURE.md, which IS indexed today) are each appended to
# a COPY of the real baseline; each half must move the verdict on its own,
# or the check is vacuous.
docs_index_self_test() {
  local rc=0
  echo "== the docs-index baseline rule, provoked both ways =="
  if ! docs_index_build_indexed_map; then
    echo 'check-docs: CANNOT-ASSESS — docs-index self-test cannot read docs/README.md' >&2
    return 2
  fi

  local -a gone_copy=("${idx_quarantine[@]+"${idx_quarantine[@]}"}" "docs/DOES-NOT-EXIST-629.md")
  docs_index_stale gone_copy
  if [ "$idx_stale" -ge 1 ]; then
    echo '  OK  a baseline entry for a file that no longer exists is refused'
  else
    printf 'check-docs: FAIL — a gone baseline entry was NOT refused\n' >&2
    rc=1
  fi

  local -a indexed_copy=("${idx_quarantine[@]+"${idx_quarantine[@]}"}" "docs/ARCHITECTURE.md")
  docs_index_stale indexed_copy
  if [ "$idx_stale" -ge 1 ]; then
    echo '  OK  a baseline entry for a doc that is now indexed is refused'
  else
    printf 'check-docs: FAIL — a now-indexed baseline entry was NOT refused\n' >&2
    rc=1
  fi

  # The real baseline, unmutated, must be clean — proves the two mutants
  # above are what moved the verdict, not a rule that always fires.
  docs_index_stale idx_quarantine
  if [ "$idx_stale" -ne 0 ]; then
    printf 'check-docs: FAIL — the real baseline is not clean (%s stale entries)\n' "$idx_stale" >&2
    rc=1
  elif [ "${#idx_quarantine[@]}" -eq 0 ]; then
    # An EMPTY baseline is the intended state as of issue #1206 — no doc is
    # exempt. Reported rather than passed over in silence: the stale rule is
    # still load-bearing (proved by the two mutants above), but on the real
    # baseline it has nothing to inspect, and saying so is the honest verdict.
    echo '  NOTE  the real baseline is EMPTY — no doc is exempt; the stale rule is proved by the mutants above'
  else
    echo '  OK  the real, unmutated baseline is clean — the mutants above are load-bearing'
  fi

  if [ "$rc" -eq 0 ]; then
    echo 'check-docs: docs-index self-test OK — the baseline rule fires both ways'
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
    self_test_rc=0
    marker_self_test || self_test_rc=1
    docs_index_self_test || self_test_rc=1
    exit "$self_test_rc"
    ;;
  --fix)
    exec python3 "$root/scripts/docs-index-fix.py"
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


# --- 5. Docs index completeness (issue #629, EPIC #616) --------------------
# Rule, baseline and mutants live above, before verb dispatch, so `--self-test`
# exercises the SAME functions the gate runs (issue #804's own lesson).
echo "== docs index completeness =="
if docs_index_build_indexed_map; then
  docs_index_missing idx_quarantine
  docs_index_stale idx_quarantine
  idx_fail=$((idx_missing + idx_stale))
else
  idx_fail=1
fi
if [ "$idx_fail" -ne 0 ]; then
  printf 'docs index completeness: %s problem(s)\n' "$idx_fail" >&2
  fail=$((fail + idx_fail))
else
  echo "docs index completeness: OK"
fi

# --- Summary ----------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  printf 'docs: %s problem(s)\n' "$fail" >&2
  exit 1
fi
echo "docs: OK"
