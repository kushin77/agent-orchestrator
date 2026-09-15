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
#     `Token: value` line, a continuation, the reference line itself, or GitHub's
#     own bare auto-close keyword (`Closes #<n>` / `Fixes #<n>` / `Resolves
#     #<n>`). A reference in the subject, or buried in an earlier prose
#     paragraph, is refused by name. (A plain substring test accepts both — that
#     is defect #287 in the lane audit; this gate must not repeat it.)
#     The auto-close keyword is recognised because the landing path composes it:
#     `gh pr merge --squash` over a body whose last paragraph is `Closes #<n>`
#     produces a trailing paragraph holding the keyword, the assistant line and
#     the reference together. By position that IS a trailer paragraph, and
#     GitHub's keyword has no colon, so refusing it made the rule disagree with
#     the tool that produces the artifact. Measured (#835): that one shape was 8
#     of the 12 `commit-ref-outside-the-trailer-block` commits in the landed
#     baseline, and accepting it shrank the baseline from 14 entries to 6. The
#     POSITION rule is unchanged: the block still ends at the first paragraph
#     that is not entirely trailer lines, and the reference is still REQUIRED to
#     be a line of it.
#     A bare `---------` separator line is a boundary *inside* that block, not
#     the end of it: the fleet's landing path (`gh pr merge --squash`, GitHub
#     composing the message) inserts one and appends its own `Co-authored-by:`
#     paragraph after it, so refusing that layout made this gate disagree with
#     the very merge path it audits (issue #522, measured on PR #520 / PR #493).
#     The separator is therefore transparent to the walk-back; prose is not,
#     so a subject-line or prose `Refs` is still refused (#309's complaint).
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
# ENFORCEMENT SURFACES (issue #311 — the follow-up that gives this gate teeth)
#   * PR time — `bash scripts/check-pr-contract.sh --pr <number>` reads the PR
#     body and the PR's base..head range through `gh` and runs every check above.
#     `scripts/merge-gate.sh run` wires it in: when `AO_PR_NUMBER` (or
#     `AO_PR_BODY_FILE` + `AO_PR_RANGE`) is set, the merge gate runs the check
#     as its `pr-contract` signal; when neither is set (an ordinary working
#     checkout) the signal is OMITTED with a note, so `make verify`/`make gate`
#     never false-red for a missing PR body.
#   * Landed history — `bash scripts/check-pr-contract.sh --landed` audits every
#     merged (non-merge) commit reachable from the range (default: HEAD) for the
#     trailing `Refs` trailer. `AI-assistance:` and `Closes #<n>` are PR-BODY
#     obligations and are enforced at PR time only — the squash-merge commit
#     body does not reliably carry them (measured on the repo's own standard
#     commit `8b97ab6`), so a landed audit cannot check them.
#
# LANDED BASELINE (grandfathering is a boundary, not a name list)
#   The enforcement boundary is the commit that landed this gate:
#   `a7e7312991ae24b1047363ff36627060a810fdd8` (PR #308). Every commit strictly
#   before it predates the contract and is grandfathered as a class — measured:
#   all 37 commits that lack the trailer (e.g. `061690c`, the commit the #288
#   review named) are strictly before the boundary, and 0 commits after it lack
#   one. A boundary is provably frozen: a new commit is always a descendant,
#   never an ancestor, so the grandfathering cannot grow silently. The audit
#   also refuses a boundary commit that itself lacks the trailer
#   (`enforcement-gate-missing-trailer`). Override with
#   `--enforcement-gate <sha>` / `AO_PR_ENFORCEMENT_GATE` to prove the audit
#   fails on a pre-boundary commit (see the non-vacuity proof in the PR).
#
# NOT WIRED INTO `make verify` — deliberately, and this is the honest reason:
# in an ordinary working checkout there is no PR body and the range
# `origin/master..HEAD` is empty, so wiring it would turn every legitimate
# `make verify` red for a reason unrelated to the change under test. It is
# invoked with the PR body at PR time, and as its own landed-history command.
# `--selftest` proves every violation is provoked and refused, so the gate is
# not a formality while it waits for those hooks.
#
# Usage:
#   bash scripts/check-pr-contract.sh --body-file <path> [--range <git-range>]
#   bash scripts/check-pr-contract.sh --pr <number>      # PR-time hook (via gh)
#   bash scripts/check-pr-contract.sh --landed           # landed-history audit
#   bash scripts/check-pr-contract.sh --selftest         # build mutants, prove it fails
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo="$root"
body_file="${AO_PR_BODY_FILE:-}"
range="${AO_PR_RANGE:-origin/master..HEAD}"
# The reference line, as a PYTHON regex (it is matched by the helper below).
# Both the repo's colon-less form (`Refs owner/repo#n`) and git's own colon form
# (`Refs: owner/repo#n`) are accepted.
trailer_pattern="${AO_TRAILER_PATTERN:-Refs:?\\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+}"
# The landed audit's enforcement boundary: the commit that landed this gate.
# Everything strictly before it is pre-contract legacy (grandfathered); the
# boundary itself is checked explicitly, everything after must carry the trailer.
default_gate="a7e7312991ae24b1047363ff36627060a810fdd8"
gate="${AO_PR_ENFORCEMENT_GATE:-$default_gate}"
RANGE_SET=0

usage() {
  printf 'usage: %s --body-file <path> [--range <git-range>] | --pr <number> | --landed [--range <git-range>] | --selftest\n' "$0" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --body-file) body_file="${2:-}"; shift 2 ;;
    --range)     range="${2:-}"; RANGE_SET=1; shift 2 ;;
    --repo)      repo="${2:-}"; shift 2 ;;
    --selftest)  SELFTEST=1; shift ;;
    --landed)    LANDED=1; shift ;;
    --pr)        PR_NUMBER="${2:-}"; shift 2 ;;
    --enforcement-gate) gate="${2:-}"; shift 2 ;;
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


# GitHub's auto-close vocabulary as a bare, colon-less line: `Closes #509`,
# `Fixes #7`, `Resolves #12`. The landing path composes exactly this shape --
# `gh pr merge --squash` over a body whose last paragraph is `Closes #<n>` -- and
# by position that paragraph IS a trailer paragraph; GitHub's keyword simply has
# no colon, so before #835 the paragraph was classified `other`, the walk-back
# stopped on it, and the reference line sharing the paragraph was reported
# `commit-ref-outside-the-trailer-block`. Measured: that single shape accounted
# for 8 of the 12 `commit-ref-outside-the-trailer-block` entries in the landed
# baseline; the 4 that remain are a reference line separated from the trailing
# block by prose, which is still refused by name. The POSITION rule is
# unchanged: the block still ends at the first paragraph that is not entirely
# trailer lines, and the reference is still REQUIRED to be a line of it -- a
# paragraph holding only a closing keyword is a missing trailer, never a trailer
# (selftest case 2f).
closing_re = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)[ \t]+#[0-9]+", re.IGNORECASE)


def is_trailer_line(line: str) -> bool:
    """A `Token: value` line, a continuation, the reference, or a close keyword."""
    if ref_re.fullmatch(line.strip()):
        return True
    if line[:1] in (" ", "\t"):
        return True
    if closing_re.fullmatch(line.strip()):
        return True
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_-]*:[ \t]*\S", line))


# A `---------` line is GitHub's paragraph separator in a squash-merge message:
# it sits BETWEEN the lane's trailer paragraph and the `Co-authored-by:`
# paragraph GitHub appends, i.e. inside the trailing block, not before it. It is
# not a `Token: value` line, so a walk-back that only asked `is_trailer_line`
# stopped on it and reported a `Refs` line *above* it as
# `commit-ref-outside-the-trailer-block` (#522). Treat it as transparent: it
# neither terminates the block nor contributes a line to it. This does not
# weaken the position rule — a paragraph that is not entirely trailer lines
# still ends the block, and prose is never a separator.
separator_re = re.compile(r"^-{2,}[ \t]*$")


def is_separator_line(line: str) -> bool:
    """A bare `---------` squash/paragraph separator line."""
    return bool(separator_re.fullmatch(line.strip()))


def paragraph_kind(paragraph: list[str]) -> str:
    """`trailer`, `separator`, or `other` (anything else ends the block)."""
    significant = [line for line in paragraph if not is_separator_line(line)]
    if not significant:
        return "separator"
    if all(is_trailer_line(line) for line in significant):
        return "trailer"
    return "other"


# Walk back over the trailing all-trailer paragraphs; the first paragraph that
# is not entirely trailer lines ends the block, and a separator paragraph is
# stepped over without ending it.
region: list[str] = []
for paragraph in reversed(paragraphs):
    kind = paragraph_kind(paragraph)
    if kind == "other":
        break
    if kind == "trailer":
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

report() { # [label]
  local label="${1:-check-pr-contract}"
  local f
  for f in "${findings[@]}"; do
    printf '  FAIL  %s\n' "$f" >&2
  done
  printf '%s: FAIL (%s finding(s))\n' "$label" "${#findings[@]}" >&2
}

run_checks() { # <body-file> <range>
  findings=()
  check_body "$1"
  check_commits "$2"
  if [ "${#findings[@]}" -gt 0 ]; then
    report "check-pr-contract"
    return 1
  fi
  echo "check-pr-contract: OK — trailer block, AI-assistance, Closes and the pre-existing-red claim are all evidenced"
  return 0
}

# --- landed-history audit: every merged commit carries the ticket trailer -----
# PR-BODY obligations (Closes, AI-assistance, pre-existing red) live at PR time;
# the landed audit enforces the one obligation a commit can carry on its own —
# the trailing `Refs` trailer — over every non-merge commit since the boundary.
landed_audit() { # <range> <gate>
  local rang="${1:-HEAD}" g="${2:-$default_gate}"
  findings=()

  local gate_msg gate_subj gate_finding
  gate_msg="$(git -C "$repo" log -1 --format=%B "$g" 2>/dev/null)"
  gate_subj="$(git -C "$repo" log -1 --format=%s "$g" 2>/dev/null)"
  if [ -z "$gate_msg" ]; then
    echo "check-pr-contract: CANNOT-ASSESS — enforcement gate $g is not reachable from this repository" >&2
    return 2
  fi
  gate_finding="$(commit_finding "$gate_msg" "$gate_subj")"
  [ -z "$gate_finding" ] || findings+=("enforcement-gate-missing-trailer:${g:0:12}")

  local shas sha message subject finding bad=0
  shas="$(git -C "$repo" rev-list --no-merges "$rang" 2>/dev/null)"
  if [ -z "$shas" ]; then
    echo "check-pr-contract: CANNOT-ASSESS — no commits in landed range $rang" >&2
    return 2
  fi
  while IFS= read -r sha; do
    [ -n "$sha" ] || continue
    if [ "$sha" = "$g" ]; then continue; fi
    if git -C "$repo" merge-base --is-ancestor "$sha" "$g" 2>/dev/null; then
      continue  # pre-boundary legacy — grandfathered as a class, never re-checked
    fi
    message="$(git -C "$repo" log -1 --format=%B "$sha" 2>/dev/null)"
    subject="$(git -C "$repo" log -1 --format=%s "$sha" 2>/dev/null)"
    finding="$(commit_finding "$message" "$subject")"
    if [ -n "$finding" ]; then
      findings+=("$finding:${sha:0:12}")
      bad=$((bad + 1))
    fi
  done <<<"$shas"
  if [ "${#findings[@]}" -gt 0 ]; then
    report "check-pr-contract: LANDED"
    return 1
  fi
  echo "check-pr-contract: LANDED OK — every merged commit since the enforcement gate carries the ticket trailer"
  return 0
}

# --- PR-time hook: the body and the range come from GitHub -------------------
pr_check() { # <number>
  local number="$1" tmpdir bodyfile base_name head_oid rang rc
  if ! command -v gh >/dev/null 2>&1; then
    echo "check-pr-contract: CANNOT-ASSESS — gh not found (the PR-time check reads the PR body via gh)" >&2
    return 2
  fi
  tmpdir="/tmp/pr-contract-pr.$(date +%s%N).$$"
  if ! mkdir "$tmpdir" 2>/dev/null; then
    echo "check-pr-contract: CANNOT-ASSESS — cannot create a temp dir at $tmpdir" >&2
    return 2
  fi
  bodyfile="$tmpdir/body.md"
  if ! ( cd "$repo" && gh pr view "$number" --json body --jq '.body' ) > "$bodyfile" 2>/dev/null; then
    rm -rf "$tmpdir"
    echo "check-pr-contract: CANNOT-ASSESS — cannot read the body of PR #$number via gh" >&2
    return 2
  fi
  # `gh pr view` exposes the base NAME and the head OID (there is no baseRefOid
  # field), so the range is `origin/<base>..<head-oid>`, falling back to the
  # local base branch when the remote-tracking ref is not present in the clone.
  base_name="$( ( cd "$repo" && gh pr view "$number" --json baseRefName --jq '.baseRefName' ) 2>/dev/null )"
  head_oid="$( ( cd "$repo" && gh pr view "$number" --json headRefOid --jq '.headRefOid' ) 2>/dev/null )"
  if [ -z "$base_name" ] || [ -z "$head_oid" ]; then
    rm -rf "$tmpdir"
    echo "check-pr-contract: CANNOT-ASSESS — cannot resolve the base/head of PR #$number" >&2
    return 2
  fi
  rang="origin/$base_name..$head_oid"
  if [ -z "$(git -C "$repo" rev-list --no-merges "$rang" 2>/dev/null)" ]; then
    rang="$base_name..$head_oid"
  fi
  if [ -z "$(git -C "$repo" rev-list --no-merges "$rang" 2>/dev/null)" ]; then
    rm -rf "$tmpdir"
    echo "check-pr-contract: CANNOT-ASSESS — no non-merge commits in $rang (is the PR head fetched into this clone?)" >&2
    return 2
  fi
  run_checks "$bodyfile" "$rang"
  rc=$?
  rm -rf "$tmpdir"
  return "$rc"
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

  # 2c. the SQUASH-MERGE layout (#522): the trailer block is followed by a
  #     `---------` separator and GitHub's own co-author paragraph. The separator
  #     is a boundary inside the block, so this commit must be ACCEPTED — before
  #     the fix it was refused as `commit-ref-outside-the-trailer-block`, which
  #     is how two real merges (07b3d5d, 8c0669f) failed `--landed`.
  printf 'e.txt\n' >"$scratch/e.txt"
  git -C "$scratch" add e.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "a squash-merged commit whose trailer block GitHub extended" \
    -m "Refs kushin77/agent-orchestrator#522" \
    -m "---------" \
    -m "Co-authored-by: agent-copilot-522 <agent+copilot-522@agents.invalid>" >/dev/null 2>&1
  e_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(run_checks "$good_body" "${e_sha}^..$e_sha" 2>&1)"
  if [ $? -eq 0 ] && printf '%s' "$out" | grep -qF "check-pr-contract: OK"; then
    printf '  OK    a separator inside the trailer block is stepped over\n'
  else
    printf '  FAIL  a squash separator broke the trailer block\n%s\n' "$out" >&2
    ok=1
  fi

  # 2d. …and the separator must not smuggle PROSE into the block: the identical
  #     layout with the reference in the prose paragraph ABOVE it is still
  #     refused by name. Without this control the fix would have traded #309's
  #     defect (#287: a substring test) for #522's.
  printf 'f.txt\n' >"$scratch/f.txt"
  git -C "$scratch" add f.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "a commit whose prose mentions the ticket, then a separator" \
    -m "The change is tracked as Refs kushin77/agent-orchestrator#522 in prose." \
    -m "---------" \
    -m "Co-authored-by: agent-copilot-522 <agent+copilot-522@agents.invalid>" >/dev/null 2>&1
  f_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(run_checks "$good_body" "${f_sha}^..$f_sha" 2>&1)"
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -qF "commit-ref-outside-the-trailer-block"; then
    printf '  OK    a prose reference below a separator is still refused by name\n'
  else
    printf '  FAIL  a separator let a prose-only reference through\n%s\n' "$out" >&2
    ok=1
  fi

  # 2e. the shape the landing path composes (#835): GitHub's colon-less
  #     auto-close keyword, the assistant line and the reference share ONE
  #     trailing paragraph. It is a trailer paragraph by position, so it must be
  #     ACCEPTED — before the fix the keyword line had no colon, the paragraph
  #     was classified `other`, and the reference inside it was refused.
  printf 'g.txt\n' >"$scratch/g.txt"
  git -C "$scratch" add g.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "a commit whose closing keyword shares the trailer paragraph" \
    -m "Closes #835
AI-assistance: Copilot (Relentless)
Refs kushin77/agent-orchestrator#835" >/dev/null 2>&1
  g_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(run_checks "$good_body" "${g_sha}^..$g_sha" 2>&1)"
  if [ $? -eq 0 ] && printf '%s' "$out" | grep -qF "check-pr-contract: OK"; then
    printf '  OK    a bare `Closes #<n>` shares the trailer paragraph\n'
  else
    printf '  FAIL  a bare `Closes #<n>` broke the trailer paragraph\n%s\n' "$out" >&2
    ok=1
  fi

  # 2f. …and the widened vocabulary is not a second rule: the reference is still
  #     REQUIRED to be a line of the block, so a closing keyword standing alone
  #     is a MISSING trailer, never a passing one.
  printf 'h.txt\n' >"$scratch/h.txt"
  git -C "$scratch" add h.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "a commit whose only ticket evidence is a closing keyword" \
    -m "Closes #835" >/dev/null 2>&1
  h_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(run_checks "$good_body" "${h_sha}^..$h_sha" 2>&1)"
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -qF "commit-missing-ticket-trailer"; then
    printf '  OK    a closing keyword without the reference is still refused\n'
  else
    printf '  FAIL  a closing keyword was accepted as the ticket evidence\n%s\n' "$out" >&2
    ok=1
  fi

  # 2g. a genuinely `other` trailing paragraph still ends the block: a keyword
  #     line is a trailer line, but a line that is neither a keyword, a
  #     `Token: value` line, nor a continuation is not — widening the vocabulary
  #     must not let prose in.
  printf 'i.txt\n' >"$scratch/i.txt"
  git -C "$scratch" add i.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "a commit whose trailing paragraph is not a trailer paragraph" \
    -m "Refs kushin77/agent-orchestrator#835" \
    -m "Closes #835 and then a line of prose" >/dev/null 2>&1
  i_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(run_checks "$good_body" "${i_sha}^..$i_sha" 2>&1)"
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -qF "commit-ref-outside-the-trailer-block"; then
    printf '  OK    a prose paragraph below the trailer is still refused by name\n'
  else
    printf '  FAIL  prose below the trailer was accepted\n%s\n' "$out" >&2
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

  # --- landed audit non-vacuity ----------------------------------------------
  # The enforcement gate is the well-trailed commit `a`; everything after it
  # must carry the trailer, everything before it (the base commit) is
  # grandfathered legacy.
  b_sha="$(git -C "$scratch" rev-list -1 --grep 'ref only in the subject' HEAD 2>/dev/null)"
  c_sha="$(git -C "$scratch" rev-list -1 --grep 'no ticket reference' HEAD 2>/dev/null)"

  # 9. post-gate commits missing the trailer are refused by name
  out="$(landed_audit "$base..HEAD" "$a_sha" 2>&1)"
  if [ $? -ne 0 ] \
     && printf '%s' "$out" | grep -qF "commit-missing-ticket-trailer:${c_sha:0:12}" \
     && printf '%s' "$out" | grep -qF "commit-ref-only-in-subject:${b_sha:0:12}"; then
    printf '  OK    the landed audit refuses post-gate commits missing the trailer, by name\n'
  else
    printf '  FAIL  the landed audit did not refuse post-gate bad commits\n%s\n' "$out" >&2
    ok=1
  fi

  # 10. a clean post-gate history (gate + grandfathered legacy only) passes
  out="$(landed_audit "$base..$a_sha" "$a_sha" 2>&1)"
  if [ $? -eq 0 ] && printf '%s' "$out" | grep -qF "LANDED OK"; then
    printf '  OK    the landed audit passes a clean post-gate history\n'
  else
    printf '  FAIL  the landed audit did not pass a clean post-gate history\n%s\n' "$out" >&2
    ok=1
  fi

  # 11. a boundary commit that itself lacks the trailer is a finding (the
  #     baseline cannot silently include a non-compliant boundary)
  out="$(landed_audit "$base..HEAD" "$base" 2>&1)"
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -qF "enforcement-gate-missing-trailer"; then
    printf '  OK    a trailer-less enforcement gate is itself refused\n'
  else
    printf '  FAIL  a trailer-less enforcement gate went undetected\n%s\n' "$out" >&2
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

if [ -n "${LANDED:-}" ]; then
  if [ "$RANGE_SET" -eq 0 ]; then
    landed_audit "HEAD" "$gate"
  else
    landed_audit "$range" "$gate"
  fi
  exit $?
fi

if [ -n "${PR_NUMBER:-}" ]; then
  pr_check "$PR_NUMBER"
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
