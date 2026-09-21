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
RE_EC2KEY='-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY( BLOCK)?-----'
RE_AWS='AKIA[0-9A-Z]{16}'
RE_GHP='(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36,}'
RE_GHAPP='github_pat_[A-Za-z0-9_]{20,}'
RE_SK='(sk|sk-ant|sk-proj)-[A-Za-z0-9_\-]{16,}'
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

text_files() {
  find . -type f \( \
    -name '*.md' -o -name '*.sh' -o -name '*.py' -o -name '*.go' -o \
    -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.json' -o \
    -name '*.txt' -o -name '*.tsv' -o -name '*.csv' -o -name 'Makefile' -o \
    -name '.gitmessage' -o -name '.gitignore' -o -name '.gitmodules' -o \
    -name '.gitleaks.toml' -o -name '.cursorrules' \) \
    -not -path './.git/*' \
    -not -path './vendor/*' \
    -not -path './.research/*' \
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

  # Vacuity: a rule that matches everything must not survive the clean half.
  scan_file "$d/clean.txt" 2>"$d/v.err"
  if [ "$scan_findings" -eq 0 ]; then
    st_ok "a clean file produces NO finding (vacuity)"
  else
    st_bad "a clean file produced $scan_findings finding(s) -- a detector matches everything"
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

scan_tree || exit 1
exit 0
