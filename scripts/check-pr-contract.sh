#!/usr/bin/env bash
# check-pr-contract.sh — the PR/commit contract gate (issue #288).
#
# WHY THIS EXISTS
#   Two obligations of the merge contract were scaffolding-free and gate-free,
#   so they were advisory in practice and were missed on real merges:
#
#     * AGENTS.md golden rule 1 — every commit a session authors carries
#       `Refs <owner>/<repo>#<n>` in its TRAILING TRAILER BLOCK. Measured on four
#       recent merges: 3 of 4 commits carried no trailer at all; two carried the
#       reference only in the subject line, which a substring test accepts.
#     * docs/GOVERNANCE.md — every AI-originated PR carries an
#       `AI-assistance: <runtime> (<mode>)` note; 2 of those 4 PRs did not.
#   And a claimed *pre-existing* red had no evidence standard: #236 merged with
#   an unproven assertion, while #267/#268 reproduced theirs on a clean checkout
#   of the base commit. This gate makes the reproduction mandatory once the claim
#   is made.
#
# WHAT IT CHECKS  (body file + commit range)
#   * every non-merge commit in the range carries the ticket reference in its
#     TRAILING TRAILER BLOCK — the last paragraph(s) whose every line is a
#     `Token: value` line, a continuation, or the reference line itself. A
#     reference in the subject, or buried in an earlier prose paragraph, is
#     refused by name. (A plain substring test accepts both — that is defect
#     #287 in the lane audit; this gate must not repeat it.)
#   * the PR body declares `Closes #<n>`;
#   * the PR body carries a filled-in `AI-assistance:` line (a placeholder such
#     as `<runtime>` is refused — that is how the template distinguishes itself
#     from a filled-in body);
#   * the PR body's `## Pre-existing red` section is either an explicit `None` or
#     a reproduction: a `Reproduce:` line naming a command in backticks followed
#     by a fenced output block.
#
# WHY NOT `git interpret-trailers --parse` ALONE: git only recognises the COLON
# form (`Refs: owner/repo#n`), while this repo's convention — and the exemplary
# commit cited in #287/#288, `8b97ab6` — writes the COLON-LESS form
# (`Refs kushin77/agent-orchestrator#263`). A strict interpret-trailers rule
# would refuse the repo's own standard, so the trailing block is parsed here: it
# is still a position rule (the last trailer paragraphs), never a substring test.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. A missing body file is
# CANNOT-ASSESS, never a pass: an empty run is not a green (GR-12).
#
# NOT WIRED INTO `make verify` — deliberately, and this is the honest reason:
# in an ordinary working checkout there is no PR body and the range
# `origin/master..HEAD` is empty, so wiring it would turn every legitimate
# `make verify` red for a reason unrelated to the change under test. It is
# invoked with the PR body at PR time. `--selftest` proves the gate can fail, so
# it is not a formality while it waits for that hook.
#
# Usage:
#   bash scripts/check-pr-contract.sh --body-file <path> [--range <git-range>]
#   bash scripts/check-pr-contract.sh --selftest        # build mutants, prove it fails
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo="$root"
body_file="${AO_PR_BODY_FILE:-}"
range="${AO_PR_RANGE:-origin/master..HEAD}"
# The reference line, as a PYTHON regex (it is matched by the helper below).
# Both the repo's colon-less form (`Refs owner/repo#n`) and git's own colon form
# (`Refs: owner/repo#n`) are accepted.
trailer_pattern="${AO_TRAILER_PATTERN:-Refs:?\\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+}"

usage() {
  printf 'usage: %s --body-file <path> [--range <git-range>] | --selftest\n' "$0" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --body-file) body_file="${2:-}"; shift 2 ;;
    --range)     range="${2:-}"; shift 2 ;;
    --repo)      repo="${2:-}"; shift 2 ;;
    --selftest)  SELFTEST=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) printf 'check-pr-contract: unknown argument %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "check-pr-contract: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
if [ ! -d "$repo" ]; then
  echo "check-pr-contract: CANNOT-ASSESS — $repo is not a directory" >&2
  exit 2
fi

# --- the checks -------------------------------------------------------------
# Each check appends one named finding to the `findings` array.
declare -a findings=()

# Is the ticket reference in this message's TRAILING TRAILER BLOCK? Prints the
# named finding, or nothing when the rule holds. The message and subject arrive
# through the environment so a commit body can never be re-parsed by the shell.
commit_finding() { # <message> <subject>
  MESSAGE="$1" SUBJECT="$2" REF_RE="$trailer_pattern" python3 - <<'PY'
import os
import re
import sys

message = os.environ["MESSAGE"]
subject = os.environ["SUBJECT"]
ref_re = re.compile(os.environ["REF_RE"])

lines = message.splitlines()
paragraphs: list[list[str]] = []
current: list[str] = []
for line in lines:
    if line.strip():
        current.append(line)
    elif current:
        paragraphs.append(current)
        current = []
if current:
    paragraphs.append(current)


def is_trailer_line(line: str) -> bool:
    """A `Token: value` line, a continuation line, or the reference line."""
    if ref_re.fullmatch(line.strip()):
        return True
    if line[:1] in (" ", "\t"):
        return True
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_-]*:[ \t]*\S", line))


# Walk back over the trailing all-trailer paragraphs; the first paragraph that
# is not entirely trailer lines ends the block.
region: list[str] = []
for paragraph in reversed(paragraphs):
    if not all(is_trailer_line(line) for line in paragraph):
        break
    region = paragraph + region

if any(ref_re.fullmatch(line.strip()) for line in region):
    sys.exit(0)
if ref_re.search(subject):
    print("commit-ref-only-in-subject")
    sys.exit(0)
if any(ref_re.search(line) for line in lines):
    print("commit-ref-outside-the-trailer-block")
    sys.exit(0)
print("commit-missing-ticket-trailer")
PY
}

check_commits() { # <range>
  local rang="$1" sha message subject finding shas
  shas="$(git -C "$repo" rev-list --no-merges "$rang" 2>/dev/null)"
  if [ -z "$shas" ]; then
    findings+=("no-commits-in-range:$rang")
    return
  fi
  while IFS= read -r sha; do
    [ -n "$sha" ] || continue
    message="$(git -C "$repo" log -1 --format=%B "$sha" 2>/dev/null)"
    subject="$(git -C "$repo" log -1 --format=%s "$sha" 2>/dev/null)"
    finding="$(commit_finding "$message" "$subject")"
    [ -z "$finding" ] || findings+=("$finding:${sha:0:12}")
  done <<<"$shas"
}

check_body() { # <body-file>
  local body="$1"
  if ! grep -qE '^Closes[[:space:]]+#[0-9]+' "$body"; then
    findings+=("pr-body-missing-closes")
  fi
  # A filled-in assistant line names a runtime and a parenthesised mode. The
  # template's `<runtime> (<model>/<mode>)` placeholder is refused, so an
  # untouched template cannot pass as a declared one.
  if ! grep -qE '^AI-assistance:[[:space:]]*[^<[:space:]][^(]*\([^)]*\)[[:space:]]*$' "$body"; then
    findings+=("pr-body-missing-ai-assistance")
  fi

  # The `## Pre-existing red` section, comments removed (the template carries its
  # example inside an HTML comment so an unfilled section reads as empty, not as
  # a reproduction).
  local section
  section="$(python3 - "$body" <<'PY'
import re, sys

text = open(sys.argv[1], encoding="utf-8").read()
text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
match = re.search(r"(?im)^##+[ \t]*Pre-existing red[ \t]*$(.*?)(?=^##+[ \t]|\Z)", text, flags=re.S | re.M)
print(match.group(1).strip() if match else "")
PY
)"
  if [ -z "$section" ]; then
    findings+=("pr-body-missing-pre-existing-red-section")
  elif printf '%s' "$section" | grep -qiE '^None\b'; then
    : # explicitly declared as none — nothing to reproduce
  elif ! printf '%s' "$section" | grep -qE '^Reproduce:[[:space:]]*`[^`]+`'; then
    findings+=("pr-body-unreproduced-pre-existing-red")
  elif ! printf '%s' "$section" | grep -qE '^```'; then
    findings+=("pr-body-unreproduced-pre-existing-red")
  fi
}

report() {
  local f
  for f in "${findings[@]}"; do
    printf '  FAIL  %s\n' "$f" >&2
  done
  printf 'check-pr-contract: FAIL (%s finding(s))\n' "${#findings[@]}" >&2
}

run_checks() { # <body-file> <range>
  findings=()
  check_body "$1"
  check_commits "$2"
  if [ "${#findings[@]}" -gt 0 ]; then
    report
    return 1
  fi
  echo "check-pr-contract: OK — trailer block, AI-assistance, Closes and the pre-existing-red claim are all evidenced"
  return 0
}

# --- selftest: the gate must be able to fail --------------------------------
# A gate whose pass and fail paths collapse is a formality (GR-12). This builds a
# scratch repository and a set of bodies, provokes every violation by name, and
# requires the gate to report it.
selftest() {
  local work scratch ok=0
  # A unique scratch dir WITHOUT a trailing run of `X`: the repo's docs-lint
  # scans for unfinished markers (and a `mktemp` X-suffix is one), so this uses
  # the convention the other gates use — an explicit /tmp name, mkdir refusing
  # loudly if it already exists rather than silently reusing another run's tree.
  work="/tmp/pr-contract.$(date +%s%N).$$"
  if ! mkdir "$work" 2>/dev/null; then
    echo "check-pr-contract: CANNOT-ASSESS — cannot create a scratch dir at $work" >&2
    exit 2
  fi
  scratch="$work/repo"
  mkdir -p "$scratch"
  # `run_checks` reads the global `repo`; point it at the scratch repository so
  # the in-process checks and the subprocess invocations agree on the subject.
  repo="$scratch"
  git -C "$scratch" init -q >/dev/null 2>&1
  git -C "$scratch" config user.name "gate" >/dev/null 2>&1
  git -C "$scratch" config user.email "gate@example.invalid" >/dev/null 2>&1
  printf 'base\n' >"$scratch/base.txt"
  git -C "$scratch" add base.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -qm "base" >/dev/null 2>&1
  base="$(git -C "$scratch" rev-parse HEAD)"

  commit_in() { # <file> <trailer-line> <subject>
    printf '%s\n' "$1" >"$scratch/$1"
    git -C "$scratch" add "$1" >/dev/null 2>&1
    local message="$3"
    if [ -n "$2" ]; then
      message="$3"$'\n\n'"$2"
    fi
    git -C "$scratch" -c commit.gpgsign=false commit -qm "$message" >/dev/null 2>&1
  }

  good_body="$work/good.md"
  cat >"$good_body" <<'MD'
## Closes

Closes #288

## Evidence

```
$ make verify
verify: PASS (30 of 30 checks)
```

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

None
MD

  unreproduced_body="$work/unreproduced.md"
  cat >"$unreproduced_body" <<'MD'
## Closes

Closes #288

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

The fleet suite was already red before my change.
MD

  template_body="$work/template.md"
  cat >"$template_body" <<'MD'
## Closes

Closes #<n>

## AI-assistance

AI-assistance: <runtime> (<model>/<mode>)

## Pre-existing red

None
MD

  expect() { # <label> <want-rc> <body-file> [<range>]
    local label="$1" want="$2" body="$3" rang="${4:-$base..HEAD}" got out
    out="$(run_checks "$body" "$rang" 2>&1)"
    got=$?
    if [ "$got" -eq "$want" ]; then
      printf '  OK    %s (rc=%s)\n' "$label" "$got"
    else
      printf '  FAIL  %s — expected rc=%s, got rc=%s\n%s\n' "$label" "$want" "$got" "$out" >&2
      ok=1
    fi
  }

  # 1. a real trailer + a filled body is accepted (range limited to the good
  #    commit, so the deliberately-bad commits below cannot leak into it)
  commit_in a.txt "Refs kushin77/agent-orchestrator#288" "a properly trailed commit"
  a_sha="$(git -C "$scratch" rev-parse HEAD)"
  expect "a real trailer and a filled body pass" 0 "$good_body" "$base..$a_sha"

  # 2. the reference in the SUBJECT only (the measured defect: audit clean, no trailer)
  commit_in b.txt "" "Refs kushin77/agent-orchestrator#288: ref only in the subject"
  out="$(run_checks "$good_body" "$base..HEAD" 2>&1)"
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -qF "commit-ref-only-in-subject"; then
    printf '  OK    a subject-only reference is refused by name\n'
  else
    printf '  FAIL  a subject-only reference went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # 2b. the reference buried in an earlier PROSE paragraph (a substring test
  #     accepts this too — the rule is position, not presence)
  printf 'tracked as Refs kushin77/agent-orchestrator#288 in prose\n' >"$scratch/d.txt"
  git -C "$scratch" add d.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "a commit whose body merely mentions the ticket" \
    -m "The change is tracked as Refs kushin77/agent-orchestrator#288 in prose." \
    -m "Co-authored-by: gate <gate@example.invalid>" >/dev/null 2>&1
  out="$(run_checks "$good_body" "$base..HEAD" 2>&1)"
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -qF "commit-ref-outside-the-trailer-block"; then
    printf '  OK    a reference buried in prose is refused by name\n'
  else
    printf '  FAIL  a prose-only reference went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # 3. no reference at all
  commit_in c.txt "" "a commit with no ticket reference"
  out="$(run_checks "$good_body" "$base..HEAD" 2>&1)"
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -qF "commit-missing-ticket-trailer"; then
    printf '  OK    a missing ticket trailer is refused by name\n'
  else
    printf '  FAIL  a missing ticket trailer went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # 4. the AI-assistance placeholder must not pass as a declaration
  expect "an unfilled AI-assistance placeholder is refused" 1 "$template_body"

  # 5. a claimed pre-existing red with no reproduction is refused
  expect "an unreproduced pre-existing red is refused" 1 "$unreproduced_body"

  # 6. a claimed pre-existing red WITH a reproduction is accepted
  reproduced_body="$work/reproduced.md"
  {
    printf '## Closes\n\nCloses #288\n\n## AI-assistance\n\n'
    printf 'AI-assistance: Copilot (Relentless, flash/LOW)\n\n'
    printf '## Pre-existing red\n\nReproduce: `bash scripts/check-secrets.sh`\n\n'
    printf '```\ncheck-secrets: FAIL (1 finding)\n```\n'
  } >"$reproduced_body"
  expect "a reproduced pre-existing red is accepted" 0 "$reproduced_body" "$base..$a_sha"

  # 7. a missing body file is CANNOT-ASSESS through the real entry point — an
  #    empty run is never a green.
  bash "$0" --repo "$scratch" --body-file "$work/absent.md" >/dev/null 2>&1
  if [ $? -eq 2 ]; then
    printf '  OK    a missing body file exits 2 (CANNOT-ASSESS)\n'
  else
    printf '  FAIL  a missing body file did not exit 2\n' >&2
    ok=1
  fi

  # 8. a range with no commits is CANNOT-ASSESS, not a vacuous pass
  bash "$0" --repo "$scratch" --body-file "$good_body" --range "$base..$base" >/dev/null 2>&1
  if [ $? -eq 2 ]; then
    printf '  OK    an empty commit range exits 2 (CANNOT-ASSESS)\n'
  else
    printf '  FAIL  an empty commit range did not exit 2\n' >&2
    ok=1
  fi

  rm -rf "$work"
  if [ "$ok" -ne 0 ]; then
    echo "check-pr-contract: SELFTEST FAIL — the gate cannot detect every violation it defines" >&2
    return 1
  fi
  echo "check-pr-contract: SELFTEST OK — every declared violation is provoked and refused"
  return 0
}

if [ -n "${SELFTEST:-}" ]; then
  selftest
  exit $?
fi

if [ -z "$body_file" ]; then
  echo "check-pr-contract: CANNOT-ASSESS — no PR body file (--body-file or AO_PR_BODY_FILE); a missing body is not a pass" >&2
  exit 2
fi
if [ ! -f "$body_file" ]; then
  echo "check-pr-contract: CANNOT-ASSESS — PR body file $body_file does not exist" >&2
  exit 2
fi
if [ -z "$(git -C "$repo" rev-list --no-merges "$range" 2>/dev/null)" ]; then
  echo "check-pr-contract: CANNOT-ASSESS — no non-merge commits in $range (nothing checked is not a pass)" >&2
  exit 2
fi

run_checks "$body_file" "$range"
exit $?
