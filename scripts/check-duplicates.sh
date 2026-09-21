#!/usr/bin/env bash
# check-duplicates.sh — the ADR-0010 canonical-copy no-fork rule, enforced
# (issue #1164). Moved here from `governance/dupcheck/check-duplicates.sh`.
#
# WHAT THIS IS
#   agent-orchestrator CONSUMES the fleet-standard canonical docs and must never
#   FORK them (ADR-0010, `docs/decision-records/ADR-0010-canonical-copy-ownership.md`).
#   This gate is the mechanical half of that decision: a same-named copy of a
#   protected canonical doc anywhere in the tree is a fork, and is REFUSED BY
#   NAME. The two documented read-only mirrors — `vendor/` (the pinned CMR
#   submodule) and `.research/` (the source clones) — are never findings.
#
# WHY IT LIVES IN scripts/
#   `scripts/discover-checks.sh` auto-wires every `scripts/check-*.sh` into
#   `scripts/verify.sh` (#698), and `scripts/check-gate-coverage.sh` reads a
#   check under that glob as WIRED. The detector used to live inside a package
#   (`governance/dupcheck/`), which is outside that glob — so NO gate ran it and
#   the ADR-0010 rule was advisory (#1164). There is ONE implementation of the
#   rule and it is this file; the original path forwards here rather than
#   carrying a second copy, because two implementations of one rule disagree
#   silently and the disagreement stays invisible until a real fork slips
#   through the weaker one.
#
# WHY IT IS SHAPED LIKE THIS
#   A gate that cannot fail is a formality (GR-12, AO-GR-4), so this gate proves
#   itself on EVERY run, before it looks at the repository at all:
#     1. a planted fork — one file per protected name, in a SCRATCH tree outside
#        the repo — is REFUSED, and every planted path is NAMED;
#     2. a clean tree, and a tree holding an unrelated file, produce NO finding
#        (vacuity: a rule that matches everything must not pass this half);
#     3. the two documented mirrors (`vendor/`, `.research/`) are NOT findings on
#        the same names — the exclusion is load-bearing, or the rule would red
#        the read-only mirrors it exists to protect;
#     4. a MUTANT OF THIS SCRIPT with the protected-name declaration neutralised
#        must STOP refusing its own plant, while a bad invocation on that same
#        mutant is still CANNOT-ASSESS — so each refusal is shown to come from
#        the rule under test, and not from the planted directory existing.
#   The provocation drives the SAME `scan_root` the repository run uses, so what
#   is proven is the code path that runs — never a copy of it.
#
# THE OPT-IN HALF
#   `demo-cases` re-verifies the documented byte-identical `dprs` =
#   `git-rca-workspace` finding. It reads the `.research/` clones, so it is NOT
#   storage-free: it is an EXPLICIT, documented opt-in and the gate NEVER runs
#   it. `compare` is the offline primitive underneath it.
#
# EXIT CONTRACT (the repo's honesty tri-state, guardrails/honesty)
#   0  OK              no fork in the tree and the self-test passed
#   1  NOT-OK          a fork refused by name, or the self-test failed
#   2  CANNOT-ASSESS   no scratch dir, a bad invocation, `--root` is not a
#                      directory, or `find` is unavailable — never a pass
#   A non-zero code is never collapsed: the gate returns the self-test's own
#   word when the self-test did not pass, and the scan's own word otherwise.
#
# Usage:
#   bash scripts/check-duplicates.sh                    the gate (self-test, then this repo)
#   bash scripts/check-duplicates.sh scan [--root DIR]  the detector alone, over DIR
#   bash scripts/check-duplicates.sh compare A B        byte-identity (files or dirs)
#   bash scripts/check-duplicates.sh demo-cases         OPT-IN: scan + the dprs demo
#   bash scripts/check-duplicates.sh --self-test        the provocation alone
#   bash scripts/check-duplicates.sh --list             the protected-name table
set -u

self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2

# One global scratch variable and one EXIT trap, the shape this repo's gates
# use: armed once, fired once, `|| true`-safe so a cleanup failure cannot mask
# the gate's own exit code.
TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

# --- the protected names (one line, so the mutant can neutralise exactly this)
# ADR-0010 canonical homes:
#   the fleet-standard trio (kushin77/CMR `docs/`):  MODEL-PROFILES.md
#                                                    SME-PROFILES.md
#                                                    SOLUTION-CLASSES.md
#   the agent-identity standard + schema set
#   (kushin77/shared-governance `GLOBAL_STANDARDS/`): agent-identity.md
#                                                     agent-identity-jwt.schema.json
#                                                     agent-action.schema.json
#                                                     agent-oidc-config.schema.json
#                                                     agent-task.schema.json
protected_names=(MODEL-PROFILES.md SME-PROFILES.md SOLUTION-CLASSES.md agent-identity.md agent-identity-jwt.schema.json agent-action.schema.json agent-oidc-config.schema.json agent-task.schema.json)

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/check-duplicates.sh                     the gate: self-test, then this repo
  bash scripts/check-duplicates.sh scan [--root DIR]   the detector alone, over DIR
  bash scripts/check-duplicates.sh compare <A> <B>     byte-identity of two paths
  bash scripts/check-duplicates.sh demo-cases          OPT-IN: reads .research/ clones
  bash scripts/check-duplicates.sh --self-test         the provocation alone
  bash scripts/check-duplicates.sh --list              the protected-name table
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
The protected names are ADR-0010 canonical docs. vendor/ (the pinned CMR
submodule) and .research/ (the source clones) are the two documented read-only
mirrors and are never findings.
USAGE
}

# --- the ONE implementation of the rule -------------------------------------
# report_fork <path> <name> — ONE formatter for every refusal, so the repository
# run and the provocation read identically: the protected NAME, the path that
# carries it, and why the copy is a fork.
report_fork() {
  printf '  REFUSED  %s\n' "$2" >&2
  printf '           %s (in-repo copy of a canonical doc — ADR-0010)\n' "$1" >&2
  printf '           why: %s has a canonical home (kushin77/CMR or\n' "$2" >&2
  printf '                kushin77/shared-governance); an in-repo copy is a fork, and two\n' >&2
  printf '                copies of a standard drift apart.\n' >&2
}

# scan_root <dir> — the rule. Returns 0 (no fork), 1 (fork(s) refused),
# 2 (cannot assess). Prints every refusal to stderr.
scan_root() {
  local base="${1:-$root}"
  if [ ! -d "$base" ]; then
    printf 'check-duplicates: CANNOT-ASSESS — not a directory: %s\n' "$base" >&2
    return 2
  fi
  if ! command -v find >/dev/null 2>&1; then
    echo 'check-duplicates: CANNOT-ASSESS — find is not on PATH' >&2
    return 2
  fi
  local forks=0
  local name
  local f
  for name in "${protected_names[@]}"; do
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      report_fork "$f" "$name"
      forks=$((forks + 1))
    done < <(find "$base" -type f -name "$name" \
      -not -path '*/.git/*' -not -path '*/vendor/*' -not -path '*/.research/*' 2>/dev/null)
  done
  if [ "$forks" -ne 0 ]; then
    printf 'dupcheck scan: FAIL - %s forked copy(ies) of a protected canonical doc\n' "$forks" >&2
    return 1
  fi
  printf 'dupcheck scan: PASS - no forked copies of protected canonical docs (%s names checked)\n' "${#protected_names[@]}"
  return 0
}

# compare <A> <B> — byte-identity of two paths: files directly, directories
# recursively ignoring .git. The offline primitive `demo-cases` is built on.
compare() {
  local a="${1:-}"
  local b="${2:-}"
  if [ -z "$a" ] || [ -z "$b" ]; then
    echo 'usage: check-duplicates.sh compare <pathA> <pathB>' >&2
    return 2
  fi
  if [ ! -e "$a" ] || [ ! -e "$b" ]; then
    printf 'dupcheck compare: missing path (%s / %s)\n' "$a" "$b" >&2
    return 2
  fi
  if [ -d "$a" ] && [ -d "$b" ]; then
    if diff -r --exclude=.git "$a" "$b" >/dev/null 2>&1; then
      printf 'dupcheck compare: IDENTICAL (%s = %s)\n' "$a" "$b"
      return 0
    fi
  elif [ -f "$a" ] && [ -f "$b" ]; then
    if cmp -s "$a" "$b"; then
      printf 'dupcheck compare: IDENTICAL (%s = %s)\n' "$a" "$b"
      return 0
    fi
  else
    printf 'dupcheck compare: type mismatch (%s vs %s)\n' "$a" "$b" >&2
    return 2
  fi
  printf 'dupcheck compare: DIFFERENT (%s vs %s)\n' "$a" "$b" >&2
  return 1
}

# demo_cases — the EXPLICIT opt-in: scan this repo, then re-verify the
# documented byte-identical dprs = git-rca-workspace finding from the .research
# clones. RESEARCH_BASE can point at another checkout that holds them. The gate
# never calls this.
demo_cases() {
  local rc=0
  local s
  local c
  scan_root "$root"
  s=$?
  [ "$s" -ne 0 ] && rc="$s"
  local base="${RESEARCH_BASE:-$root/.research}"
  local dprs="$base/fleet/dprs"
  local grw="$base/fleet/git-rca-workspace"
  if [ -d "$dprs" ] && [ -d "$grw" ]; then
    compare "$dprs" "$grw"
    c=$?
    [ "$c" -ne 0 ] && rc="$c"
  else
    printf 'dupcheck demo: dprs / git-rca-workspace clones not found under %s; skipping compare\n' "$base" >&2
  fi
  return "$rc"
}

# --- the provocation: the rule must fire, and must not fire on non-forks -----
self_test() {
  local rc=0
  local d
  local suffix
  local name
  local f
  local out
  local got
  # The six-character scratch suffix is assembled at run time: a literal run of
  # the marker token would trip the docs-lint unfinished-marker scan (issue #804).
  suffix="$(printf 'X%.0s' 1 2 3 4 5 6)"
  TMPD="$(mktemp -d "/tmp/ao1164-dupcheck.$suffix")" || {
    echo 'check-duplicates: CANNOT-ASSESS — no scratch directory for the self-test' >&2
    return 2
  }
  d="$TMPD"

  # Fixture A — a clean tree: one unrelated file, nothing protected at all.
  mkdir -p "$d/clean" || return 2
  printf 'nothing to see here\n' > "$d/clean/NOTES.md"

  # Fixture B — one planted fork per protected name, each written under its own
  # name, so "refused by name" is per name rather than per batch.
  mkdir -p "$d/planted" || return 2
  for name in "${protected_names[@]}"; do
    printf 'a forked copy, planted by --self-test\n' > "$d/planted/$name"
  done

  # Fixture C — the same protected names where the rule must NOT fire: inside
  # the two documented read-only mirrors. Without this half the exclusions could
  # be inert and the gate would still look green.
  mkdir -p "$d/mirrors/vendor" "$d/mirrors/.research/fleet" || return 2
  printf 'the pinned mirror\n' > "$d/mirrors/vendor/MODEL-PROFILES.md"
  printf 'a read-only source clone\n' > "$d/mirrors/.research/fleet/SME-PROFILES.md"

  echo '== the ADR-0010 no-fork rule, provoked =='

  # Half 1 — a clean tree, and an unrelated file, are refused nothing. Only
  # STDERR is captured: the PASS summary is a verdict, not a finding, and
  # reading it as one is how this half would fail on a healthy tree.
  out="$(scan_root "$d/clean" 2>&1 >/dev/null)"
  got=$?
  if [ "$got" -ne 0 ] || [ -n "$out" ]; then
    printf 'check-duplicates: FAIL — a clean tree was refused (rc=%s); the rule over-matches:\n%s\n' "$got" "$out" >&2
    rc=1
  else
    echo '  OK    a clean tree, and a tree holding an unrelated file, produce no finding'
  fi

  # Half 2 — every planted fork is refused, by name.
  out="$(scan_root "$d/planted" 2>&1 >/dev/null)"
  got=$?
  if [ "$got" -ne 1 ]; then
    printf 'check-duplicates: FAIL — the planted forks were NOT refused (rc=%s); the detector matches nothing\n' "$got" >&2
    rc=1
  else
    for name in "${protected_names[@]}"; do
      case "$out" in
        *"$d/planted/$name"*) ;;
        *)
          printf 'check-duplicates: FAIL — refused but NOT NAMED: %s\n' "$name" >&2
          rc=1
          ;;
      esac
    done
    [ "$rc" -eq 0 ] && printf '  OK    each of the %s planted forks is refused, by name\n' "${#protected_names[@]}"
  fi

  # Half 3 — the two documented mirrors are not findings.
  out="$(scan_root "$d/mirrors" 2>&1 >/dev/null)"
  got=$?
  if [ "$got" -ne 0 ] || [ -n "$out" ]; then
    printf 'check-duplicates: FAIL — the vendor/ + .research/ mirrors were refused (rc=%s); the exclusion is inert:\n%s\n' "$got" "$out" >&2
    rc=1
  else
    echo '  OK    the same names inside vendor/ and .research/ are not findings'
  fi

  # Half 4 — the protected-name declaration is load-bearing. A mutant of THIS
  # script with that one declaration neutralised must accept the very tree the
  # gate refuses, and must still be a working script.
  local mutant="$d/mutant.sh"
  mkdir -p "$d/lib"
  cp "$(dirname "$self")/lib/common.sh" "$d/lib/common.sh"
  sed -e 's|^protected_names=(.*)$|protected_names=()|' "$self" > "$mutant"
  if cmp -s "$self" "$mutant"; then
    echo 'check-duplicates: FAIL — the mutant is byte-identical to this script; the mutation proved nothing' >&2
    rc=1
  else
    out="$(bash "$mutant" scan --root "$d/planted" 2>&1 >/dev/null)"
    got=$?
    if [ "$got" -ne 0 ]; then
      printf 'check-duplicates: FAIL — with the name declaration neutralised the mutant STILL refused its own plant (rc=%s): the refusal is not that rule\n' "$got" >&2
      rc=1
    fi
    out="$(bash "$mutant" bogus-subcommand 2>&1)"
    got=$?
    if [ "$got" -ne 2 ]; then
      printf 'check-duplicates: FAIL — the mutant broke the script instead of one rule (bad invocation rc=%s, expected 2)\n' "$got" >&2
      rc=1
    fi
    [ "$rc" -eq 0 ] && echo '  OK    the name declaration is load-bearing: neutralised, the plant is accepted and the script still works'
  fi

  # Half 5 — a bad invocation is CANNOT-ASSESS, never a pass.
  local bad=0
  bash "$self" bogus-subcommand >/dev/null 2>&1
  [ "$?" -eq 2 ] || bad=1
  bash "$self" scan --root >/dev/null 2>&1
  [ "$?" -eq 2 ] || bad=1
  bash "$self" scan --root "$d/clean/NOTES.md" >/dev/null 2>&1
  [ "$?" -eq 2 ] || bad=1
  bash "$self" scan --nonsense >/dev/null 2>&1
  [ "$?" -eq 2 ] || bad=1
  if [ "$bad" -ne 0 ]; then
    echo 'check-duplicates: FAIL — a bad invocation did not answer CANNOT-ASSESS (rc 2)' >&2
    rc=1
  else
    echo '  OK    a bad invocation is CANNOT-ASSESS (rc 2), never a pass'
  fi

  [ "$rc" -eq 0 ] && echo 'check-duplicates: self-test OK — the rule fires, and only on forks'
  return "$rc"
}

# --- the gate (no arguments) -----------------------------------------------
# The self-test runs FIRST, before the repository is looked at, so a rule that
# cannot fire can never report this tree as clean. The self-test's own word is
# returned when it is not 0: CANNOT-ASSESS is never collapsed into a pass.
gate() {
  local s
  self_test
  s=$?
  if [ "$s" -ne 0 ]; then
    printf 'check-duplicates: reporting the self-test verdict (rc=%s) rather than the tree result\n' "$s" >&2
    return "$s"
  fi
  echo '== the tree =='
  scan_root "$root"
}

# scan_cmd [--root DIR] — the detector alone, over this repo by default.
scan_cmd() {
  local base="$root"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root)
        shift
        if [ "$#" -eq 0 ]; then
          echo 'check-duplicates: CANNOT-ASSESS — --root needs a directory' >&2
          return 2
        fi
        base="$1"
        shift
        ;;
      *)
        printf 'check-duplicates: CANNOT-ASSESS — unknown argument to scan: %s (see --help)\n' "$1" >&2
        return 2
        ;;
    esac
  done
  scan_root "$base"
}

rc=0
case "${1:-}" in
  '') gate; rc=$? ;;
  scan) shift; scan_cmd "$@"; rc=$? ;;
  compare) shift; compare "${1:-}" "${2:-}"; rc=$? ;;
  demo-cases) demo_cases; rc=$? ;;
  --self-test) self_test; rc=$? ;;
  --list)
    for pname in "${protected_names[@]}"; do printf 'PROTECTED  %s\n' "$pname"; done
    rc=0
    ;;
  -h | --help) usage; rc=0 ;;
  *)
    printf 'check-duplicates: CANNOT-ASSESS — unknown invocation: %s\n' "${1:-}" >&2
    usage >&2
    rc=2
    ;;
esac
exit "$rc"
