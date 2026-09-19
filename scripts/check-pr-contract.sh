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
#   * the PR body's `## Merge order` section declares `Gate-changing: no` or
#     `Gate-changing: yes — <paths>` (issue #1054), and the declaration is
#     cross-checked against the diff: `no` while the range touches
#     `scripts/verify.sh`/`scripts/gate.sh`/`scripts/merge-gate.sh`/
#     `scripts/check-*.sh`/`scripts/gate-coverage-baseline.txt` (the glob list
#     lives in `scripts/lib/gate-paths.txt`, read from THIS repo's checkout —
#     not the range under test — so the boundary is stable) is refused, and so
#     is `yes` while the range touches none of them. This is a PR-BODY
#     obligation, exactly like `Closes`/`AI-assistance`: it is enforced at PR
#     time only, never by `--landed` (a squash-merge commit carries no PR body
#     to re-judge, so the enforcement-gate grandfathering does not need to say
#     anything about it — there is nothing in landed history for it to check).
#     The diff this declaration is judged against is the lane's OWN diff: the
#     range is resolved against its merge base before `git diff` sees it, so a
#     base branch that moved on after the lane forked cannot vote (#1147).
#
# WHY THE RANGE IS RESOLVED AGAINST THE MERGE BASE (issue #1147)
#   `git rev-list A..B` and `git diff A..B` are two different things wearing the
#   same syntax: the first is the COMMIT SET `reachable(B) − reachable(A)`, the
#   second is a TWO-TREE diff of `tree(A)` against `tree(B)`. They agree only
#   while A is an ancestor of B — which is exactly what stops being true the
#   moment the base branch moves on after a lane forks. The PR-time range was
#   `origin/<base>..<head-oid>`, so as soon as master advanced, the
#   `Gate-changing:` cross-check (the one check here that diffs) saw every file
#   MASTER had changed since the fork alongside the lane's own, and refused a
#   correct `Gate-changing: no` with `pr-body-gate-changing-mismatch-no` for
#   master's commits. Measured on PR #1125 (head `84e0bba`, base `c9040b9`):
#   `--landed --range HEAD^..HEAD` was OK and only that cross-check misfired —
#   and it misfires for EVERY lane cut from an older master.
#   The set this gate means is the PR's own range, which git spells
#   `merge-base(A, B)..B`. The derivation resolves that once, in `--pr` (and in
#   `diff_range_for` for any range handed to the diffing check, so a
#   caller-supplied `--range`/`AO_PR_RANGE` cannot reintroduce the defect).
#   The commit-set half is deliberately left alone: `rev-list --no-merges
#   A..B` already yields the lane's own commits, so the trailer check never
#   misfired — only the diffing consumer did.
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
#     body through `gh` and the PR's own range — the merge base of its base and
#     its head, through to the head (`#1147`; see the merge-base note above) —
#     and runs every check above.
#     `scripts/merge-gate.sh run` wires it in: when `AO_PR_NUMBER` (or
#     `AO_PR_BODY_FILE` + `AO_PR_RANGE`) is set, the merge gate runs the check
#     as its `pr-contract` signal; when neither is set (an ordinary working
#     checkout) the signal is OMITTED with a note, so `make verify`/`make gate`
#     never false-red for a missing PR body.
#   * Landed history — `bash scripts/check-pr-contract.sh --landed` audits every
#     merged (non-merge) commit reachable from the range (default: HEAD) for the
#     trailing `Refs` trailer, and books the findings it measures against the
#     recorded-legacy record (see "THE RECORDED LEGACY" below). `AI-assistance:`
#     and `Closes #<n>` are PR-BODY
#     obligations and are enforced at PR time only — the squash-merge commit
#     body does not reliably carry them (measured on the repo's own standard
#     commit `8b97ab6`), so a landed audit cannot check them.
#
# LANDED BASELINE (grandfathering is a boundary, not a name list)
#   The enforcement boundary is the commit that landed this gate:
#   `a7e7312991ae24b1047363ff36627060a810fdd8` (PR #308). Every commit strictly
#   before it predates the contract and is grandfathered as a class — measured:
#   all 37 commits that lack the trailer (e.g. `061690c`, the commit the #288
#   review named) are strictly before the boundary. What is strictly AFTER it is
#   not clean, and this file asserted otherwise as a MEASUREMENT for a year:
#   re-measured 2026-09-19 on a pristine `origin/master`, **39** post-boundary
#   commits lack the trailer (39 of 39 findings after the boundary, measured in
#   `--range` mode). The claim is restated here to match what this check
#   measures rather than what it was written to say. A boundary is provably
#   frozen: a new commit is always a descendant, never an ancestor, so the
#   grandfathering cannot grow silently. The audit
#   also refuses a boundary commit that itself lacks the trailer
#   (`enforcement-gate-missing-trailer`). Override with
#   `--enforcement-gate <sha>` / `AO_PR_ENFORCEMENT_GATE` to prove the audit
#   fails on a pre-boundary commit (see the non-vacuity proof in the PR).
#
# THE RECORDED LEGACY, AND THE TWO CONTRACTS OF `--landed` (issue #1402)
#   Post-boundary residue has a record: `governance/isolation/landed-baseline.json`
#   (issues #287/#836/#1249), the file `scripts/check-isolation-landed.sh` — which
#   IS in `make verify` — is green against. So one rule had two answers, and one
#   of them was a permanent, unactionable 39: residue on a protected branch whose
#   message can never be corrected (history is never rewritten, hard floor),
#   reported as a failure forever by a surface nobody can act on. This check now
#   reads that same record — **through that module's own loader**
#   (`governance/isolation/landed.py: load_baseline`), so the record has one home
#   and one parser — and `--landed` has two named contracts:
#
#     * `--landed` (no range named) — the REPOSITORY VERDICT. It measures the
#       same findings as before, books each against the record, prints the
#       recorded ones as `NOTE  recorded legacy` (named on every run, never
#       hidden — the same treatment `governance/isolation/cli.py enforce` gives
#       them), and FAILS by name only for findings that are NOT recorded. This
#       is the surface #1402 is about: rc 0 with an honest number instead of a
#       permanent 39.
#     * `--landed --range <r>` — the PREDICATE RE-CHECK, verbatim: the findings
#       for `<r>` with the record deliberately NOT applied, rc 1 when there are
#       any. That is not an oversight, it is the contract every programmatic
#       consumer of this check already depends on, and each of them names a
#       range: `governance/isolation/trailer.py`'s `run_landed`/`classify_commit`
#       and `scripts/check-session-isolation.sh`. `landed.assess` derives
#       "recorded legacy" FROM the findings it parses, so a predicate that
#       pre-filtered them would book all 39 as `quarantine-entry-stale` —
#       turn `check-isolation-landed.sh` red, and redden `make verify` for every
#       lane. Pre-filtering that path is therefore not a tightening of the rule,
#       it is a break of the reader that enforces it. `--selftest` asserts the
#       seam in both directions, so it is a checked property rather than a
#       comment (GR-29). The discriminant is the argv `--range`, never
#       `AO_PR_RANGE` (which this mode has always ignored) — so the seam cannot
#       be flipped by an ambient variable.
#
#   Grandfathering here only ever REMOVES a finding this check actually measured
#   for the commit the record names, matched on the commit alone (the record's
#   own semantics — one recorded commit already carries a code the predicate has
#   since changed), so this reader can never invent a pass for a commit it did
#   not check. An entry that no longer matches anything is not this reader's
#   business: the record's integrity (stale entries, entries outside the assessed
#   range) is enforced by the isolation surface in `make verify`, which owns it.
#   A missing or malformed record is NOT-OK (rc 1), never a skip — otherwise
#   deleting the file would be a way to switch this check off, the rule
#   `governance/isolation/landed.py` applies to its own baseline.
#
# NOT WIRED INTO `make verify` — deliberately, and this is the honest reason:
# in an ordinary working checkout there is no PR body and the range
# `origin/master..HEAD` is empty, so wiring it would turn every legitimate
# `make verify` red for a reason unrelated to the change under test. It is
# invoked with the PR body at PR time, and as its own landed-history command.
# `--selftest` proves every violation is provoked and refused, so the gate is
# not a formality while it waits for those hooks.
#
# THE INERTNESS IS RECORDED, NOT ASSUMED (#1396)
#   "Not wired" is true, and incomplete. `pr-contract` is disabled BY NAME in
#   `scripts/check-denylist.txt`, so `make verify` never runs this check — which
#   means the scoped PR-context passthrough that #1341 added to
#   `scripts/verify.sh` is UNEXERCISED by the composite: the wiring reads as
#   done and does nothing, the same class of defect as the one it caused. The
#   resolution taken here is the issue's second option — RECORD it as
#   unexercised rather than wire it in — and for a measured reason: a check that
#   runs in the composite with no PR context must record a SKIP, which means a
#   `scripts/skip-budget.json` entry, and both that file and the composite's
#   check set belong to other lanes (AGENTS.md rule 2 forbids two lanes sharing
#   a file). So `scripts/check-denylist.txt` carries the record
#   (`unexercised-seam: #1341`) and `--selftest` asserts it in BOTH directions,
#   refusing by name: `denylist-inertness-undeclared` when a denylisted seam
#   loses its record, `denylist-inertness-record-stale` when the seam becomes
#   live and the record outlives it. A record in a comment rots (GR-29: a
#   doc-only rule is advisory); this one is a checked property.
#
# PRECEDENCE — argv > DECLARED env > ambient hint (#1396)
#   An explicit `--pr`/`--body-file`/`--range` is a STATEMENT of what to judge.
#   An exported `PR_NUMBER`/`_PR_NUMBER` is a HINT about which PR the caller is
#   on. This check used to resolve `PR_NUMBER="${_PR_NUMBER:-${PR_NUMBER:-}}"`
#   BEFORE parsing argv, so the hint outranked the statement — and it was
#   measured, not reasoned about: `scripts/verify.sh` exported `PR_NUMBER` into
#   the whole composite environment, and `scripts/check-landing.sh`'s
#   attribution fixture — which calls this check with an explicit
#   `--body-file`/`--range` against a stub `gh`, precisely so that it cannot
#   read the host's GitHub state — was hijacked into the LIVE PR path and
#   answered CANNOT-ASSESS, reddening the gate of record for the whole tree
#   (#1344, #1396). The principle is general: a control must not read the
#   machine it is supposed to be independent of.
#   The order is now argv > the DECLARED env interface (`AO_PR_NUMBER`, the name
#   `infra/cloudbuild/verify.yaml` exports and `scripts/merge-gate.sh` reads) >
#   the ambient `_PR_NUMBER`/`PR_NUMBER` hint — and the hint is consulted ONLY
#   when no argv named a subject at all, so #1341's no-flags passthrough in
#   `scripts/verify.sh` still reaches `pr_check` exactly as before while an
#   ambient name can no longer rewrite the argv a caller wrote on purpose. A
#   `--pr` with no number is refused by name. `--selftest` provokes the hijack
#   under BOTH ambient spellings and requires the mutant that restores the
#   ambient-first resolution to be caught.
#
# Usage:
#   bash scripts/check-pr-contract.sh --body-file <path> [--range <git-range>]
#   bash scripts/check-pr-contract.sh --pr <number>      # PR-time hook (via gh)
#   bash scripts/check-pr-contract.sh --landed           # landed verdict (record applied)
#   bash scripts/check-pr-contract.sh --landed --range <r>  # predicate re-check, verbatim
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
# The recorded-legacy record (#1402): the same file, read through the same loader,
# that `governance/isolation/landed.py` owns and `scripts/check-isolation-landed.sh`
# enforces in `make verify`. Resolved beside THIS script's own checkout, exactly as
# that module resolves it beside itself, so the record has one home and one parser,
# and a change to the repo under test cannot move either.
baseline_default="${AO_PR_CONTRACT_BASELINE:-$root/governance/isolation/landed-baseline.json}"
baseline_file="$baseline_default"
RANGE_SET=0
# Did ARGV itself name the subject to judge? See "PRECEDENCE" in the header
# (#1396): `--pr`, `--body-file` and `--range` all state what to judge, and
# argv outranks an ambient PR number, which is only a hint.
ARGV_CONTEXT=0
PR_ARG_SET=0

usage() {
  printf 'usage: %s --body-file <path> [--range <git-range>] | --pr <number> | --landed [--range <git-range>] | --selftest\n' "$0" >&2
  printf '       --landed alone is the repository verdict (the recorded legacy is applied);\n' >&2
  printf '       --landed --range <r> is the predicate re-check for <r>, verbatim (#1402).\n' >&2
}

# The `## Classification` block (issue #1254 step 5 / #1328). Vocabularies are
# read live from the sources the tagging gate already owns; the scratch path
# below exists only so `--selftest` can provoke `pr-class-below-surface`
# against a fixture ladder without touching the real one.
surfaces_yaml_override=""

while [ $# -gt 0 ]; do
  case "$1" in
    --body-file) body_file="${2:-}"; ARGV_CONTEXT=1; shift 2 ;;
    --range)     range="${2:-}"; RANGE_SET=1; ARGV_CONTEXT=1; shift 2 ;;
    --repo)      repo="${2:-}"; shift 2 ;;
    --selftest)  SELFTEST=1; shift ;;
    --self-test) SELFTEST=1; shift ;;
    --landed)    LANDED=1; shift ;;
    --pr)        PR_NUMBER="${2:-}"; PR_ARG_SET=1; ARGV_CONTEXT=1; shift 2 ;;
    --enforcement-gate) gate="${2:-}"; shift 2 ;;
    --surfaces-yaml) surfaces_yaml_override="${2:-}"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    *) printf 'check-pr-contract: unknown argument %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

if [ "$PR_ARG_SET" -eq 1 ] && [ -z "${PR_NUMBER:-}" ]; then
  printf 'check-pr-contract: --pr needs a number (an empty --pr is not a context)\n' >&2
  usage
  exit 2
fi

# --- BEGIN #1396 precedence block (mutated by --selftest; keep the markers) --
# The order is stated once, in the header: argv > declared env > ambient hint.
if [ "$PR_ARG_SET" -eq 1 ]; then
  : # `--pr` named the PR itself; there is nothing to resolve.
elif [ "$ARGV_CONTEXT" -eq 0 ]; then
  # No argv named a subject: the DECLARED interface first, then the ambient
  # hint. This is #1341's no-flags path -- `scripts/verify.sh` hands this check
  # a bare `PR_NUMBER` and `infra/cloudbuild/verify.yaml` exports
  # `AO_PR_NUMBER` -- so it must keep reaching `pr_check`.
  PR_NUMBER="${AO_PR_NUMBER:-${_PR_NUMBER:-${PR_NUMBER:-}}}"
else
  # argv named the subject. An ambient PR number is a hint about which PR the
  # caller is on, never a statement of what to judge, so it is refused here
  # rather than allowed to rewrite the argv the caller wrote on purpose.
  PR_NUMBER=""
fi
# --- END #1396 precedence block ---------------------------------------------

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
declare -a class_findings=()

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

# `git diff A..B` is a TWO-TREE diff, not "the commits in the range": unlike
# `git rev-list A..B` (`reachable(B) − reachable(A)`) it compares `tree(A)` with
# `tree(B)`, and the two agree only while A is an ancestor of B. A base branch
# that moved on after the lane forked breaks exactly that, so the diffing check
# must never be handed a raw two-dot range. Full rationale (and the measurement)
# in the header, "WHY THE RANGE IS RESOLVED AGAINST THE MERGE BASE".
merge_base_range() { # <base-rev> <head-rev> — "<merge-base>..<head>", or rc 1
  local mb
  mb="$(git -C "$repo" merge-base "$1" "$2" 2>/dev/null)"
  [ -n "$mb" ] || return 1
  printf '%s..%s' "$mb" "$2"
}

diff_range_for() { # <range> — the same range resolved against its merge base
  local r="$1" a b mb
  case "$r" in
    *...*) printf '%s' "$r"; return 0 ;;  # `git diff A...B` IS the merge-base diff
    *..*)  a="${r%%..*}"; b="${r#*..}" ;;
    *)     printf '%s' "$r"; return 0 ;;  # not a range — nothing to resolve
  esac
  [ -n "$a" ] && [ -n "$b" ] || return 1
  mb="$(git -C "$repo" merge-base "$a" "$b" 2>/dev/null)"
  [ -n "$mb" ] || return 1
  printf '%s..%s' "$mb" "$b"
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

# The Gate-changing declaration, cross-checked against the diff (issue #1054).
# Reads the glob list from THIS script's own repo (`$root`), never from the
# repo/range under test — the selftest's scratch repo carries no
# `scripts/lib/gate-paths.txt`, and a real audit should not let a PR that
# deletes the path file also delete the cross-check.
check_gate_changing() { # <body-file> <range>
  local body="$1" rang="$2" line declared touched matched paths_file diff_range
  line="$(grep -E '^Gate-changing:' "$body" | head -n1)"
  declared=""
  if [ -n "$line" ]; then
    case "$line" in
      Gate-changing:*'<'*) declared="" ;;
      *) case "$(printf '%s' "$line" | sed -E 's/^Gate-changing:[[:space:]]*//')" in
           [Nn]o*)  declared="no" ;;
           [Yy]es*) declared="yes" ;;
           *)       declared="" ;;
         esac
      ;;
    esac
  fi
  if [ -z "$declared" ]; then
    findings+=("pr-body-missing-gate-changing")
    return
  fi

  # The declaration is judged against the LANE'S OWN diff, so the range is
  # resolved against its merge base first: handed a raw `base..head` whose base
  # has moved, `git diff` would put the base's own commits in the lane's diff and
  # refuse a correct `no` (#1147). When the range cannot be resolved against a
  # merge base the declaration cannot be assessed, and an unassessable
  # declaration is refused by name rather than quietly compared against a
  # two-tree diff.
  if ! diff_range="$(diff_range_for "$rang")"; then
    findings+=("pr-body-gate-changing-unassessable:$rang")
    return
  fi
  touched="$(git -C "$repo" diff --name-only "$diff_range" 2>/dev/null)"
  paths_file="$root/scripts/lib/gate-paths.txt"
  matched="0"
  if [ -n "$touched" ]; then
    matched="$(GATE_PATHS_FILE="$paths_file" TOUCHED="$touched" python3 - <<'PY'
import fnmatch
import os

paths_file = os.environ["GATE_PATHS_FILE"]
touched = [line for line in os.environ["TOUCHED"].splitlines() if line]
globs = []
try:
    with open(paths_file, encoding="utf-8") as fh:
        for raw in fh:
            g = raw.strip()
            if not g or g.startswith("#"):
                continue
            globs.append(g)
except OSError:
    pass

hit = any(fnmatch.fnmatch(f, g) for f in touched for g in globs)
print(1 if hit else 0)
PY
)"
  fi

  if [ "$declared" = "no" ] && [ "$matched" = "1" ]; then
    findings+=("pr-body-gate-changing-mismatch-no")
  elif [ "$declared" = "yes" ] && [ "$matched" = "0" ]; then
    findings+=("pr-body-gate-changing-mismatch-yes")
  fi
}

# --- Classification block (issue #1254 step 5 / #1328) ---------------------
# WARN-ONLY by default (issue #1328): findings are printed but do not flip the
# exit code unless AO_PR_CONTRACT_ENFORCE=1. The flip is a later PR, once every
# open PR carries the block. Every vocabulary is read live from the source the
# tagging gate already owns -- never a second copy of the ladder (ADR-0010 /
# ADR-0031): `governance/conformance/policy.yaml` for `class`,
# `governance/tagging/taxonomy.yaml` for `posture`/`lifecycle`/`pillar`,
# `docs/PYTHON-PATTERNS.md` / `docs/SHELL-PATTERNS.md` / `AGENTS.md` / decision
# records for `pattern`.
check_classification() { # <body-file> <diff-range> [<head-branch>] [<surfaces-yaml>]
  local body="$1" diff_range="$2" head_branch="${3:-}" surfaces_yaml="${4:-}"
  local out line
  out="$(
    AO_ROOT="$root" AO_REPO="$repo" AO_BODY="$body" AO_DIFF_RANGE="$diff_range" \
    AO_HEAD_BRANCH="$head_branch" \
    AO_SURFACES_YAML="${surfaces_yaml:-$root/governance/conformance/surfaces.yaml}" \
    python3 - <<'PY'
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["AO_ROOT"])
repo = Path(os.environ["AO_REPO"])
body_path = os.environ["AO_BODY"]
diff_range = os.environ.get("AO_DIFF_RANGE", "")
head_branch = os.environ.get("AO_HEAD_BRANCH", "") or ""
surfaces_yaml = Path(os.environ["AO_SURFACES_YAML"])


def emit(code, detail=""):
    print("%s:%s" % (code, detail) if detail else code)


try:
    text = Path(body_path).read_text(encoding="utf-8")
except OSError as exc:
    emit("pr-context-missing", "body unreadable: %s" % exc)
    sys.exit(0)

no_comments = re.sub(r"<!--.*?-->", "", text, flags=re.S)
match = re.search(
    r"(?im)^##+[ \t]*Classification[ \t]*$(.*?)(?=^##+[ \t]|\Z)",
    no_comments,
    flags=re.S | re.M,
)
if not match or not match.group(1).strip():
    emit("pr-classification-missing")
    sys.exit(0)

fields = {}
for raw_line in match.group(1).splitlines():
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    kv = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", stripped)
    if not kv:
        continue
    key = kv.group(1).lower()
    value = re.split(r"\s+#", kv.group(2).strip(), maxsplit=1)[0].strip()
    fields[key] = value


def filled(value):
    return bool(value) and "<" not in value


try:
    import yaml
except ImportError as exc:
    emit("pr-context-missing", "PyYAML unavailable: %s" % exc)
    sys.exit(0)


def load_yaml(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


policy = load_yaml(root / "governance/conformance/policy.yaml")
ladder = list(policy.get("ladder") or [])

sys.path.insert(0, str(root / "governance/tagging"))
try:
    import model as M  # noqa: E402

    taxonomy = M.load_taxonomy(root / "governance/tagging/taxonomy.yaml")
    posture_vocab = list(taxonomy.dimensions["posture"].values)
    posture_exclusive = list(taxonomy.dimensions["posture"].mutually_exclusive or [])
    lifecycle_vocab = list(taxonomy.dimensions["lifecycle"].values)
    pillar_vocab = list(taxonomy.dimensions["pillar"].values)
except Exception as exc:  # the authority itself is unreadable -- CANNOT-ASSESS
    emit("pr-context-missing", "tagging authority unreadable: %s" % exc)
    sys.exit(0)

findings = []

cls = fields.get("class", "")
if not filled(cls) or cls not in ladder:
    findings.append(("pr-class-unknown", cls or "(missing)"))

posture_raw = fields.get("posture", "")
if not filled(posture_raw):
    findings.append(("pr-posture-unknown", "(missing)"))
else:
    postures = [p.strip() for p in posture_raw.split(",") if p.strip()]
    bad = [p for p in postures if p not in posture_vocab]
    if bad or not postures:
        findings.append(("pr-posture-unknown", ",".join(bad) or posture_raw))
    for pair in posture_exclusive:
        if all(v in postures for v in pair):
            findings.append(("pr-posture-contradiction", "+".join(pair)))

lifecycle = fields.get("lifecycle", "")
if not filled(lifecycle) or lifecycle not in lifecycle_vocab:
    findings.append(("pr-lifecycle-unknown", lifecycle or "(missing)"))

pillar = fields.get("pillar", "")
if not filled(pillar) or pillar not in pillar_vocab:
    findings.append(("pr-pillar-unknown", pillar or "(missing)"))

pattern = fields.get("pattern", "")


def pattern_resolvable(value):
    if value == "none":
        return True
    fam = re.match(r"^(PP|SP|GR)-([0-9]+)$", value)
    if fam:
        family, num = fam.groups()
        doc = {
            "PP": root / "docs/PYTHON-PATTERNS.md",
            "SP": root / "docs/SHELL-PATTERNS.md",
            "GR": root / "AGENTS.md",
        }[family]
        try:
            doc_text = doc.read_text(encoding="utf-8")
        except OSError:
            return False
        return bool(re.search(r"(?m)^#+.*\b%s-%s\b" % (family, num), doc_text))
    adr = re.match(r"^ADR-([0-9]{4})$", value)
    if adr:
        decisions = root / "docs/decision-records"
        try:
            return any(
                p.name.startswith("ADR-%s-" % adr.group(1)) or p.name == "ADR-%s.md" % adr.group(1)
                for p in decisions.glob("ADR-*.md")
            )
        except OSError:
            return False
    return False


if not filled(pattern) or not pattern_resolvable(pattern):
    findings.append(("pr-pattern-unresolvable", pattern or "(missing)"))

lane = fields.get("lane", "")
lane_ok = False
if filled(lane):
    if lane == "direct":
        lane_ok = not re.match(r"^issue-[0-9]+", head_branch)
    else:
        lm = re.match(r"^issue-([0-9]+)$", lane)
        if lm:
            lane_ok = bool(head_branch) and (
                head_branch == lane or head_branch.startswith(lane + "-")
            )
if not lane_ok:
    findings.append(
        ("pr-lane-mismatch", "lane=%s head=%s" % (lane or "(missing)", head_branch or "(unknown)"))
    )

if filled(cls) and cls in ladder and diff_range:
    spolicy = None
    try:
        sys.path.insert(0, str(root / "governance/conformance"))
        import surfaces as SF  # noqa: E402

        spolicy = SF.load_surface_policy(surfaces_yaml)
    except Exception:
        spolicy = None
    if spolicy is not None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), "diff", "--name-only", diff_range],
                capture_output=True,
                text=True,
                check=False,
            )
            touched = proc.stdout.splitlines()
        except OSError:
            touched = []
        cls_rank = spolicy.rank(cls)
        worst_surface = None
        worst_rank = -1
        for f in touched:
            if not f:
                continue
            best_spec = None
            best_len = -1
            for spec in spolicy.surfaces:
                sp = spec.path.rstrip("/")
                if f == sp or f.startswith(sp + "/"):
                    if len(sp) > best_len:
                        best_len = len(sp)
                        best_spec = spec
            if best_spec is not None:
                r = spolicy.rank(best_spec.declared_class)
                if r > worst_rank:
                    worst_rank = r
                    worst_surface = best_spec.surface
        if worst_surface is not None and 0 <= cls_rank < worst_rank:
            findings.append(
                (
                    "pr-class-below-surface",
                    "%s requires >= %s" % (worst_surface, spolicy.ladder[worst_rank]),
                )
            )

for code, detail in findings:
    emit(code, detail)
PY
  )"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    class_findings+=("$line")
  done <<<"$out"
}

# resolve_issue_number — the issue this PR/lane closes, from the body's
# Refs/Closes/Fixes/Resolves trailer or, failing that, an `issue-<n>` head
# branch name. Empty when neither names one (nothing to check against).
resolve_issue_number() { # <body-file> <head-branch>
  local body="$1" head_branch="${2:-}" n
  if [ -f "$body" ]; then
    n="$(grep -oE '(Refs[^#]*#|Closes #|Fixes #|Resolves #)[0-9]+' "$body" 2>/dev/null \
      | grep -oE '[0-9]+$' | head -1)"
    [ -n "$n" ] && { printf '%s' "$n"; return 0; }
  fi
  n="$(printf '%s' "$head_branch" | grep -oE '(^|[^0-9])issue-[0-9]+' | grep -oE '[0-9]+$' | head -1)"
  [ -n "$n" ] && printf '%s' "$n"
}

# check_duplicate_pr — the cheapest of the two 2026-09-18-wave controls
# (governance/lessons ledger, class "duplicate-pr-for-issue"; PRs #1097 vs
# #1115, #1131 vs #1106, and #1294's duplicate direct-merge all resolved the
# same issue from two open PRs, so no PR mutually excluded the other and both
# advanced in parallel until one won a race). Warn-only, same
# AO_PR_CONTRACT_ENFORCE switch as the rest of the classification block.
# Reads the open-PR list from AO_PR_CONTRACT_OPEN_PRS_JSON (a
# `gh pr list --json number,headRefName,body` capture) when set — this is what
# --self-test uses — else shells out to `gh` live; with neither reachable it
# is silently CANNOT-ASSESS for this one finding (not a gate-wide refusal: the
# PR-body checks around it still run).
check_duplicate_pr() { # <body-file> <head-branch> <self-pr-number>
  local body="$1" head_branch="${2:-}" self_num="${3:-}" issue list_json out other
  issue="$(resolve_issue_number "$body" "$head_branch")"
  [ -n "$issue" ] || return 0
  list_json="${AO_PR_CONTRACT_OPEN_PRS_JSON:-}"
  if [ -n "$list_json" ]; then
    [ -f "$list_json" ] || return 0
    out="$(cat "$list_json" 2>/dev/null)"
  elif command -v gh >/dev/null 2>&1; then
    out="$( (cd "$repo" && gh pr list --state open --json number,headRefName,body) 2>/dev/null)"
  else
    return 0
  fi
  [ -n "$out" ] || return 0
  other="$(AO_ISSUE="$issue" AO_SELF="$self_num" AO_PR_LIST_JSON="$out" python3 - 2>/dev/null <<'PY'
import json, os, re
issue = os.environ["AO_ISSUE"]
self_num = os.environ.get("AO_SELF", "")
try:
    data = json.loads(os.environ.get("AO_PR_LIST_JSON") or "[]")
except Exception:
    data = []
pat_body = re.compile(r"(?im)(?:Refs\s+\S*#|Closes\s+#|Fixes\s+#|Resolves\s+#)" + re.escape(issue) + r"\b")
pat_branch = re.compile(r"(?:^|[^0-9])issue-" + re.escape(issue) + r"(?:[^0-9]|$)")
for pr in data:
    num = str(pr.get("number", ""))
    if num and num == self_num:
        continue
    if pat_body.search(pr.get("body") or "") or pat_branch.search(pr.get("headRefName") or ""):
        print(num)
        break
PY
)"
  [ -n "$other" ] && class_findings+=("duplicate-pr-for-issue:${issue} (also open PR #${other})")
}

# check_semantic_merge_review — the second cheapest control: a merge commit in
# the PR's own range that resolved conflicts across >200 changed lines
# (`git log --merges` + `git show --shortstat`) must be accompanied by a `##
# Review` section in the PR body naming the reviewer's verdict (ledger class
# "review-required-for-semantic-merge"; PRs #1120/#1111 — a semantic-merge
# rebase that a reviewer pass caught at MEDIUM, with no PR-body obligation
# forcing that pass to happen). Warn-only, same enforcement switch.
check_semantic_merge_review() { # <body-file> <range>
  local body="$1" range="$2" merges m ins del tot biggest=0 biggest_sha=""
  merges="$(git -C "$repo" log --merges --format=%H "$range" 2>/dev/null)"
  [ -n "$merges" ] || return 0
  for m in $merges; do
    tot=0
    while read -r n; do
      [ -n "$n" ] && tot=$((tot + n))
    done < <(git -C "$repo" show --shortstat --format= "$m" 2>/dev/null | grep -oE '[0-9]+')
    if [ "$tot" -gt "$biggest" ]; then
      biggest="$tot"
      biggest_sha="$m"
    fi
  done
  [ "$biggest" -gt 200 ] || return 0
  if [ ! -f "$body" ] || ! grep -qiE '^##+[ \t]*Review[ \t]*$' "$body"; then
    class_findings+=("review-required-for-semantic-merge:${biggest_sha} (${biggest} changed lines, no ## Review section)")
    return 0
  fi
  # a `## Review` heading with nothing under it before the next heading is the
  # same defect as `pr-classification-missing` above — a title with no verdict.
  if ! awk '
    BEGIN{insec=0; nonblank=0}
    /^##+[ \t]*Review[ \t]*$/{insec=1; next}
    /^##+[ \t]/{if(insec) exit; next}
    insec && $0 ~ /[^[:space:]]/ {nonblank=1}
    END{exit nonblank?0:1}
  ' "$body" >/dev/null 2>&1; then
    class_findings+=("review-required-for-semantic-merge:${biggest_sha} (## Review section is empty, no verdict named)")
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

run_checks() { # <body-file> <range> [<head-branch>] [<surfaces-yaml>]
  findings=()
  class_findings=()
  check_body "$1"
  check_gate_changing "$1" "$2"
  check_commits "$2"
  local dr
  dr="$(diff_range_for "$2" 2>/dev/null || true)"
  check_classification "$1" "$dr" "${3:-}" "${4:-}"
  check_duplicate_pr "$1" "${3:-}" "${PR_NUMBER:-}"
  check_semantic_merge_review "$1" "$2"

  local enforce="${AO_PR_CONTRACT_ENFORCE:-0}" cf level
  if [ "${#class_findings[@]}" -gt 0 ]; then
    [ "$enforce" = "1" ] && level="FAIL" || level="WARN"
    for cf in "${class_findings[@]}"; do
      printf '  %s  classification:%s\n' "$level" "$cf" >&2
    done
  fi

  if [ "${#findings[@]}" -gt 0 ] || { [ "$enforce" = "1" ] && [ "${#class_findings[@]}" -gt 0 ]; }; then
    report "check-pr-contract"
    return 1
  fi
  if [ "${#class_findings[@]}" -gt 0 ]; then
    echo "check-pr-contract: OK — trailer block, AI-assistance, Closes and the pre-existing-red claim are all evidenced (classification: ${#class_findings[@]} warn-only finding(s); set AO_PR_CONTRACT_ENFORCE=1 to enforce)"
    return 0
  fi
  echo "check-pr-contract: OK — trailer block, AI-assistance, Closes, the pre-existing-red claim and the Classification block are all evidenced"
  return 0
}

# --- landed-history audit: every merged commit carries the ticket trailer -----
# PR-BODY obligations (Closes, AI-assistance, pre-existing red) live at PR time;
# the landed audit enforces the one obligation a commit can carry on its own —
# the trailing `Refs` trailer — over every non-merge commit since the boundary.
#
# `landed_collect` MEASURES; `landed_audit` reports the predicate's answer
# verbatim (the contract its programmatic consumers depend on; see "THE TWO
# CONTRACTS OF `--landed`" in the header); `landed_verdict` books those same
# measurements against the recorded-legacy record. The split is what lets the
# record apply to one reader without changing the other.
landed_collect() { # <range> <gate> — fills `findings`; rc 0 clean / 1 findings / 2 CANNOT-ASSESS
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

  local shas sha message subject finding
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
    fi
  done <<<"$shas"
  [ "${#findings[@]}" -gt 0 ] && return 1
  return 0
}

landed_audit() { # <range> <gate> — the predicate's findings for <range>, verbatim
  local rang="${1:-HEAD}" g="${2:-$default_gate}" rc
  landed_collect "$rang" "$g"; rc=$?
  [ "$rc" -eq 2 ] && return 2
  if [ "$rc" -eq 1 ]; then
    report "check-pr-contract: LANDED"
    return 1
  fi
  echo "check-pr-contract: LANDED OK — every merged commit since the enforcement gate carries the ticket trailer"
  return 0
}

# The record, through the isolation module's own loader: it owns the path
# convention and the parse (a 40-hex commit id, a code, a `why`; a duplicate or a
# malformed entry is refused), so nothing about the record is re-implemented here.
record_entries() { # "<40hex>\t<code>" per entry; rc 1 when the record cannot be trusted
  PR_CONTRACT_ROOT="$root" PR_CONTRACT_BASELINE="$baseline_file" python3 - <<'PY'
import os
import sys

sys.path.insert(0, os.environ["PR_CONTRACT_ROOT"])
try:
    from governance.isolation.landed import BaselineMalformed, load_baseline
except ImportError as exc:
    # The module owns the record. Without it nothing about the record is proven,
    # and an unprovable input is refused rather than skipped.
    print(f"baseline-loader-unavailable: {exc}")
    sys.exit(1)
try:
    baseline = load_baseline(os.environ["PR_CONTRACT_BASELINE"])
except BaselineMalformed as exc:
    print(str(exc))
    sys.exit(1)
for entry in baseline.entries:
    print(f"{entry.sha}\t{entry.code}")
PY
}

landed_verdict() { # <range> <gate> — the repository verdict, with the recorded legacy
  local rang="${1:-HEAD}" g="${2:-$default_gate}" rc entries f code fsha hit entry_sha entry_code
  local -a unrecorded=()
  local recorded=0
  landed_collect "$rang" "$g"; rc=$?
  [ "$rc" -eq 2 ] && return 2

  # `2>&1` because on success the loader prints nothing to stderr, and on failure
  # its own message is the honest reason this check cannot answer.
  if ! entries="$(record_entries 2>&1)"; then
    printf 'check-pr-contract: LANDED FAIL — the recorded-legacy record %s cannot be read, so the verdict is unproven; deleting the record is not a way to switch this check off\n' "$baseline_file" >&2
    [ -n "$entries" ] && printf '%s\n' "$entries" | sed 's/^/  /' >&2
    return 1
  fi

  # A finding is recorded legacy when the record names ITS COMMIT — matched on the
  # commit alone, which IS the record's own semantics (`landed.Baseline.find`), and
  # deliberately not on the code: one recorded commit (`4986cb636344`) already
  # carries a code the predicate has since changed, and a record that stopped
  # applying because the predicate's wording moved would un-grandfather a commit
  # nobody re-decided. Grandfathering only ever removes a finding this run
  # MEASURED for that commit, so no pass can be invented for an unmeasured one.
  for f in "${findings[@]}"; do
    code="${f%%:*}"; fsha="${f#*:}"
    hit=""
    while IFS=$'\t' read -r entry_sha entry_code; do
      [ -n "$entry_sha" ] || continue
      case "$entry_sha" in "$fsha"*) hit="$entry_sha"; break ;; esac
    done <<<"$entries"
    if [ -n "$hit" ]; then
      printf '  NOTE  recorded legacy  %s\n' "$f"
      recorded=$((recorded + 1))
    else
      unrecorded+=("$f")
    fi
  done

  if [ "${#unrecorded[@]}" -gt 0 ]; then
    for f in "${unrecorded[@]}"; do
      printf '  FAIL  %s\n' "$f" >&2
    done
    printf 'check-pr-contract: LANDED: FAIL (%s unrecorded finding(s), %s recorded legacy) — an unrecorded finding is a commit that landed after the boundary without the trailer: if it has not MERGED yet, put the reference in a trailing trailer paragraph and re-push; if it is already on a protected branch its message can never be corrected (history is never rewritten), so record it in %s by an explicit reviewed commit carrying this measured finding — never automatically, and never to silence a run (#836)\n' \
      "${#unrecorded[@]}" "$recorded" "${baseline_file#"$root"/}" >&2
    return 1
  fi
  printf 'check-pr-contract: LANDED OK — 0 unrecorded finding(s) after the enforcement gate; %s recorded legacy commit(s) in %s, named above and never hidden (#1402)\n' \
    "$recorded" "${baseline_file#"$root"/}"
  return 0
}

# --- PR-time hook: the body and the range come from GitHub -------------------
pr_check() { # <number>
  local number="$1" tmpdir bodyfile base_name head_oid head_name rang rc base_rev resolved
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
  # field), so the base rev is `origin/<base>`, falling back to the local base
  # branch when the remote-tracking ref is not present in the clone. The range is
  # then the PR'S OWN range — `merge-base(base, head)..head`, never the raw
  # `base..head` — because the diffing `Gate-changing:` cross-check would
  # otherwise judge the lane by every commit the base gained after the fork
  # (#1147; the two-dot/two-tree difference is spelled out in the header).
  base_name="$( ( cd "$repo" && gh pr view "$number" --json baseRefName --jq '.baseRefName' ) 2>/dev/null )"
  head_oid="$( ( cd "$repo" && gh pr view "$number" --json headRefOid --jq '.headRefOid' ) 2>/dev/null )"
  head_name="$( ( cd "$repo" && gh pr view "$number" --json headRefName --jq '.headRefName' ) 2>/dev/null )"
  if [ -z "$base_name" ] || [ -z "$head_oid" ]; then
    rm -rf "$tmpdir"
    echo "check-pr-contract: CANNOT-ASSESS — cannot resolve the base/head of PR #$number" >&2
    return 2
  fi
  rang=""
  for base_rev in "origin/$base_name" "$base_name"; do
    git -C "$repo" rev-parse --verify --quiet "$base_rev^{commit}" >/dev/null 2>&1 || continue
    resolved="$(merge_base_range "$base_rev" "$head_oid" 2>/dev/null)" || continue
    [ -n "$resolved" ] || continue
    if [ -n "$(git -C "$repo" rev-list --no-merges "$resolved" 2>/dev/null)" ]; then
      rang="$resolved"
      break
    fi
  done
  if [ -z "$rang" ]; then
    rm -rf "$tmpdir"
    echo "check-pr-contract: CANNOT-ASSESS — no non-merge commits between PR #$number's base ($base_name) and its head ($head_oid) (is the PR head fetched into this clone?)" >&2
    return 2
  fi
  run_checks "$bodyfile" "$rang" "$head_name" "$surfaces_yaml_override"
  rc=$?
  rm -rf "$tmpdir"
  return "$rc"
}

# --- the denylist inertness record (#1396) -----------------------------------
# `make verify` does not run this check while `pr-contract` is disabled by name
# in `scripts/check-denylist.txt`, so #1341's scoped PR-context passthrough in
# `scripts/verify.sh` is UNEXERCISED by the composite. The header states why that
# is recorded rather than wired in; what it must not be is a comment, because a
# comment cannot fail (GR-29: a doc-only rule is advisory). So the record is a
# property this gate checks, in BOTH directions, and the check is itself provoked
# with a mutant copy of the denylist.
denylist_marker="unexercised-seam: #1341"
denylist_path() { # the discovery layer's own seam (scripts/discover-checks.sh)
  printf '%s\n' "${CHECK_DENYLIST:-$root/scripts/check-denylist.txt}"
}
denylist_inertness() { # <denylist-file> — prints a refusal, rc 1; silent, rc 0
  local file="$1" out rc
  if [ ! -f "$file" ]; then
    echo "check-pr-contract: denylist-unreadable: $file (the record cannot be asserted without it)"
    return 1
  fi
  if grep -qE '^[[:space:]]*(pr-contract|check-pr-contract\.sh)[[:space:]]*$' "$file"; then
    # Disabled by name: the seam is inert, so the inertness must be RECORDED and
    # the passthrough the record names must still exist.
    if ! grep -qF "$denylist_marker" "$file"; then
      echo "check-pr-contract: denylist-inertness-undeclared: $file disables pr-contract, so #1341's PR-context passthrough in scripts/verify.sh is unexercised by the composite, and the record ($denylist_marker) is missing"
      return 1
    fi
    if ! grep -qF '"$name" = "pr-contract"' "$root/scripts/verify.sh" 2>/dev/null \
       || ! grep -qF 'pr_contract_context' "$root/scripts/verify.sh" 2>/dev/null; then
      echo "check-pr-contract: denylist-inertness-record-stale: the record names an unexercised passthrough, but scripts/verify.sh no longer wires one"
      return 1
    fi
    return 0
  fi
  # Not denylisted: the composite runs this check, so the record must be gone and
  # a context-less run must be a SKIP (rc 2), never a pass.
  if grep -qF "$denylist_marker" "$file"; then
    echo "check-pr-contract: denylist-inertness-record-stale: pr-contract is NOT denylisted any more, so the seam is live and the record ($denylist_marker) must be deleted"
    return 1
  fi
  out="$(env -u AO_PR_NUMBER -u _PR_NUMBER -u PR_NUMBER -u AO_PR_BODY_FILE -u AO_PR_RANGE \
    bash "$0" --repo "$root" 2>&1)"
  rc=$?
  if [ "$rc" -ne 2 ]; then
    echo "check-pr-contract: denylist-inertness-record-stale: pr-contract is wired into the composite, but a context-less run exits $rc, not 2 — the composite would record a PASS where nothing was judged"
    return 1
  fi
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

## Merge order

Gate-changing: no

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
    printf '## Closes\n\nCloses #288\n\n## Merge order\n\nGate-changing: no\n\n'
    printf '## AI-assistance\n\n'
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

  # --- the recorded legacy: one rule, one record, two readers (#1402) ---------
  # `landed_verdict` books the findings it MEASURED against the record
  # `governance/isolation/landed.py` owns, read through that module's own loader.
  # Four directions are asserted: a recorded finding is booked (rc 0), an
  # UNRECORDED one is still refused by name, an unreadable record is NOT-OK
  # rather than a skip, and — the seam — the record does NOT reach the predicate
  # path its programmatic consumers read. If the last arm ever passes while the
  # first fails, the record has been applied one reader too far.
  #
  # The record is built from the findings THIS run measures, expanded to full
  # commit ids (the loader refuses anything but a 40-hex id), so the arms cannot
  # drift when an earlier arm adds a commit to the range.
  recorded_baseline="$work/landed-baseline.json"
  empty_baseline="$work/landed-baseline-empty.json"
  partial_baseline="$work/landed-baseline-partial.json"
  malformed_baseline="$work/landed-baseline-malformed.json"
  findings_tsv="$work/landed-findings.tsv"
  : >"$findings_tsv"
  landed_collect "$base..HEAD" "$a_sha" >/dev/null 2>&1
  for f in "${findings[@]}"; do
    printf '%s\t%s\n' "$(git -C "$scratch" rev-parse "${f#*:}")" "${f%%:*}" >>"$findings_tsv"
  done
  python3 - "$findings_tsv" "$recorded_baseline" "$empty_baseline" "$partial_baseline" \
    "$malformed_baseline" "$b_sha" <<'PY'
import json
import sys

tsv, recorded, empty, partial, malformed, keep = sys.argv[1:7]
entries = []
with open(tsv, encoding="utf-8") as fh:
    for line in fh:
        sha, _, code = line.strip().partition("\t")
        if sha and code:
            entries.append({"sha": sha, "code": code, "why": "provoked by --selftest"})


def write(path, payload):
    json.dump(
        {"measured_at": "2026-09-19", "measured_head": "", "entries": payload},
        open(path, "w", encoding="utf-8"),
        indent=2,
    )


write(recorded, entries)
write(empty, [])
write(partial, [entry for entry in entries if entry["sha"] == keep])
# The loader rejects an unparseable record, and a rejected record must not be a
# way to switch the check off (the rule `landed.py` applies to its own baseline).
json.dump(
    {"measured_at": "2026-09-19", "measured_head": "", "entries": "not-a-list"},
    open(malformed, "w", encoding="utf-8"),
    indent=2,
)
PY

  # NOTE the containment form: bash-native `[[ … == *"needle"* ]]`, never a pipe
  # into `grep -q`. `check-verdict-contains` counts the pipe idiom per file and its
  # record is shrink-only, so a new `printf | grep -q` here REFUSES the whole gate
  # (measured: nine of them took this file 15 -> 24 against a recorded 15).
  # 12. a MEASURED finding the record names is booked as recorded legacy and the
  #     repository verdict is OK — with the residue named, never hidden.
  baseline_file="$recorded_baseline"
  out="$(landed_verdict "$base..HEAD" "$a_sha" 2>&1)"
  if [ $? -eq 0 ] \
     && [[ "$out" == *"recorded legacy  commit-missing-ticket-trailer:${c_sha:0:12}"* ]] \
     && [[ "$out" == *"LANDED OK"* ]]; then
    printf '  OK    a recorded post-boundary finding is booked as recorded legacy, not refused\n'
  else
    printf '  FAIL  a recorded post-boundary finding was not booked as recorded legacy\n%s\n' "$out" >&2
    ok=1
  fi

  # 13. per-commit precision, in both directions at once: with ONLY `b` recorded,
  #     `b` is booked and the unrecorded `c` is still refused BY NAME — the record
  #     grandfathers the commits it names, never the run. The unrecorded COUNT is
  #     whatever the earlier arms left in the range, so it is not asserted; the
  #     recorded count is exactly the one entry this arm planted.
  baseline_file="$partial_baseline"
  out="$(landed_verdict "$base..HEAD" "$a_sha" 2>&1)"
  if [ $? -eq 1 ] \
     && [[ "$out" == *"  FAIL  commit-missing-ticket-trailer:${c_sha:0:12}"* ]] \
     && [[ "$out" == *"recorded legacy  commit-ref-only-in-subject:${b_sha:0:12}"* ]] \
     && [[ "$out" == *", 1 recorded legacy)"* ]]; then
    printf '  OK    an unrecorded finding is refused while a recorded one is booked, in one run\n'
  else
    printf '  FAIL  the record did not book/refuse per commit\n%s\n' "$out" >&2
    ok=1
  fi

  # 13b. …and an EMPTY record grandfathers nothing at all.
  baseline_file="$empty_baseline"
  out="$(landed_verdict "$base..HEAD" "$a_sha" 2>&1)"
  if [ $? -eq 1 ] && [[ "$out" == *"commit-missing-ticket-trailer:${c_sha:0:12}"* ]]; then
    printf '  OK    an empty record grandfathers nothing (the record is the act)\n'
  else
    printf '  FAIL  an empty record was treated as a pass\n%s\n' "$out" >&2
    ok=1
  fi

  # 14. THE SEAM. With the record that grandfathers every finding in the range
  #     still in force, the predicate path (`landed_audit`, the shape every
  #     programmatic consumer asks for with an explicit --range) must keep
  #     refusing them: the record applies to the repository verdict, never to the
  #     verbatim re-check.
  baseline_file="$recorded_baseline"
  out="$(landed_audit "$base..HEAD" "$a_sha" 2>&1)"
  if [ $? -eq 1 ] && [[ "$out" == *"commit-missing-ticket-trailer:${c_sha:0:12}"* ]]; then
    printf '  OK    the record does not weaken the predicate path its consumers read\n'
  else
    printf '  FAIL  the record leaked into the predicate path (explicit --range)\n%s\n' "$out" >&2
    ok=1
  fi

  # 15. an unreadable record is NOT-OK (rc 1), never a skip: otherwise deleting
  #     the file would be a way to switch the check off. Both shapes are provoked.
  baseline_file="$work/absent-baseline.json"
  out="$(landed_verdict "$base..HEAD" "$a_sha" 2>&1)"
  if [ $? -eq 1 ] && [[ "$out" == *"baseline-missing"* ]]; then
    printf '  OK    a missing record is refused (rc 1, never a skip)\n'
  else
    printf '  FAIL  a missing record was not refused as NOT-OK\n%s\n' "$out" >&2
    ok=1
  fi
  baseline_file="$malformed_baseline"
  out="$(landed_verdict "$base..HEAD" "$a_sha" 2>&1)"
  if [ $? -eq 1 ] && [[ "$out" == *"baseline-malformed"* ]]; then
    printf '  OK    a malformed record is refused by name\n'
  else
    printf '  FAIL  a malformed record was not refused by name\n%s\n' "$out" >&2
    ok=1
  fi
  baseline_file="$baseline_default"

  # --- Gate-changing plants (issue #1054) -------------------------------------
  # plant (a): missing/placeholder `Gate-changing:` line is refused by name.
  no_gate_body="$work/no-gate-changing.md"
  cat >"$no_gate_body" <<'MD'
## Closes

Closes #1054

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

None
MD
  out="$(run_checks "$no_gate_body" "$base..$a_sha" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"pr-body-missing-gate-changing"* ]]; then
    printf '  OK    plant (a): a missing Gate-changing line is refused by name\n'
  else
    printf '  FAIL  plant (a) went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # placeholder form (the untouched template) is refused the same way.
  placeholder_gate_body="$work/placeholder-gate.md"
  cat >"$placeholder_gate_body" <<'MD'
## Closes

Closes #1054

## Merge order

Gate-changing: <no | yes — paths>

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

None
MD
  out="$(run_checks "$placeholder_gate_body" "$base..$a_sha" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"pr-body-missing-gate-changing"* ]]; then
    printf '  OK    plant (a): the unfilled Gate-changing placeholder is refused by name\n'
  else
    printf '  FAIL  the Gate-changing placeholder went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # plant (b): declared `no` while the range touches a gate path.
  mkdir -p "$scratch/scripts"
  printf 'x\n' >"$scratch/scripts/check-plant-b.sh"
  git -C "$scratch" add scripts/check-plant-b.sh >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "touch a gate path" \
    -m "Refs kushin77/agent-orchestrator#1054" >/dev/null 2>&1
  plantb_sha="$(git -C "$scratch" rev-parse HEAD)"
  gate_no_body="$work/gate-no.md"
  cat >"$gate_no_body" <<'MD'
## Closes

Closes #1054

## Merge order

Gate-changing: no

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

None
MD
  out="$(run_checks "$gate_no_body" "${plantb_sha}^..$plantb_sha" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"pr-body-gate-changing-mismatch-no"* ]]; then
    printf '  OK    plant (b): declared no while touching a gate path is refused by name\n'
  else
    printf '  FAIL  plant (b) went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # plant (c): declared `yes` while the range touches none.
  gate_yes_body="$work/gate-yes.md"
  cat >"$gate_yes_body" <<'MD'
## Closes

Closes #1054

## Merge order

Gate-changing: yes — scripts/check-plant-b.sh

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

None
MD
  out="$(run_checks "$gate_yes_body" "$base..$a_sha" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"pr-body-gate-changing-mismatch-yes"* ]]; then
    printf '  OK    plant (c): declared yes while touching no gate path is refused by name\n'
  else
    printf '  FAIL  plant (c) went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # non-vacuity: a correctly declared `yes` against a gate-touching range passes.
  gate_ok_body="$work/gate-ok.md"
  cat >"$gate_ok_body" <<'MD'
## Closes

Closes #1054

## Merge order

Gate-changing: yes — scripts/check-plant-b.sh

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

None
MD
  out="$(run_checks "$gate_ok_body" "${plantb_sha}^..$plantb_sha" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" == *"check-pr-contract: OK"* ]]; then
    printf '  OK    a correctly declared Gate-changing: yes passes\n'
  else
    printf '  FAIL  a correct Gate-changing declaration was refused\n%s\n' "$out" >&2
    ok=1
  fi

  # --- mutant: the Gate-changing check must be load-bearing -------------------
  # Neuter every Gate-changing finding into a no-op and prove the mutant
  # diverges from the real script on plant (a): the real gate refuses it, the
  # mutant accepts it. A mutant byte-identical to the original proves nothing.
  gate_mutant="$work/check-pr-contract.mutant.sh"
  sed \
    -e 's/findings+=("pr-body-missing-gate-changing")/:/' \
    -e 's/findings+=("pr-body-gate-changing-mismatch-no")/:/' \
    -e 's/findings+=("pr-body-gate-changing-mismatch-yes")/:/' \
    "$0" >"$gate_mutant"
  if cmp -s "$0" "$gate_mutant"; then
    echo "check-pr-contract: SELFTEST FAIL — the Gate-changing mutant is byte-identical to this script; the mutation proved nothing" >&2
    ok=1
  else
    mutant_out="$(bash "$gate_mutant" --repo "$scratch" --body-file "$no_gate_body" --range "$base..$a_sha" 2>&1)"
    mutant_rc=$?
    if [ "$mutant_rc" -eq 0 ]; then
      printf '  OK    the mutant (Gate-changing check neutered) accepts plant (a); it diverges from the real gate\n'
    else
      printf '  FAIL  the mutant still refused plant (a); the check is not load-bearing\n%s\n' "$mutant_out" >&2
      ok=1
    fi
  fi

  # --- #1147: a base that MOVED must not be judged as if it were the lane ------
  # The scratch repository above never moves a base, and THAT is why this defect
  # survived: while `base` is an ancestor of the lane, `git diff base..lane` and
  # `git diff $(git merge-base base lane)..lane` are the same diff, so no case
  # here could tell them apart. These cases move the base and then judge the range
  # exactly as `--pr` derives it. (Measured on the real repo: PR #1125, head
  # `84e0bba`, base `c9040b9` — refused `pr-body-gate-changing-mismatch-no` for
  # master's own commits while every other check of the same PR said OK.)
  moved_lane="lane-moved-1147"
  moved_base_branch="base-moved-1147"
  git -C "$scratch" branch "$moved_base_branch" "$base" >/dev/null 2>&1
  git -C "$scratch" checkout -q -b "$moved_lane" "$base" >/dev/null 2>&1
  printf 'lane-1147\n' >"$scratch/lane-only-1147.txt"
  git -C "$scratch" add lane-only-1147.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "the lane own non-gate change" \
    -m "Refs kushin77/agent-orchestrator#1147" >/dev/null 2>&1
  moved_lane_sha="$(git -C "$scratch" rev-parse HEAD)"
  # …and the BASE moves on, touching a gate path the lane never touched.
  git -C "$scratch" checkout -q "$moved_base_branch" >/dev/null 2>&1
  mkdir -p "$scratch/scripts"
  printf 'base-1147\n' >"$scratch/scripts/check-base-moved-1147.sh"
  git -C "$scratch" add scripts/check-base-moved-1147.sh >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "the base own gate-path change" \
    -m "Refs kushin77/agent-orchestrator#1147" >/dev/null 2>&1
  moved_base_sha="$(git -C "$scratch" rev-parse HEAD)"
  moved_range="$moved_base_sha..$moved_lane_sha"

  # 12. the lane own diff touches NO gate path while the base moved with one: the
  #     declared `no` is CORRECT, so it must pass. Two-dot, this was refused.
  #     The assertion is the bash-native containment test, never
  #     `… | grep -q` — a quiet grep exits on its first match and SIGPIPEs the
  #     producer, which `set -o pipefail` turns into a status for the whole
  #     pipeline, so that idiom fails OPEN on a large report
  #     (`scripts/check-verdict-contains.sh`, whose record is shrink-only).
  out="$(run_checks "$gate_no_body" "$moved_range" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" == *"check-pr-contract: OK"* ]]; then
    printf '  OK    a moved base does not vote — the lane diff decides Gate-changing\n'
  else
    printf '  FAIL  a moved base was judged as the lane diff\n%s\n' "$out" >&2
    ok=1
  fi

  # 12b. non-vacuity for case 12: the same body and the same range under a mutant
  #      that reinstates the raw two-dot diff must be REFUSED, so case 12 measures
  #      the merge-base resolution rather than agreeing with whatever the gate now
  #      happens to do. The mutant lives INSIDE the scratch repo (and gets a copy
  #      of the real gate-paths list) because the script derives both its own repo
  #      root and that list from `BASH_SOURCE` — a mutant left next to the real
  #      script would resolve a paths file that is not there and measure nothing.
  moved_mutant="$scratch/scripts/check-pr-contract.two-dot.sh"
  mkdir -p "$scratch/scripts/lib"
  cp "$root/scripts/lib/gate-paths.txt" "$scratch/scripts/lib/gate-paths.txt"
  sed -e 's|diff_range="$(diff_range_for "$rang")"|diff_range="$rang"|' "$0" >"$moved_mutant"
  if cmp -s "$0" "$moved_mutant"; then
    echo "check-pr-contract: SELFTEST FAIL — the two-dot mutant is byte-identical to this script; the mutation proved nothing" >&2
    ok=1
  else
    mutant_out="$(bash "$moved_mutant" --repo "$scratch" --body-file "$gate_no_body" --range "$moved_range" 2>&1)"
    mutant_rc=$?
    if [ "$mutant_rc" -ne 0 ] && [[ "$mutant_out" == *"pr-body-gate-changing-mismatch-no"* ]]; then
      printf '  OK    the two-dot mutant refuses it; case 12 measures the merge-base fix\n'
    else
      printf '  FAIL  the two-dot mutant did not refuse the moved-base case (rc=%s)\n%s\n' "$mutant_rc" "$mutant_out" >&2
      ok=1
    fi
  fi

  # 13. non-permissiveness: with the base moved, a lane that REALLY touches a gate
  #     path and declares `no` is still refused by name — the fix must not blind
  #     the check it repairs.
  git -C "$scratch" checkout -q "$moved_lane" >/dev/null 2>&1
  mkdir -p "$scratch/scripts"
  printf 'lane-gate-1147\n' >"$scratch/scripts/check-lane-moved-1147.sh"
  git -C "$scratch" add scripts/check-lane-moved-1147.sh >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "the lane own gate-path change" \
    -m "Refs kushin77/agent-orchestrator#1147" >/dev/null 2>&1
  lane_touch_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(run_checks "$gate_no_body" "$moved_base_sha..$lane_touch_sha" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"pr-body-gate-changing-mismatch-no"* ]]; then
    printf '  OK    a lane that really touches a gate path is still refused (declared no)\n'
  else
    printf '  FAIL  the merge-base range blinded the Gate-changing check\n%s\n' "$out" >&2
    ok=1
  fi

  # 14. …and the resolved range is the lane own commits, not an empty diff: a
  #     wrong `yes` is still refused. (A diff resolved down to nothing would
  #     accept both declarations, which is the other way to make case 12 pass.)
  out="$(run_checks "$gate_yes_body" "$moved_range" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"pr-body-gate-changing-mismatch-yes"* ]]; then
    printf '  OK    a wrong yes is still refused against the lane diff\n'
  else
    printf '  FAIL  a wrong yes was accepted after the merge-base resolution\n%s\n' "$out" >&2
    ok=1
  fi

  # --- the Classification block (issue #1254 step 5 / #1328) -----------------
  # WARN-ONLY by default: every plant below must be provoked BY NAME with
  # AO_PR_CONTRACT_ENFORCE=1 (rc 1) and must NOT flip a plain run's rc (warn
  # only, rc 0) — proving the warn-only default is real, not decorative.
  class_good_body() {
    cat <<MD
## Closes

Closes #1328

## Classification

class: pattern
posture: overall
lifecycle: build
pillar: governance
pattern: none
lane: issue-1328

## Merge order

Gate-changing: no

## Evidence

\`\`\`
\$ make verify
verify: PASS (30 of 30 checks)
\`\`\`

## AI-assistance

AI-assistance: Copilot (Relentless, flash/LOW)

## Pre-existing red

None
MD
  }
  class_good="$work/class-good.md"
  class_good_body >"$class_good"

  expect_class() { # <label> <body-file> <head-branch> <want-code> <surfaces-yaml>
    local label="$1" body="$2" head="$3" want="$4" syaml="${5:-}" out rc
    out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$body" "$base..$a_sha" "$head" "$syaml" 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ] && [[ "$out" == *"classification:$want"* ]]; then
      printf '  OK    %s\n' "$label"
    else
      printf '  FAIL  %s (rc=%s)\n%s\n' "$label" "$rc" "$out" >&2
      ok=1
    fi
    # …and the SAME plant, warn-only (no AO_PR_CONTRACT_ENFORCE), must not flip rc.
    out="$(run_checks "$body" "$base..$a_sha" "$head" "$syaml" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ] && [[ "$out" == *"classification:$want"* ]]; then
      printf '  OK    %s — warn-only by default (rc stays 0)\n' "$label"
    else
      printf '  FAIL  %s did not stay warn-only (rc=%s)\n%s\n' "$label" "$rc" "$out" >&2
      ok=1
    fi
  }

  # a compliant body on its declared lane passes clean, ENFORCED, with no
  # classification finding at all.
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$class_good" "$base..$a_sha" "issue-1328" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" == *"check-pr-contract: OK"* ]] && [[ "$out" != *"classification:"* ]]; then
    printf '  OK    a compliant Classification block passes enforced, with no findings\n'
  else
    printf '  FAIL  a compliant Classification block was refused\n%s\n' "$out" >&2
    ok=1
  fi

  # no block at all.
  expect_class "a missing Classification block is refused by name" "$good_body" "issue-1328" "pr-classification-missing"

  # class outside the ladder.
  bad_class="$work/class-bad-class.md"
  class_good_body | sed 's/^class: pattern$/class: not-a-rung/' >"$bad_class"
  expect_class "an unknown class is refused by name" "$bad_class" "issue-1328" "pr-class-unknown"

  # posture outside the vocabulary.
  bad_posture="$work/class-bad-posture.md"
  class_good_body | sed 's/^posture: overall$/posture: made-up/' >"$bad_posture"
  expect_class "an unknown posture is refused by name" "$bad_posture" "issue-1328" "pr-posture-unknown"

  # lifecycle outside the vocabulary.
  bad_lifecycle="$work/class-bad-lifecycle.md"
  class_good_body | sed 's/^lifecycle: build$/lifecycle: made-up/' >"$bad_lifecycle"
  expect_class "an unknown lifecycle is refused by name" "$bad_lifecycle" "issue-1328" "pr-lifecycle-unknown"

  # pillar outside the vocabulary.
  bad_pillar="$work/class-bad-pillar.md"
  class_good_body | sed 's/^pillar: governance$/pillar: made-up/' >"$bad_pillar"
  expect_class "an unknown pillar is refused by name" "$bad_pillar" "issue-1328" "pr-pillar-unknown"

  # posture contradiction: both mutually-exclusive autonomy claims declared.
  contra_posture="$work/class-posture-contradiction.md"
  class_good_body | sed 's/^posture: overall$/posture: no-human-needed, human-gated/' >"$contra_posture"
  expect_class "a posture contradiction is refused by name" "$contra_posture" "issue-1328" "pr-posture-contradiction"

  # pattern id that resolves to nothing in the named doc.
  bad_pattern="$work/class-bad-pattern.md"
  class_good_body | sed 's/^pattern: none$/pattern: SP-99999/' >"$bad_pattern"
  expect_class "an unresolvable pattern id is refused by name" "$bad_pattern" "issue-1328" "pr-pattern-unresolvable"

  # a REAL pattern id resolves cleanly (non-vacuity for the resolver).
  real_pattern="$work/class-real-pattern.md"
  class_good_body | sed 's/^pattern: none$/pattern: SP-1/' >"$real_pattern"
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$real_pattern" "$base..$a_sha" "issue-1328" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" != *"pr-pattern-unresolvable"* ]]; then
    printf '  OK    a real pattern id (SP-1) resolves and is accepted\n'
  else
    printf '  FAIL  a real pattern id was refused\n%s\n' "$out" >&2
    ok=1
  fi

  # lane declares an issue branch that does not match the actual head.
  expect_class "a lane/head mismatch is refused by name" "$class_good" "issue-9999" "pr-lane-mismatch"

  # lane: direct on a branch that IS an issue lane is also a mismatch.
  direct_on_lane="$work/class-direct-on-lane.md"
  class_good_body | sed 's/^lane: issue-1328$/lane: direct/' >"$direct_on_lane"
  expect_class "lane: direct on an issue-lane head is refused by name" "$direct_on_lane" "issue-1328" "pr-lane-mismatch"

  # lane: direct on a non-lane head is accepted.
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$direct_on_lane" "$base..$a_sha" "main" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" != *"pr-lane-mismatch"* ]]; then
    printf '  OK    lane: direct on a non-lane head is accepted\n'
  else
    printf '  FAIL  lane: direct on a non-lane head was refused\n%s\n' "$out" >&2
    ok=1
  fi

  # class-below-surface: a SCRATCH surfaces.yaml declares one root `elite`;
  # the lane touches it while declaring `pattern` — below the surface's rung.
  scratch_surfaces="$work/surfaces.scratch.yaml"
  cat >"$scratch_surfaces" <<'YAML'
ladder: [template, pattern, elite]
evidence:
  tests:
    kind: machine
    description: a test suite exists
requirements:
  template: []
  pattern: [tests]
  elite: [tests]
surfaces:
  - surface: scratch-elite-surface
    path: elitepath
    declared_class: elite
YAML
  mkdir -p "$scratch/elitepath"
  printf 'x\n' >"$scratch/elitepath/thing.txt"
  git -C "$scratch" add elitepath/thing.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -q \
    -m "touch a surface the scratch policy declares elite" \
    -m "Refs kushin77/agent-orchestrator#1328" >/dev/null 2>&1
  below_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$class_good" "${below_sha}^..$below_sha" "issue-1328" "$scratch_surfaces" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"classification:pr-class-below-surface"* ]]; then
    printf '  OK    a class below the touched surface'"'"'s rung is refused by name\n'
  else
    printf '  FAIL  a below-surface class went undetected\n%s\n' "$out" >&2
    ok=1
  fi

  # non-vacuity: the same touch declaring `elite` (at or above the surface's
  # rung) is accepted.
  at_surface="$work/class-at-surface.md"
  class_good_body | sed 's/^class: pattern$/class: elite/' >"$at_surface"
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$at_surface" "${below_sha}^..$below_sha" "issue-1328" "$scratch_surfaces" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" != *"pr-class-below-surface"* ]]; then
    printf '  OK    a class at the surface'"'"'s own rung is accepted\n'
  else
    printf '  FAIL  a correctly-classed touch was refused\n%s\n' "$out" >&2
    ok=1
  fi

  # --- duplicate-pr-for-issue (2026-09-18 wave: #1097 vs #1115, #1131 vs
  # #1106 — two OPEN PRs resolving the same issue, neither excluding the
  # other). Warn-only, same AO_PR_CONTRACT_ENFORCE switch; fed a fixture
  # open-PR list so the self-test needs no network / real `gh`.
  dup_json="$work/open-prs.json"
  cat >"$dup_json" <<'JSON'
[{"number": 9999, "headRefName": "issue-1328", "body": "Closes #1328"}]
JSON
  PR_NUMBER="1328"
  out="$(AO_PR_CONTRACT_ENFORCE=1 AO_PR_CONTRACT_OPEN_PRS_JSON="$dup_json" run_checks "$class_good" "$base..$a_sha" "issue-1328" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"duplicate-pr-for-issue:1328"* ]]; then
    printf '  OK    a second open PR for the same issue is refused by name\n'
  else
    printf '  FAIL  a duplicate PR for one issue was not detected\n%s\n' "$out" >&2
    ok=1
  fi
  # negative control: the open-PR list names a different issue — no finding.
  cat >"$dup_json" <<'JSON'
[{"number": 9999, "headRefName": "issue-4242", "body": "Closes #4242"}]
JSON
  out="$(AO_PR_CONTRACT_ENFORCE=1 AO_PR_CONTRACT_OPEN_PRS_JSON="$dup_json" run_checks "$class_good" "$base..$a_sha" "issue-1328" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" != *"duplicate-pr-for-issue"* ]]; then
    printf '  OK    a real tree with no duplicate PR is not flagged (negative control)\n'
  else
    printf '  FAIL  a false positive: no duplicate exists but one was reported\n%s\n' "$out" >&2
    ok=1
  fi
  PR_NUMBER=""

  # --- review-required-for-semantic-merge (2026-09-18 wave: #1120/#1111 — a
  # semantic-merge rebase whose reviewer pass caught a MEDIUM, with no PR-body
  # obligation forcing that pass). Build a real merge commit with >200 changed
  # lines in the scratch repo and require a non-empty `## Review` section.
  git -C "$scratch" checkout -q -B side-big "$base" >/dev/null 2>&1
  seq 1 300 >"$scratch/big.txt"
  git -C "$scratch" add big.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -qm "$(printf 'side: add a 300-line file\n\nRefs kushin77/agent-orchestrator#1328')" >/dev/null 2>&1
  side_sha="$(git -C "$scratch" rev-parse HEAD)"
  git -C "$scratch" checkout -q -B main-big "$base" >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false merge -q --no-ff -m "merge: bring in the 300-line file" "$side_sha" >/dev/null 2>&1
  merge_sha="$(git -C "$scratch" rev-parse HEAD)"

  no_review="$work/no-review.md"
  class_good_body >"$no_review"
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$no_review" "$base..$merge_sha" "issue-1328" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"review-required-for-semantic-merge:${merge_sha}"* ]]; then
    printf '  OK    a >200-line semantic merge with no ## Review section is refused\n'
  else
    printf '  FAIL  a semantic merge with no review section was accepted\n%s\n' "$out" >&2
    ok=1
  fi

  empty_review="$work/empty-review.md"
  { class_good_body; printf '\n## Review\n\n'; } >"$empty_review"
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$empty_review" "$base..$merge_sha" "issue-1328" 2>&1)"
  if [ $? -ne 0 ] && [[ "$out" == *"review-required-for-semantic-merge:${merge_sha}"* ]]; then
    printf '  OK    an empty ## Review section (a heading with no verdict) is refused\n'
  else
    printf '  FAIL  an empty review section was accepted as a verdict\n%s\n' "$out" >&2
    ok=1
  fi

  with_review="$work/with-review.md"
  { class_good_body; printf '\n## Review\n\nApproved — reviewer: gate, verdict: LGTM (found one MEDIUM, fixed).\n'; } >"$with_review"
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$with_review" "$base..$merge_sha" "issue-1328" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" != *"review-required-for-semantic-merge"* ]]; then
    printf '  OK    a semantic merge with a filled ## Review section is accepted (negative control)\n'
  else
    printf '  FAIL  a real reviewed semantic merge was refused\n%s\n' "$out" >&2
    ok=1
  fi

  # non-vacuity: a small (<=200 line) merge, no Review section, is not flagged.
  git -C "$scratch" checkout -q -B side-small "$base" >/dev/null 2>&1
  printf 'tiny\n' >"$scratch/tiny.txt"
  git -C "$scratch" add tiny.txt >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false commit -qm "$(printf 'side: tiny file\n\nRefs kushin77/agent-orchestrator#1328')" >/dev/null 2>&1
  small_side_sha="$(git -C "$scratch" rev-parse HEAD)"
  git -C "$scratch" checkout -q -B main-small "$base" >/dev/null 2>&1
  git -C "$scratch" -c commit.gpgsign=false merge -q --no-ff -m "merge: bring in the tiny file" "$small_side_sha" >/dev/null 2>&1
  small_merge_sha="$(git -C "$scratch" rev-parse HEAD)"
  out="$(AO_PR_CONTRACT_ENFORCE=1 run_checks "$no_review" "$base..$small_merge_sha" "issue-1328" 2>&1)"
  if [ $? -eq 0 ] && [[ "$out" != *"review-required-for-semantic-merge"* ]]; then
    printf '  OK    a small merge under the 200-line threshold is not flagged\n'
  else
    printf '  FAIL  a small merge tripped the semantic-merge-review control\n%s\n' "$out" >&2
    ok=1
  fi

  # --- ambient-vs-argv precedence (#1396) -----------------------------------
  # A control must not read the machine it is supposed to be independent of. An
  # exported PR context is a HINT about which PR the caller is on; an explicit
  # `--body-file`/`--range` is a STATEMENT of what to judge, and the statement
  # wins. Every arm below runs with a stub `gh` on PATH that FAILS, so a hijacked
  # run cannot hide behind a live GitHub read: it answers, by name, for the
  # ambient PR number instead.
  stub_bin="$work/bin"
  mkdir -p "$stub_bin"
  printf '#!/usr/bin/env bash\nexit 1\n' >"$stub_bin/gh"
  chmod +x "$stub_bin/gh"
  hijack_argv=(--repo "$scratch" --body-file "$good_body" --range "$base..$a_sha")
  clean_out="$(env PATH="$stub_bin:$PATH" bash "$0" "${hijack_argv[@]}" 2>&1)"
  clean_rc=$?
  if [ "$clean_rc" -eq 0 ]; then
    printf '  OK    the explicit argv alone reaches a verdict (rc=0), so the arms below measure the hint\n'
  else
    printf '  FAIL  the explicit argv alone did not reach a verdict (rc=%s)\n%s\n' "$clean_rc" "$clean_out" >&2
    ok=1
  fi
  # (a)/(b) the hijack itself, under BOTH ambient spellings the check reads, each
  # set to a DIFFERENT PR than any the argv names.
  for ambient in PR_NUMBER _PR_NUMBER; do
    out="$(env PATH="$stub_bin:$PATH" "$ambient=1396" bash "$0" "${hijack_argv[@]}" 2>&1)"
    rc=$?
    if [ "$rc" -eq "$clean_rc" ] && [ "$out" = "$clean_out" ]; then
      printf '  OK    an ambient %s naming a DIFFERENT PR leaves the explicit argv verdict byte-identical (#1396)\n' "$ambient"
    else
      printf '  FAIL  ambient %s hijacked the explicit argv (rc %s -> %s)\n%s\n' "$ambient" "$clean_rc" "$rc" "$out" >&2
      ok=1
    fi
  done
  # (c) negative controls: with NO argv naming a subject the hint IS the context.
  #     This is #1341's no-flags passthrough — it must still reach `pr_check`,
  #     and a context it cannot resolve must be a SKIP, never a pass. `gh` is the
  #     failing stub, so the arm also proves the arm cannot silently reach GitHub.
  for hint in AO_PR_NUMBER PR_NUMBER; do
    out="$(env PATH="$stub_bin:$PATH" "$hint=1396" bash "$0" --repo "$scratch" 2>&1)"
    rc=$?
    if [ "$rc" -eq 2 ] && [[ "$out" == *"PR #1396"* ]]; then
      printf '  OK    with no argv, %s still selects the PR context (rc 2, a SKIP that names PR #1396)\n' "$hint"
    else
      printf '  FAIL  with no argv, %s no longer reaches pr_check (rc=%s)\n%s\n' "$hint" "$rc" "$out" >&2
      ok=1
    fi
  done
  # (d) the mutant: the ambient-first resolution restored. It must be hijacked,
  #     naming the ambient PR — otherwise the control above proves nothing. It
  #     lives inside the scratch repo (with a copy of the real gate-paths list,
  #     made by case 12b) because the script derives both its own repo root and
  #     that list from `BASH_SOURCE`. The pattern is ANCHORED at column 0: an
  #     unanchored one would also match this very line and delete the arm.
  prec_mutant="$scratch/scripts/check-pr-contract.ambient-first.sh"
  sed -e '/^# --- BEGIN #1396 precedence block/,/^# --- END #1396 precedence block/c\PR_NUMBER="${_PR_NUMBER:-${PR_NUMBER:-}}"' "$0" >"$prec_mutant"
  if cmp -s "$0" "$prec_mutant"; then
    echo "check-pr-contract: SELFTEST FAIL — the ambient-precedence mutant is byte-identical to this script; the mutation proved nothing" >&2
    ok=1
  else
    out="$(env PATH="$stub_bin:$PATH" PR_NUMBER=1396 bash "$prec_mutant" "${hijack_argv[@]}" 2>&1)"
    rc=$?
    if [ "$rc" -ne "$clean_rc" ] && [[ "$out" == *"PR #1396"* ]]; then
      printf '  OK    the mutant that resolves the ambient name unconditionally IS hijacked by PR #1396; the control is load-bearing\n'
    else
      printf '  FAIL  the ambient-first mutant was not hijacked (rc=%s)\n%s\n' "$rc" "$out" >&2
      ok=1
    fi
    rm -f "$prec_mutant"
  fi

  # --- the inertness record cannot rot (#1396) ------------------------------
  dout="$(denylist_inertness "$(denylist_path)")"
  drc=$?
  if [ "$drc" -eq 0 ] && [ -z "$dout" ]; then
    printf '  OK    the seam is RECORDED as unexercised while denylisted (%s), and verify.sh still wires it\n' "$denylist_marker"
  else
    printf '  FAIL  the real denylist/verify.sh pair does not satisfy the inertness record (rc=%s)\n%s\n' "$drc" "$dout" >&2
    ok=1
  fi
  # the mutant: the same denylist with the record stripped must be refused BY NAME.
  dmutant="$work/check-denylist.no-record.txt"
  grep -vF "$denylist_marker" "$(denylist_path)" >"$dmutant"
  if cmp -s "$(denylist_path)" "$dmutant"; then
    echo "check-pr-contract: SELFTEST FAIL — the denylist mutant is byte-identical to the real record; the mutation proved nothing" >&2
    ok=1
  else
    dout="$(denylist_inertness "$dmutant")"
    drc=$?
    if [ "$drc" -ne 0 ] && [[ "$dout" == *"denylist-inertness-undeclared"* ]]; then
      printf '  OK    a denylisted seam whose record is missing is refused BY NAME; the record is load-bearing\n'
    else
      printf '  FAIL  a denylisted seam with no record was accepted (rc=%s)\n%s\n' "$drc" "$dout" >&2
      ok=1
    fi
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
  # Two named contracts, and the discriminant is the argv `--range` — never an
  # ambient variable. No range named is the repository verdict (the recorded
  # legacy applied); an explicit range is the predicate re-check, verbatim, which
  # is what every programmatic consumer of this check asks for. See "THE RECORDED
  # LEGACY, AND THE TWO CONTRACTS OF `--landed`" in the header (#1402).
  if [ "$RANGE_SET" -eq 0 ]; then
    landed_verdict "HEAD" "$gate"
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
  echo "check-pr-contract: CANNOT-ASSESS — pr-context-missing: no --pr, no --body-file/AO_PR_BODY_FILE, and no \$AO_PR_NUMBER/\$_PR_NUMBER/\$PR_NUMBER hint to fall back on; a missing body is not a pass" >&2
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

run_checks "$body_file" "$range" "$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)" "$surfaces_yaml_override"
exit $?
