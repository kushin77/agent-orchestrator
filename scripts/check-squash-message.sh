#!/usr/bin/env bash
# check-squash-message.sh — refuse a squash-merge message before it lands
# (issue #1001, child of #882/#878 lane L3).
#
# THE DEFECT THIS EXISTS FOR
#   `gh pr merge --squash` builds the landed commit message from the PR TITLE
#   and BODY (`"<title> (#N)\n\n<body>"`), not from the branch's own commit
#   trailers. Nothing checked that composed message before the merge, so a PR
#   whose body dropped the trailer paragraph landed clean on the merge queue and
#   only went red AFTER the fact, on `check-isolation-landed` — measured three
#   times in one day (7d5f6a1a/#960, f7a7a12c/#976, then b3d7144e/#996 and
#   b85cae42/#991), each recurrence blocking every open PR's `make verify`
#   until a baseline-entry PR (#836 doctrine) unblocked the queue. A baseline
#   entry records a defect; it does not prevent the next one.
#
# WHAT THIS CHECKS
#   Renders the SAME message `gh pr merge --squash` would produce for a given
#   PR (`gh pr view N --json title,body` -> "<title> (#N)\n\n<body>") and runs
#   it through the ONE shared trailer predicate
#   (`governance/isolation/trailer.py`, which itself delegates to
#   `scripts/check-pr-contract.sh` — see that module's docstring). This script
#   does not reimplement the rule: it makes a throwaway two-commit scratch git
#   repo (a base commit plus one commit carrying the rendered message) so the
#   predicate — which is commit-shaped, not string-shaped — can be asked about
#   it, then discards the scratch repo. Refuses BY NAME on:
#     * commit-missing-ticket-trailer   — no `Refs <slug>#<n>` anywhere
#     * commit-ref-only-in-subject      — the reference is in the title, not a
#       trailer paragraph
#     * commit-ref-outside-the-trailer-block — the reference exists but sits in
#       prose above the trailing trailer block
#
#   Issue #1266: 27 squash merges landed on 2026-09-18 and closed 0 issues —
#   bodies carried the reference (`Refs kushin77/agent-orchestrator#<n>`,
#   which satisfies the shared predicate above) but never GitHub's own
#   auto-close keyword, so the issue stayed open after merge and the lifecycle
#   auto-filer went on to raise `VERIFY_EVIDENCE_MISSING` against issues
#   already closed by hand (#992/#1247/#1251). The shared predicate accepts a
#   bare `Closes #<n>` line as satisfying the trailer rule (since #835), but it
#   does not REQUIRE one — `Refs` alone still passes it. This script adds that
#   second, narrower requirement on top, only for lanes shaped `issue-<n>*`:
#   the composed message's trailing trailer block must ALSO contain
#   `Closes #<n>` (or another GitHub auto-close keyword naming the same
#   number), refusing `closes-missing:<n>` otherwise. A branch not shaped
#   `issue-<n>*` (a `direct` lane) is exempt from this rule; it still needs the
#   shared predicate's plain `Refs` trailer, checked above.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/check-squash-message.sh --pr <number>
#   bash scripts/check-squash-message.sh --self-test
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

usage() {
  printf 'usage: %s --pr <number> | --self-test\n' "$0" >&2
}

pr_number=""
self_test=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pr) pr_number="${2:-}"; shift 2 ;;
    --self-test) self_test=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'check-squash-message: unknown argument %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

if [ "$self_test" -eq 0 ] && [ -z "$pr_number" ]; then
  usage
  exit 2
fi

if ! command -v git >/dev/null 2>&1; then
  echo "check-squash-message: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "check-squash-message: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -f "$root/governance/isolation/trailer.py" ]; then
  echo "check-squash-message: CANNOT-ASSESS — governance/isolation/trailer.py is missing (the shared predicate)" >&2
  exit 2
fi

# --- render the squash message, exactly as `gh pr merge --squash` composes it
render_message() { # <title> <number> <body>
  local title="$1" number="$2" body="$3"
  printf '%s (#%s)\n\n%s' "$title" "$number" "$body"
}

# --- ask the ONE shared predicate about a rendered message ------------------
# Builds a throwaway two-commit repo (a parent is required: trailer.py's
# classify_commit asks about `<sha>^..<sha>`), classifies the SECOND commit,
# then discards the scratch repo. Prints the finding code on stdout (empty
# when clean), or `PREDICATE-UNAVAILABLE:<detail>` on stdout when the shared
# predicate itself could not be run — never reported as clean.
classify_message() { # <message>
  local msg="$1" tmp
  tmp="$(mktemp -d "/tmp/ao877-squash.$(printf 'X%.0s' 1 2 3 4 5 6)" 2>/dev/null)" || {
    echo "PREDICATE-UNAVAILABLE:mktemp failed"
    return 0
  }
  (
    cd "$tmp" || exit 1
    git init -q -b main .
    git config user.email "gate@check-squash-message.invalid"
    git config user.name "check-squash-message"
    git commit -q --allow-empty -m "scratch base commit"
  ) >/dev/null 2>&1

  printf '%s' "$msg" > "$tmp/.squash-msg"
  git -C "$tmp" commit -q --allow-empty -F "$tmp/.squash-msg" >/dev/null 2>&1
  sha="$(git -C "$tmp" rev-parse HEAD 2>/dev/null)"

  if [ -z "$sha" ]; then
    rm -rf "$tmp"
    echo "PREDICATE-UNAVAILABLE:scratch commit could not be created"
    return 0
  fi

  SCRATCH_REPO="$tmp" SCRATCH_SHA="$sha" AO_REPO_ROOT="$root" python3 - <<'PY'
import os
import sys

sys.path.insert(0, os.environ["AO_REPO_ROOT"])
from governance.isolation import trailer  # noqa: E402

repo = os.environ["SCRATCH_REPO"]
sha = os.environ["SCRATCH_SHA"]
try:
    code = trailer.classify_commit(repo, sha)
except trailer.PredicateUnavailable as exc:
    print(f"PREDICATE-UNAVAILABLE:{exc}")
    raise SystemExit(0)
print(code or "")
PY
  local rc=$?
  rm -rf "$tmp"
  return "$rc"
}

# --- issue-1266: does the message's own trailing trailer block ALSO carry ----
# GitHub's auto-close keyword for THIS issue number? Mirrors the shared
# predicate's paragraph-walk-back (scripts/check-pr-contract.sh
# `commit_finding`) so "trailer block" means the same thing in both places,
# without importing that script (it is not a module) or editing it. Prints
# `closes-missing:<n>` on stdout when the block lacks the keyword, empty when
# it is present.
closes_finding() { # <message> <issue-number>
  local msg="$1" n="$2"
  MESSAGE="$msg" ISSUE_N="$n" python3 - <<'PY'
import os
import re

message = os.environ["MESSAGE"]
n = os.environ["ISSUE_N"]

lines = message.splitlines()
paragraphs = []
current = []
for line in lines:
    if line.strip():
        current.append(line)
    elif current:
        paragraphs.append(current)
        current = []
if current:
    paragraphs.append(current)

ref_re = re.compile(r"Refs:?\s+(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#[0-9]+")
closing_re = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?[ \t]+#[0-9]+", re.IGNORECASE)
separator_re = re.compile(r"^-{2,}[ \t]*$")


def is_trailer_line(line: str) -> bool:
    if ref_re.fullmatch(line.strip()):
        return True
    if line[:1] in (" ", "\t"):
        return True
    if closing_re.fullmatch(line.strip()):
        return True
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_-]*:[ \t]*\S", line))


def is_separator_line(line: str) -> bool:
    return bool(separator_re.fullmatch(line.strip()))


def paragraph_kind(paragraph):
    significant = [line for line in paragraph if not is_separator_line(line)]
    if not significant:
        return "separator"
    if all(is_trailer_line(line) for line in significant):
        return "trailer"
    return "other"


region = []
for paragraph in reversed(paragraphs):
    kind = paragraph_kind(paragraph)
    if kind == "other":
        break
    if kind == "trailer":
        region = paragraph + region

this_close_re = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?[ \t]+#" + re.escape(n) + r"\b",
    re.IGNORECASE,
)
if any(this_close_re.search(line) for line in region):
    print("")
else:
    print(f"closes-missing:{n}")
PY
}

# The lane-branch shape this rule is scoped to: `issue-<n>` or `issue-<n>-*`.
# Anything else (a `direct` lane, e.g. `fix/foo`) is exempt from the
# `Closes #<n>` requirement — it still owes the shared predicate's `Refs`.
issue_branch_number() { # <branch> -> prints <n>, empty + rc 1 if not shaped issue-<n>*
  local branch="$1"
  if [[ "$branch" =~ ^issue-([0-9]+)(-.*)?$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

report_finding() { # <finding> [label]
  local finding="$1" label="${2:-check-squash-message}"
  case "$finding" in
    PREDICATE-UNAVAILABLE:*)
      echo "$label: CANNOT-ASSESS — ${finding#PREDICATE-UNAVAILABLE:}" >&2
      return 2
      ;;
    "")
      echo "$label: OK — the rendered squash message carries the ticket reference in its trailing trailer block"
      return 0
      ;;
    commit-missing-ticket-trailer|commit-ref-only-in-subject|commit-ref-outside-the-trailer-block)
      printf '  FAIL  %s\n' "$finding" >&2
      echo "$label: NOT-OK — the rendered squash message would fail check-isolation-landed after merge" >&2
      return 1
      ;;
    closes-missing:*)
      printf '  FAIL  %s\n' "$finding" >&2
      echo "$label: NOT-OK — the head branch is an issue lane but the trailer block has no Closes #<n>, so merging would not auto-close the issue" >&2
      return 1
      ;;
    *)
      echo "$label: CANNOT-ASSESS — the shared predicate returned an unrecognised finding: $finding" >&2
      return 2
      ;;
  esac
}

# --- self-test (issue #1001): a passing fixture and a failing mutant --------
run_self_test() {
  local failures=0

  # Fixture: a well-formed body whose LAST paragraph is the trailer, exactly
  # what the PR template (issue #1001 fix 1) now defaults to.
  local good_body
  good_body="$(printf 'What changed.\n\nSome detail.\n\nRefs kushin77/agent-orchestrator#1234')"
  local good_msg
  good_msg="$(render_message "fix(thing): do the thing" "1234" "$good_body")"
  local good_finding
  good_finding="$(classify_message "$good_msg")"
  if [ -z "$good_finding" ]; then
    echo "  OK    passing fixture: rendered message accepted (trailer in the trailing block)"
  else
    echo "  FAIL  passing fixture: expected a clean verdict, got '$good_finding'" >&2
    failures=$((failures + 1))
  fi

  # Mutant 1: the trailer paragraph is dropped entirely — the defect this
  # script exists to catch, in the exact shape #960/#976/#996/#991 landed as.
  local mutant_body
  mutant_body="$(printf 'What changed.\n\nSome detail, no trailer at all.')"
  local mutant_msg
  mutant_msg="$(render_message "fix(thing): do the thing" "1234" "$mutant_body")"
  local mutant_finding
  mutant_finding="$(classify_message "$mutant_msg")"
  if [ "$mutant_finding" = "commit-missing-ticket-trailer" ]; then
    echo "  OK    failing mutant (no trailer): refused by name (commit-missing-ticket-trailer)"
  else
    echo "  FAIL  failing mutant (no trailer): expected commit-missing-ticket-trailer, got '${mutant_finding:-<clean>}'" >&2
    failures=$((failures + 1))
  fi

  # Mutant 2: the reference is only in the title (subject-only) — the other
  # shape a substring test would wrongly accept.
  local subj_body="What changed.\n\nSome detail, no trailer paragraph."
  local subj_msg
  subj_msg="$(printf 'fix(thing): do the thing (refs kushin77/agent-orchestrator#1234) (#1234)\n\n%s' "$subj_body")"
  # Force the reference into the SUBJECT line only, matching the predicate's
  # own subject regex shape (`Refs <slug>#<n>`), never in a trailer paragraph.
  subj_msg="$(printf 'Refs kushin77/agent-orchestrator#1234 (#1234)\n\n%s' "$subj_body")"
  local subj_finding
  subj_finding="$(classify_message "$subj_msg")"
  if [ "$subj_finding" = "commit-ref-only-in-subject" ]; then
    echo "  OK    failing mutant (subject-only reference): refused by name (commit-ref-only-in-subject)"
  else
    echo "  FAIL  failing mutant (subject-only reference): expected commit-ref-only-in-subject, got '${subj_finding:-<clean>}'" >&2
    failures=$((failures + 1))
  fi

  # issue #1266: an issue-lane PR whose trailer has `Refs` but no `Closes`.
  local refs_only_body
  refs_only_body="$(printf 'What changed.\n\nRefs kushin77/agent-orchestrator#42')"
  local refs_only_msg
  refs_only_msg="$(render_message "fix(thing): do the thing" "42" "$refs_only_body")"
  local refs_only_n refs_only_finding
  if refs_only_n="$(issue_branch_number "issue-42")"; then
    refs_only_finding="$(closes_finding "$refs_only_msg" "$refs_only_n")"
  else
    refs_only_finding="BRANCH-NOT-RECOGNISED"
  fi
  if [ "$refs_only_finding" = "closes-missing:42" ]; then
    echo "  OK    issue-42 branch, Refs-only body: refused by name (closes-missing:42)"
  else
    echo "  FAIL  issue-42 branch, Refs-only body: expected closes-missing:42, got '${refs_only_finding:-<clean>}'" >&2
    failures=$((failures + 1))
  fi

  # Same body, with `Closes #42` added to the trailer block -> accepted.
  local with_closes_body
  with_closes_body="$(printf 'What changed.\n\nRefs kushin77/agent-orchestrator#42\nCloses #42')"
  local with_closes_msg
  with_closes_msg="$(render_message "fix(thing): do the thing" "42" "$with_closes_body")"
  local with_closes_finding
  with_closes_finding="$(closes_finding "$with_closes_msg" "$refs_only_n")"
  if [ -z "$with_closes_finding" ]; then
    echo "  OK    issue-42 branch, body with Closes #42: accepted"
  else
    echo "  FAIL  issue-42 branch, body with Closes #42: expected clean, got '$with_closes_finding'" >&2
    failures=$((failures + 1))
  fi

  # issue #1593: the bare `Refs #<n>` form (no owner/repo slug) is accepted by
  # the shared predicate.
  local bare_refs_body
  bare_refs_body="$(printf 'What changed.\n\nSome detail.\n\nRefs #1234')"
  local bare_refs_msg bare_refs_finding
  bare_refs_msg="$(render_message "fix(thing): do the thing" "1234" "$bare_refs_body")"
  bare_refs_finding="$(classify_message "$bare_refs_msg")"
  if [ -z "$bare_refs_finding" ]; then
    echo "  OK    bare 'Refs #<n>' trailer (#1593): accepted"
  else
    echo "  FAIL  bare 'Refs #<n>' trailer (#1593): expected a clean verdict, got '$bare_refs_finding'" >&2
    failures=$((failures + 1))
  fi

  # issue #1593: `Closes: #<n>` (git's own colon trailer form) satisfies the
  # per-issue Closes check the same as the colon-less GitHub keyword form.
  local colon_closes_body
  colon_closes_body="$(printf 'What changed.\n\nRefs kushin77/agent-orchestrator#42\nCloses: #42')"
  local colon_closes_msg colon_closes_finding
  colon_closes_msg="$(render_message "fix(thing): do the thing" "42" "$colon_closes_body")"
  colon_closes_finding="$(closes_finding "$colon_closes_msg" "42")"
  if [ -z "$colon_closes_finding" ]; then
    echo "  OK    issue-42 branch, body with 'Closes: #42' (#1593): accepted"
  else
    echo "  FAIL  issue-42 branch, body with 'Closes: #42' (#1593): expected clean, got '$colon_closes_finding'" >&2
    failures=$((failures + 1))
  fi

  # A `direct` lane (branch not shaped issue-<n>*) is exempt: only Refs needed.
  if issue_branch_number "fix/foo" >/dev/null; then
    echo "  FAIL  branch fix/foo: expected NOT to match issue-<n>* shape, but it did" >&2
    failures=$((failures + 1))
  else
    echo "  OK    branch fix/foo: exempt from Closes #<n> (not an issue-<n>* lane)"
  fi

  echo ""
  if [ "$failures" -eq 0 ]; then
    echo "check-squash-message --self-test: OK — passing fixture accepted, both mutants refused by name"
    return 0
  fi
  echo "check-squash-message --self-test: NOT-OK — $failures self-test case(s) failed" >&2
  return 1
}

if [ "$self_test" -eq 1 ]; then
  run_self_test
  exit $?
fi

# --- --pr <number> mode -------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  echo "check-squash-message: CANNOT-ASSESS — gh (GitHub CLI) not found" >&2
  exit 2
fi

# REST (issue #1567): `gh pr view` is GraphQL, so this guard went blind — and with it
# EVERY merge (`merge-pr.sh` refuses CANNOT-ASSESS when the guard reaches no verdict) —
# whenever the box's shared GraphQL budget was exhausted. REST is not gated the same way
# (docs/SHELL-PATTERNS.md SP-8: read the board through REST).
repo="${AO_REPO:-$(git remote get-url origin 2>/dev/null | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
if [ -z "$repo" ]; then
  echo "check-squash-message: CANNOT-ASSESS — cannot resolve the repository for #$pr_number (set AO_REPO)" >&2
  exit 2
fi
pr_json="$(gh api "repos/$repo/pulls/$pr_number" --jq '{title, body, headRefName: .head.ref}' 2>&1)"
gh_rc=$?
if [ "$gh_rc" -ne 0 ]; then
  echo "check-squash-message: CANNOT-ASSESS — reading PR $pr_number through REST failed: $pr_json" >&2
  exit 2
fi

pr_title="$(printf '%s' "$pr_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["title"])' 2>/dev/null)"
pr_body="$(printf '%s' "$pr_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("body") or "", end="")' 2>/dev/null)"
pr_branch="$(printf '%s' "$pr_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("headRefName") or "", end="")' 2>/dev/null)"
if [ -z "$pr_title" ]; then
  echo "check-squash-message: CANNOT-ASSESS — could not parse title/body from gh pr view $pr_number" >&2
  exit 2
fi

message="$(render_message "$pr_title" "$pr_number" "$pr_body")"
finding="$(classify_message "$message")"
if [ -z "$finding" ]; then
  issue_n="$(issue_branch_number "$pr_branch")" && finding="$(closes_finding "$message" "$issue_n")"
fi
report_finding "$finding" "check-squash-message --pr $pr_number"
exit $?
