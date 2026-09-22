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
#   bash scripts/check-duplicates.sh header-values      security-header VALUE drift (#1890)
#   bash scripts/check-duplicates.sh compare A B        byte-identity (files or dirs)
#   bash scripts/check-duplicates.sh demo-cases         OPT-IN: scan + the dprs demo
#   bash scripts/check-duplicates.sh --self-test        the provocation alone
#   bash scripts/check-duplicates.sh --list             the protected-name table
#
# ---knowledge---
# module_id: scripts.check-duplicates
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, offline-hermetic, named-refusal, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#698", "#1164"]
# do_not_duplicate: null
# ---knowledge---
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
  bash scripts/check-duplicates.sh header-values       security-header VALUE drift across sibling repos (#1890)
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

# --- the security-header VALUE contract (issue #1890) -----------------------
# A name-based fork detector cannot see this class of drift: each site below
# is a DIFFERENT file with a DIFFERENT name (nginx conf, a Cloudflare Worker, a
# deploy script, an Express middleware...) that all restate the SAME header
# value. The canonical home is shared-frontend/auth/src/server.mjs (declared
# SOURCE OF TRUTH, e2e/tests/matchers.ts:156). Sibling repos are sibling
# checkouts next to this one (SIBLINGS_ROOT, default: this repo's parent dir),
# and a missing sibling is a SKIP, not a failure — this gate must stay green on
# a checkout that only has agent-orchestrator (#1890 review note).
#
# header_sites <header> — one line per known copy site for that header, as
# "repo/relative/path:grep-pattern". Kept as a function (not a static array)
# so each header's site list is easy to read next to the header it belongs to.
header_sites() {
  case "$1" in
    x-frame-options)
      cat <<'SITES'
shared-frontend/auth/src/server.mjs:X-Frame-Options
shared-services/infra/nginx/sites/elevatediq-https.conf:X-Frame-Options
shared-services/scripts/deploy-cloudflare-security-headers-worker.js:x-frame-options
capital-underwriting/apps/server/src/middleware/security.ts:X-Frame-Options
SITES
      ;;
    permissions-policy)
      cat <<'SITES'
shared-frontend/auth/src/server.mjs:Permissions-Policy
shared-services/scripts/deploy-cloudflare-security-headers-worker.js:permissions-policy
capital-underwriting/apps/server/src/middleware/security.ts:Permissions-Policy
SITES
      ;;
    strict-transport-security)
      cat <<'SITES'
shared-services/scripts/deploy-cloudflare-security-headers-worker.js:strict-transport-security
capital-underwriting/apps/server/src/middleware/security.ts:Strict-Transport-Security
SITES
      ;;
  esac
}

# header_value <file> <pattern> — the quoted literal on the first matching
# line, whichever quote style that layer uses ('...', "...", or a bare
# nginx add_header token up to the trailing `always`/`;`).
# header_value <file> <name> — the effective value, whatever the layer's
# syntax: a JS `.setHeader("Name", "val" + "val2")` / `.set("name", "val")`
# call (value may be a multi-line string concatenation — captured up to the
# first unescaped `)`), or an nginx `add_header Name VAL always;` line.
header_value() {
  local file="$1" name="$2"
  [ -f "$file" ] || return 1
  python3 - "$file" "$name" <<'PY'
import re, sys
file, name = sys.argv[1], sys.argv[2]
text = open(file, encoding="utf-8", errors="ignore").read()
esc = re.escape(name)
m = re.search(r'(?:setHeader|\.set)\(\s*["\']' + esc + r'["\']\s*,\s*(.*?)\);',
              text, re.IGNORECASE | re.DOTALL)
if m:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'', m.group(1))
    print("".join(a or b for a, b in parts).replace('\\"', '"'))
    sys.exit(0)
m = re.search(r'add_header\s+' + esc + r'\s+(.*?);', text, re.IGNORECASE)
if m:
    val = m.group(1).strip()
    val = re.sub(r'\s+always\s*$', '', val, flags=re.IGNORECASE)
    val = val.strip('"\'')
    print(val)
    sys.exit(0)
sys.exit(1)
PY
}

# header_values_cmd — compare the actual header VALUE across every known site,
# per header. Sites in a sibling repo that is not checked out are a named SKIP.
# The negative control is content-security-policy: it is NOT in header_sites
# above (CSP legitimately differs per layer/host — enforcing vs report-only,
# different domains), so it is never compared and never flagged, by
# construction. `header-values --list-negative-control` names it for a test to
# assert against.
# sibling_default_branch <repo_dir> — origin/HEAD's branch, else main, else
# master. Empty output = cannot resolve (repo present but no recognisable
# default branch) — callers treat that as a SKIP, not a fork/fail.
sibling_default_branch() {
  local repo="$1" b
  b="$(git -C "$repo" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"
  if [ -n "$b" ]; then
    printf '%s\n' "${b#origin/}"
    return 0
  fi
  for b in main master; do
    if git -C "$repo" show-ref --verify --quiet "refs/heads/$b" 2>/dev/null; then
      printf '%s\n' "$b"
      return 0
    fi
  done
  return 1
}

header_values_cmd() {
  local siblings="${SIBLINGS_ROOT:-$(dirname "$root")}"
  local headers=(x-frame-options permissions-policy strict-transport-security)
  local mismatches=0
  local h line reponame relpath pattern val branch tmpf
  local -a vals
  local -a labels
  # Read from each sibling's DEFAULT BRANCH (git show), not its working tree:
  # a fleet checkout can legitimately have a sibling repo sitting on someone
  # else's in-progress feature branch, and this gate must judge the committed
  # contract, not transient WIP noise on an unrelated branch (#1890 review).
  TMPD="${TMPD:-$(mktemp -d 2>/dev/null)}"
  for h in "${headers[@]}"; do
    vals=()
    labels=()
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      reponame="${line%%/*}"
      relpath="${line#*/}"
      relpath="${relpath%%:*}"
      pattern="${line#*:}"
      if [ ! -d "${siblings}/${reponame}" ]; then
        printf '  SKIP     %s — sibling repo not checked out under %s\n' "$line" "$siblings" >&2
        continue
      fi
      branch="$(sibling_default_branch "${siblings}/${reponame}")" || {
        printf '  SKIP     %s — could not resolve a default branch\n' "$line" >&2
        continue
      }
      tmpf="${TMPD}/$(echo "$reponame-$relpath" | tr '/' '_')"
      if ! git -C "${siblings}/${reponame}" show "${branch}:${relpath}" >"$tmpf" 2>/dev/null; then
        printf '  SKIP     %s — not present on %s@%s\n' "$line" "$reponame" "$branch" >&2
        continue
      fi
      val="$(header_value "$tmpf" "$pattern")"
      if [ -z "$val" ]; then
        printf '  SKIP     %s — header not found in this file (legitimately absent at this layer)\n' "$line" >&2
        continue
      fi
      vals+=("$val")
      labels+=("${reponame}@${branch}:${relpath}")
    done < <(header_sites "$h")
    if [ "${#vals[@]}" -lt 2 ]; then
      printf '  n/a      %s — fewer than 2 live copies to compare\n' "$h" >&2
      continue
    fi
    local first="${vals[0]}"
    local mismatch_here=0
    local i
    for i in "${!vals[@]}"; do
      if [ "${vals[$i]}" != "$first" ]; then
        mismatch_here=1
      fi
    done
    if [ "$mismatch_here" -ne 0 ]; then
      printf '  REFUSED  %s — copies disagree on the value:\n' "$h" >&2
      for i in "${!vals[@]}"; do
        printf '           %-70s %s\n' "${labels[$i]}" "${vals[$i]}" >&2
      done
      mismatches=$((mismatches + 1))
    else
      printf '  OK       %s — %s live cop%s agree: %s\n' "$h" "${#vals[@]}" \
        "$([ "${#vals[@]}" -eq 1 ] && echo y || echo ies)" "$first"
    fi
  done
  if [ "$mismatches" -ne 0 ]; then
    printf 'dupcheck header-values: FAIL - %s header(s) disagree across copy sites\n' "$mismatches" >&2
    return 1
  fi
  printf 'dupcheck header-values: PASS (or SKIP where a sibling repo is absent)\n'
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

  # Half 6 — the header-value contract's negative control (#1890): a header
  # that legitimately differs per layer (content-security-policy: enforcing on
  # one host, Report-Only on another, different directives per app) must never
  # be compared, by construction — it is not in header_sites() for any header
  # name, so header_values_cmd can never mention it.
  local hv_headers=(x-frame-options permissions-policy strict-transport-security)
  local hv_h
  local control_leaked=0
  for hv_h in "${hv_headers[@]}"; do
    if header_sites "$hv_h" | grep -qi 'content-security-policy'; then
      control_leaked=1
    fi
  done
  if [ "$control_leaked" -ne 0 ]; then
    echo 'check-duplicates: FAIL — content-security-policy (the negative control) is in a compared header set' >&2
    rc=1
  else
    echo '  OK    content-security-policy (legitimately differs per layer) is never compared — the negative control holds'
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
  local scan_rc=$?
  echo '== the security-header value contract (#1890) =='
  header_values_cmd
  local hv_rc=$?
  if [ "$scan_rc" -ne 0 ]; then
    return "$scan_rc"
  fi
  return "$hv_rc"
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
  header-values) header_values_cmd; rc=$? ;;
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
