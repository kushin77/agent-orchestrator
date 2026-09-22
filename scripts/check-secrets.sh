#!/usr/bin/env bash
# Secrets gate for `make verify` (GR-6): a mechanical, always-on pattern scan
# over the repo's text files. The gate never depends on the gitleaks binary
# being installed; `.gitleaks.toml` is consumed by the optional `make gitleaks`
# target and by the pre-commit gitleaks hook. Any finding is a hard failure.
#
# TWO DETECTORS THAT COULD NOT FIRE (issue #938)
# Both shipped, both were invisible, and both were found by MEASURING this file
# rather than reading it. They are the same failure class: a rule that cannot
# fire, whose inability to fire is discarded along with the evidence.
#
#   1. THE PLACEHOLDER EXEMPTION WAS UNREACHABLE. RE_PLACE began with `(?i)`,
#      which is a PCRE inline flag -- the pattern was ported verbatim from the
#      Python reference at
#      vendor/CMR/guardrails/policy/secrets/secrets_policy.py, where the `re`
#      module makes `(?i)` a MODE. Under `grep -E` it is not a mode and not an
#      error either: grep warns `? at start of expression`, compiles anyway,
#      and then requires a LITERAL `?i` in the text. So the exemption matched
#
# ---knowledge---
# module_id: scripts.check-secrets
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, named-refusal]
# derives_from: null
# owner_sme: security-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#938"]
# do_not_duplicate: null
# ---knowledge---
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
#      nothing a placeholder ever contains, and every generic assignment with
#      an >=8-char value was refused even when the value said `placeholder`.
#      A lane was blocked by it on a fixture that was obviously fake.
#      Case-insensitivity is the MATCHER's job (`grep -i`), never the pattern's:
#      a mode belongs to the engine, so a pattern carrying one is engine-locked.
#
#   2. THE HIGH-SIGNAL BRANCH NEVER FIRED EITHER. SHAPES is an alternation
#      whose FIRST alternative is RE_EC2KEY, which starts `-----BEGIN`, so the
#      pattern starts with `-` -- and the call was `grep -nHE "$SHAPES" "$f"`
#      with no `--`. grep parsed the PATTERN as an OPTION, exited 2
#      (`unrecognized option`), and the call site threw that stderr away
#      (`2>/dev/null`) and swallowed the status (`|| true`), so the branch
#      contributed ZERO findings for EVERY file. MEASURED over this tree: 2494
#      files scanned, 0 high-signal hits -- while all seven shapes each matched
#      their own sample once the guard was passed. The check could not have
#      caught a private key, an AWS key, a GitHub PAT, an OpenAI key, a Google
#      key or a Slack token. Fixed with `--`; the swallow is fixed with it, so
#      a status above 1 is reported as CANNOT-ASSESS and never read as clean
#      (a control that fails open is worse than none -- AO-GR-25).
#
# HOW IT PROVES ITSELF (a control that cannot fail is a formality, GR-12)
# `--self-test` runs on EVERY invocation, before the tree is read, and each half
# carries a MUTANT that must MOVE the verdict, so no assertion here can be
# satisfied vacuously:
#   * the placeholder is exempt AND the defective `(?i)` form -- the very defect
#     -- must refuse it (the half that would pass a dead exemption);
#   * a bare assignment is refused BY NAME AND an exemption widened with a
#     marker that is a substring of the key name must swallow it (the half that
#     would pass a hole);
#   * a high-signal shape is refused BY NAME AND the same pattern WITHOUT the
#     guard must go silent (the half that would pass a dead shape branch).
# Fixture values are assembled from fragments so this file cannot be its own
# finding -- the convention in guardrails/dlp/tests/support.py.
#
# VERDICT VOCABULARY -- a finding names the DETECTOR that fired, so "refused"
# is a refusal BY NAME: high-signal-shape / generic-assignment.
#
# THE GATE'S OWN OUTPUT IS NOT THE TREE (issue #1983). `make verify` records
# every check's transcript in `.verify/.check-out/<name>.txt` and its verdict in
# `.verify/attestation.json`. This scan walked the worktree with `find`, so a
# SECOND run in the SAME venue read the FIRST run's own record -- and a record of
# a finding quotes the detector verbatim, so the record became a new finding.
# MEASURED in one lane worktree, back to back: run 1 `secrets: OK (3241 file(s)
# scanned)`, run 2 `secrets: OK (3416 file(s) scanned)` with the extra 175 files
# ALL the gate's own output. And a red is one prior finding away -- measured in
# the same venue: control (record emptied) rc=0; run A (a real `sk-` credential
# planted in a TRACKED file) rc=1 refused BY NAME; run B (that credential
# REMOVED from the tree, only the record changed) rc=1 with its one finding being
#   ./.verify/.check-out/secrets.txt:16:  FAIL ./zz-...-control.txt:1 ... (possible secret: high-signal-shape) (possible secret: high-signal-shape)
# -- a clean tree, red anyway. So the gate was not idempotent, and a lane that
# re-gated after a rebase (the DOCUMENTED practice: a squash lands on a newer
# master, so a verdict reached at an old base is inadmissible) earned a red that
# had nothing to do with its diff.
#
# The declared generated roots (scripts/gate-generated-roots.txt -- the ONE
# declaration, read here and by check-gitignore.sh / check-yaml.py) are therefore
# pruned from the walk, and the exclusion is MEASURED rather than assumed: a root
# is pruned only while git ignores it AND it holds no tracked file, and both
# halves are refused BY NAME below (check-gitignore check 3 asserts the second
# half on the same declaration). An exclusion that could hide a tracked file is a
# blind spot, not a scope.
#
# STATED, NOT IMPLIED: a secret planted INSIDE a declared generated root is OUT
# OF SCOPE BY DESIGN. That content is gitignored gate output -- it never reaches
# a commit, so it is not a repository leak, and this scan says so rather than
# pretending to judge it. The self-test provokes all three directions: the
# declared root is not walked, the exclusion is LOAD-BEARING (with no declaration
# the same secret IS walked), and a genuine credential on an ordinary path is
# still refused by name.
#
# Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/check-secrets.sh              the scan (make verify)
#   bash scripts/check-secrets.sh --self-test  the provocation alone
set -u

root="$(find_repo_root)"
cd "$root" || exit 2

failed=0
count=0
st_passed=0
st_failed=0

# High-signal secret shapes (never exempted — no legitimate placeholder shape).
#
# THE OpenAI-KEY SHAPE IS ANCHORED AT A WORD BOUNDARY (issue #1975, the #1872
# bug class). Unanchored, the OpenAI prefix matched INSIDE a word: any token
# whose text happens to contain "ta" + "sk" + "-" + 16 more word characters — an
# ordinary generated index path, or a source comment — was refused as a key.
# MEASURED on master before this fix: 19 findings, most of them exactly that
# shape over `catalog/indexer/index-graph.json` and one source comment. `\b` is
# grep -E's word-boundary assertion and is exact here: it fires only where the
# preceding character is not a word character, which is precisely the negative
# lookbehind `(?<![A-Za-z0-9_])` #1872 used in the Python scanner
# (governance/cto-overlay/overlay.py) — grep -E has no lookbehind. The
# alternation and the quantifier are UNCHANGED, so a genuine `sk-` credential is
# still refused, at a boundary; the self-test below provokes both directions, and
# (per #1872) no comment here carries a continuous prefix-plus-16 run.
RE_EC2KEY='-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY( BLOCK)?-----'
RE_AWS='AKIA[0-9A-Z]{16}'
RE_GHP='(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36,}'
RE_GHAPP='github_pat_[A-Za-z0-9_]{20,}'
RE_SK='\b(sk|sk-ant|sk-proj)-[A-Za-z0-9_\-]{16,}'
RE_GOOG='AIza[0-9A-Za-z_\-]{30,}'
RE_SLACK='xox[baprs]-[0-9A-Za-z\-]{10,}'
SHAPES="$RE_EC2KEY|$RE_AWS|$RE_GHP|$RE_GHAPP|$RE_SK|$RE_GOOG|$RE_SLACK"

# Generic secret-key assignment: key contains a secret word followed by an
# assignment with a quoted value of >= 8 chars.
RE_GEN="(api[_-]?key|secret|password|token|access[_-]?key|client[_-]?secret)[\"']?[[:space:]]*[:=][[:space:]]*[\"'][^\"']{8,}[\"']"

# Placeholder/example markers: a generic match whose line carries one of these
# is documentation, not a leak (mirrors the .gitleaks.toml allowlist).
#
# NO INLINE FLAG HERE (defect 1, header): the leading `(?i)` this pattern shipped
# with was a PCRE/Python flag doing nothing under ERE except demanding a literal
# `?i`, which silently reduced the exemption to dead code. The pattern stays
# exactly this set of markers -- the fix is to remove the flag, NOT to widen the
# exemption (an exemption that is too wide is a hole, and the self-test below
# provokes exactly that). Case-blindness comes from `grep -i` in is_placeholder.
RE_PLACE='(example|sample|placeholder|dummy|fake|changeme|replace[-_ ]?me|xxxxx|your[-_ ]?key|<[^>]{1,60}>|redacted|not[-_ ]?a[-_ ]?real)'

# --- the gate's own generated roots (issue #1983) ---------------------------
# ONE declaration, read here as well as by `check-gitignore.sh` and
# `check-yaml.py` -- never re-typed. Two copies could disagree, and the half that
# would then be wrong is the half that believes an exclusion is safe.
ROOTS_FILE="$root/scripts/gate-generated-roots.txt"

# The declared roots: comments and blank lines dropped. rc 1 when the file is
# unreadable -- never an empty list read as "declares nothing", which is the
# defect this fixes wearing a different coat.
generated_roots() { # [declaration-file] -> one repo-relative root per line
  local f="${1:-${SECRETS_ROOTS_FILE:-$ROOTS_FILE}}"
  [ -r "$f" ] || return 1
  sed -e 's/#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$f" |
    grep -v '^$' || true
}

# The declared roots, VALIDATED before any walk. Both halves are measured
# against git's own matcher instead of asserted, because an exclusion is exactly
# the kind of fix that quietly becomes a blind spot: a root git does not ignore
# could hide untracked work, and a root holding a tracked file could hide that
# file. Either is refused BY NAME.
declared_roots() { # -> validated roots on stdout; rc 1 + named refusal on stderr
  local roots r f="${SECRETS_ROOTS_FILE:-$ROOTS_FILE}"
  if [ "$(git -C "$root" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
    printf 'check-secrets: CANNOT-ASSESS -- %s is not a git work tree, so the declared generated roots cannot be measured (issue #1983)\n' "$root" >&2
    return 1
  fi
  roots="$(generated_roots "$f")" || {
    printf 'check-secrets: CANNOT-ASSESS -- the gate-generated-roots declaration is unreadable: %s\n' "$f" >&2
    return 1
  }
  if [ -z "$roots" ]; then
    printf 'check-secrets: CANNOT-ASSESS -- %s declares NO generated root; a tree walk with no exclusion list cannot be judged idempotent (issue #1983)\n' "$f" >&2
    return 1
  fi
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    if ! git check-ignore -q --no-index -- "$r/ao-generated-root-probe" 2>/dev/null; then
      printf 'check-secrets: FAIL -- declared generated root %s is NOT gitignored; excluding it would be a blind spot, not a scope (issue #1983)\n' "$r" >&2
      return 1
    fi
    if [ -n "$(git ls-files -- "$r" 2>/dev/null)" ]; then
      printf 'check-secrets: FAIL -- declared generated root %s holds a TRACKED file; excluding it would hide it (issue #1983)\n' "$r" >&2
      return 1
    fi
  done <<<"$roots"
  printf '%s\n' "$roots"
}

# The judged set: the tree, MINUS the gate's own declared generated roots. The
# prune list is READ, never spelled out here (issue #1983). `[base]` exists so
# the self-test can drive a fixture root through the same code path.
text_files() { # [base-dir, default '.']
  local base="${1:-.}" prune=() r
  while IFS= read -r r; do
    prune+=( -not -path "$base/$r" -not -path "$base/$r/*" )
  done < <(generated_roots)
  find "$base" -type f \( \
    -name '*.md' -o -name '*.sh' -o -name '*.py' -o -name '*.go' -o \
    -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.json' -o \
    -name '*.txt' -o -name '*.tsv' -o -name '*.csv' -o -name 'Makefile' -o \
    -name '.gitmessage' -o -name '.gitignore' -o -name '.gitmodules' -o \
    -name '.gitleaks.toml' -o -name '.cursorrules' \) \
    -not -path "$base/.git/*" \
    -not -path "$base/vendor/*" \
    -not -path "$base/.research/*" \
    "${prune[@]}" \
    | LC_ALL=C sort
}

# --- the detectors, as functions --------------------------------------------
# Both are functions so the provocation below drives the SAME code path the scan
# runs: a proof that exercises a COPY proves nothing about the path that runs.

# The exemption test. Not a pipe: `printf ... | grep -q` is the idiom that
# reports text PRESENT as ABSENT once the producer outgrows the pipe buffer, and
# negated -- which is exactly how it is used here -- it is a false red
# (scripts/check-verdict-contains.sh, #852). A here-string has no producer
# process for a short-circuiting consumer to SIGPIPE.
# $2 is the exemption pattern, defaulting to the shipped one. It exists so the
# self-test can drive THIS function with a mutant; the scan passes the
# invocation's own pattern, which is the shipped default for every real run.
is_placeholder() { # is_placeholder <line-text> [exemption-pattern]
  grep -iE "${2:-$RE_PLACE}" <<<"$1" >/dev/null
}

# The high-signal scan. `--` is LOAD-BEARING (defect 2, header): SHAPES begins
# with `-` because RE_EC2KEY starts `-----BEGIN`, so without the guard grep reads
# the pattern as an option, exits 2, and matches nothing at all.
shapes_scan() { # shapes_scan <file> -> matching lines; rc 0/1 assessed, >=2 not
  grep -nHE -- "$SHAPES" "$1"
}

report() { # report <detector-name> <hit-line>
  printf '  FAIL  %s (possible secret: %s)\n' "$2" "$1" >&2
  scan_findings=$((scan_findings + 1))
}

# ONE file's findings, in ONE place. The self-test drives THIS, so the code path
# under provocation is the one the repo run uses.
# $2 = the exemption pattern for this invocation (default: the shipped RE_PLACE).
scan_file() { # scan_file <file> [exemption-pattern]
  local f="$1" place="${2:-$RE_PLACE}" hits rc=0 text=''
  scan_findings=0

  # High-signal shapes: report every match. A status above 1 means the engine
  # could not assess the file at all: reported, never swallowed -- a scan that
  # did not run is not a clean file, which is the half of defect 2 that let it
  # hide for so long.
  hits="$(shapes_scan "$f")" || rc=$?
  if [ "$rc" -gt 1 ]; then
    printf '  FAIL  %s (CANNOT-ASSESS: high-signal grep rc=%s)\n' "$f" "$rc" >&2
    scan_findings=$((scan_findings + 1))
  elif [ -n "$hits" ]; then
    while IFS= read -r hit; do
      [ -n "$hit" ] || continue
      report high-signal-shape "$hit"
    done <<<"$hits"
  fi

  # Generic secret-key assignments, exempting example/placeholder values.
  rc=0
  hits="$(grep -nHE "$RE_GEN" "$f")" || rc=$?
  if [ "$rc" -gt 1 ]; then
    printf '  FAIL  %s (CANNOT-ASSESS: generic grep rc=%s)\n' "$f" "$rc" >&2
    scan_findings=$((scan_findings + 1))
  elif [ -n "$hits" ]; then
    while IFS= read -r hit; do
      [ -n "$hit" ] || continue
      text="${hit#*:}"
      is_placeholder "$text" "$place" || report generic-assignment "$hit"
    done <<<"$hits"
  fi
}

scan_tree() {
  echo "== secrets (mechanical scan) =="
  # The declared generated roots are resolved AND validated before a single file
  # is read: a walk that cannot say what it excludes cannot be judged idempotent
  # (issue #1983). An unreadable or empty declaration is CANNOT-ASSESS, never a
  # silent "exclude nothing" -- that silent behaviour IS the defect.
  local roots
  if ! roots="$(declared_roots)"; then
    return 2
  fi
  printf '  scope: walk of %s pruning the declared generated root(s): %s\n' \
    "$root" "$(printf '%s' "$roots" | tr '\n' ',')"
  while IFS= read -r f; do
    count=$((count + 1))
    scan_file "$f"
    failed=$((failed + scan_findings))
  done < <(text_files)

  if [ "$failed" -ne 0 ]; then
    printf 'secrets: %s finding(s) across %s file(s)\n' "$failed" "$count" >&2
    return 1
  fi
  printf 'secrets: OK (%s file(s) scanned)\n' "$count"
  return 0
}

# --- the provocation: every half, with the mutant that must move it ---------
# Asserting only "the placeholder is exempt" would pass a DEAD exemption.
# Asserting only "a bare value is refused" would pass an exemption so wide it
# admits everything. Asserting only "a shape is refused" would pass the
# unguarded call. Each half therefore carries a mutant that must MOVE the
# verdict, so none of the three can be satisfied vacuously (GR-12).
st_ok() { printf '  OK    %s\n' "$1"; st_passed=$((st_passed + 1)); }
st_bad() { printf '  FAIL  %s\n' "$1" >&2; st_failed=$((st_failed + 1)); }
st_has() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

self_test() {
  local d rc=0 out=''
  d="$(mktemp -d /tmp/ao-secrets-selftest.XXXXXX)" || {
    echo 'check-secrets: CANNOT-ASSESS -- no scratch directory for the self-test' >&2
    return 2
  }
  trap "rm -rf '$d'" EXIT

  # Every fixture VALUE is assembled from FRAGMENTS: this file is one of the
  # files the scan reads, so a secret shape or an assignment spelled whole here
  # would make the checker its own finding (guardrails/dlp/tests/support.py).
  local fx_kw='pass''word' fx_key='api_''key'
  local fx_ph='fake-keydb-placeholder-1234567890'
  local fx_bare='Zq7Wm3Kd9Rt2Vx5Lp8Bn'
  local fx_aws_a='AK''IA' fx_aws_b='IOSFODNN7EXAMPLE'
  # The shipped defect, verbatim: the Python flag prefixed onto the markers.
  local fx_dead="(?i)$RE_PLACE"
  # An over-wide exemption a lane could plausibly write: the marker is a
  # SUBSTRING of the assignment's own key name, so it exempts everything.
  local fx_wide="$RE_PLACE|key"

  printf '%s="%s"\n' "$fx_kw" "$fx_ph" > "$d/placeholder.txt"
  printf '%s="%s"\n' "$fx_key" "$fx_bare" > "$d/bare.txt"
  printf '%s%s\n' "$fx_aws_a" "$fx_aws_b" > "$d/shape.txt"
  printf '%s\n' 'a clean control file with nothing to find' > "$d/clean.txt"

  printf '== the detectors, provoked both ways ==\n'

  # (0) The defect itself, reproduced, so the controls below are known to be
  #     about the defect and not about something adjacent.
  grep -iE "$fx_dead" /dev/null >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 1 ]; then
    st_ok "the (?i) form is INERT under grep -E: it compiled (rc=1) and matched nothing"
  else
    st_bad "the (?i) form exited $rc -- the reproduction of defect 1 is wrong"
  fi
  # stderr is discarded on this ONE call: the mutant's `? at start of expression`
  # warning IS the defect's signature, and the control above already asserts the
  # inert behaviour it announces, so repeating it on every gate run is noise.
  if is_placeholder 'x = "?iexample12"' "$fx_dead" 2>/dev/null; then
    st_ok "the (?i) form matches only a LITERAL '?i' -- a flag, not a mode"
  else
    st_bad "the (?i) form did not match a literal '?i' -- the reproduction is wrong"
  fi

  # (a) the placeholder IS exempt, and the DEAD exemption must move that.
  scan_file "$d/placeholder.txt" 2>"$d/a.err"
  if [ "$scan_findings" -eq 0 ]; then
    st_ok "the placeholder assignment is EXEMPT (0 findings)"
  else
    st_bad "the placeholder assignment was refused ($scan_findings finding(s)): $(cat "$d/a.err")"
  fi
  scan_file "$d/placeholder.txt" "$fx_dead" 2>"$d/a-mutant.err"
  if [ "$scan_findings" -gt 0 ]; then
    st_ok "MUTANT the (?i) exemption refuses it ($scan_findings finding(s)): half (a) is load-bearing"
  else
    st_bad "MUTANT the (?i) exemption still exempted the placeholder -- half (a) proves nothing"
  fi

  # (b) a bare value is refused BY NAME, and an OVER-WIDE exemption must move
  #     that -- otherwise the exemption could be a hole rather than a fix.
  scan_file "$d/bare.txt" 2>"$d/b.err"
  if [ "$scan_findings" -eq 1 ] && st_has "$(cat "$d/b.err")" 'generic-assignment'; then
    st_ok "a bare generic assignment is refused BY NAME (generic-assignment)"
  else
    st_bad "a bare generic assignment was not refused by name: $(cat "$d/b.err")"
  fi
  scan_file "$d/bare.txt" "$fx_wide" 2>"$d/b-mutant.err"
  if [ "$scan_findings" -eq 0 ]; then
    st_ok "MUTANT an exemption widened with 'key' swallows it: half (b) is load-bearing"
  else
    st_bad "MUTANT the widened exemption still refused it -- half (b) proves nothing"
  fi

  # (c) a high-signal shape is refused BY NAME, and the UNGUARDED call must go
  #     silent -- that silent, status-swallowed call is defect 2.
  scan_file "$d/shape.txt" 2>"$d/c.err"
  if [ "$scan_findings" -eq 1 ] && st_has "$(cat "$d/c.err")" 'high-signal-shape'; then
    st_ok "a high-signal shape is refused BY NAME (high-signal-shape)"
  else
    st_bad "a high-signal shape was not refused by name: $(cat "$d/c.err")"
  fi
  out="$(grep -nHE "$SHAPES" "$d/shape.txt" 2>"$d/c-mutant.err")" || rc=$?
  if [ "$rc" -gt 1 ] && [ -z "$out" ]; then
    st_ok "MUTANT without the guard grep exits $rc and reports NOTHING: the '--' is load-bearing"
  else
    st_bad "MUTANT the unguarded call still caught the shape (rc=$rc) -- the guard proves nothing"
  fi

  # (d) the word-boundary anchor on the OpenAI-key shape (#1975). Both
  #     directions, because a fix that only silenced the false positive would be
  #     a hole: a genuine key MUST still be refused, and the "ta"+"sk" tail of an
  #     ordinary word must NOT be. Fixtures are assembled from fragments so this
  #     file is not its own finding.
  local fx_sk_p='s'"k-" fx_sk_body='PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPP'
  local fx_fp_a='ta' fx_fp_b='sk' fx_fp_c='-1-review-request'
  local fx_sk_unanchored='(sk|sk-ant|sk-proj)-[A-Za-z0-9_\-]{16,}'
  printf '%s\n' "$fx_sk_p$fx_sk_body" > "$d/sk-real.txt"
  printf '%s\n' "$fx_fp_a$fx_fp_b$fx_fp_c" > "$d/sk-word.txt"

  scan_file "$d/sk-real.txt" 2>"$d/d.err"
  if [ "$scan_findings" -eq 1 ] && st_has "$(cat "$d/d.err")" 'high-signal-shape'; then
    st_ok "the anchored OpenAI shape still refuses a genuine key BY NAME"
  else
    st_bad "the anchored OpenAI shape did not refuse a genuine key: $(cat "$d/d.err")"
  fi
  scan_file "$d/sk-word.txt" 2>"$d/d2.err"
  if [ "$scan_findings" -eq 0 ]; then
    st_ok "the 'ta'+'sk' tail of an ordinary word is NO longer a finding (the anchor bites)"
  else
    st_bad "the anchor did not move the false positive ($scan_findings finding(s)): $(cat "$d/d2.err")"
  fi
  # ...and the UNANCHORED shape must still match that same word, so the anchor
  # is proven load-bearing rather than assumed.
  out="$(grep -nHE -- "$fx_sk_unanchored" "$d/sk-word.txt" 2>/dev/null)"
  if [ -n "$out" ]; then
    st_ok "MUTANT the unanchored shape still matches that word: the anchor is load-bearing"
  else
    st_bad "MUTANT the unanchored shape did not match -- the false positive was never real"
  fi

  # Vacuity: a rule that matches everything must not survive the clean half.
  scan_file "$d/clean.txt" 2>"$d/v.err"
  if [ "$scan_findings" -eq 0 ]; then
    st_ok "a clean file produces NO finding (vacuity)"
  else
    st_bad "a clean file produced $scan_findings finding(s) -- a detector matches everything"
  fi

  # (e) THE GATE'S OWN OUTPUT IS NOT THE TREE (issue #1983). All three directions
  #     are provoked, because an exclusion is exactly the kind of fix that quietly
  #     becomes a blind spot: the declared root is NOT walked, the exclusion is
  #     LOAD-BEARING (with no declaration the same secret IS walked), and a
  #     genuine credential on an ordinary path is still refused BY NAME.
  local fx_repo="$d/repo"
  mkdir -p "$fx_repo/.verify"
  printf '%s\n' 'a clean file on an ordinary path' > "$fx_repo/tracked.txt"
  printf '%s\n' "$fx_sk_p$fx_sk_body" > "$fx_repo/leak.txt"
  printf '%s\n' "$fx_sk_p$fx_sk_body" > "$fx_repo/.verify/attestation.json"
  printf '# a declaration that names no root (the mutant)\n' > "$d/roots-none.txt"

  if st_has "$(generated_roots)" '.verify'; then
    st_ok "the shipped declaration names .verify, the gate's own scratch root"
  else
    st_bad "the shipped declaration does not name .verify: $(generated_roots | tr '\n' ' ')"
  fi

  out="$(text_files "$fx_repo")"
  if st_has "$out" 'tracked.txt' && st_has "$out" 'leak.txt' &&
    ! st_has "$out" '.verify/'; then
    st_ok "text_files prunes the declared generated root and still walks the tree"
  else
    st_bad "text_files did not honour the declaration: $(printf '%s' "$out" | tr '\n' ' ')"
  fi

  out="$(SECRETS_ROOTS_FILE="$d/roots-none.txt" text_files "$fx_repo")"
  if st_has "$out" '.verify/attestation.json'; then
    st_ok "MUTANT with no declared root the gate's OWN artifact IS walked: the exclusion is load-bearing"
  else
    st_bad "MUTANT the undeclared walk still skipped .verify/ -- the exclusion proves nothing"
  fi

  scan_file "$fx_repo/leak.txt" 2>"$d/e.err"
  if [ "$scan_findings" -eq 1 ] && st_has "$(cat "$d/e.err")" 'high-signal-shape'; then
    st_ok "a planted credential on an ordinary path is STILL refused BY NAME (the exclusion is not a blind spot)"
  else
    st_bad "a planted credential outside the generated root was not refused: $(cat "$d/e.err")"
  fi

  # (f) THE EXCLUSION'S PRECONDITION, MEASURED. A root may be pruned only while
  #     git really ignores it AND it holds no tracked file. Both refusals are
  #     driven against a real miniature repository, so neither is narration.
  local fx_git="$d/gitrepo"
  mkdir -p "$fx_git/.verify"
  if git init -q "$fx_git" >/dev/null 2>&1; then
    git -C "$fx_git" config user.email 'check-secrets-selftest@example.invalid'
    git -C "$fx_git" config user.name 'check-secrets self-test'
    printf '.verify/\n' > "$fx_git/.gitignore"
    printf 'x\n' > "$fx_git/keep.txt"
    printf 'x\n' > "$fx_git/.verify/inside.txt"
    git -C "$fx_git" add .gitignore keep.txt >/dev/null 2>&1
    git -C "$fx_git" add -f .verify/inside.txt >/dev/null 2>&1
    git -C "$fx_git" commit -qm selftest >/dev/null 2>&1
    printf '# c\n.verify\n' > "$d/roots-tracked.txt"
    printf '# c\nkeep.txt\n' > "$d/roots-unignored.txt"
    out="$(cd "$fx_git" && SECRETS_ROOTS_FILE="$d/roots-tracked.txt" declared_roots 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ] && st_has "$out" 'holds a TRACKED file'; then
      st_ok "REFUSES a declared root holding a tracked file: the exclusion cannot hide one"
    else
      st_bad "the tracked-file refusal did not fire (rc=$rc): $out"
    fi
    out="$(cd "$fx_git" && SECRETS_ROOTS_FILE="$d/roots-unignored.txt" declared_roots 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ] && st_has "$out" 'NOT gitignored'; then
      st_ok "REFUSES a declared root git does not ignore: the exclusion cannot be a blind spot"
    else
      st_bad "the not-gitignored refusal did not fire (rc=$rc): $out"
    fi
  else
    st_bad "could not build the exclusion-precondition fixture (git init failed)"
  fi

  printf 'check-secrets self-test: %s control(s) passed, %s failed\n' "$st_passed" "$st_failed"
  [ "$st_failed" -eq 0 ]
}

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/check-secrets.sh              the scan (make verify)
  bash scripts/check-secrets.sh --self-test  the provocation alone
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

case "${1:-}" in
  --self-test) self_test; exit $? ;;
  -h|--help) usage; exit 0 ;;
  '') ;;
  *) printf 'check-secrets: unknown argument: %s\n' "$1" >&2; exit 2 ;;
esac

# The provocation runs on EVERY invocation, before the tree is read: a gate that
# cannot fail is a formality, and a regression of either defect above would
# otherwise be as invisible as the defects themselves were.
st_rc=0
self_test || st_rc=$?
if [ "$st_rc" -ne 0 ]; then
  printf 'check-secrets: the self-test did not hold (rc=%s) -- the scan is not trustworthy\n' "$st_rc" >&2
  exit "$st_rc"
fi

scan_rc=0
scan_tree || scan_rc=$?
exit "$scan_rc"
