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
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage:
#   bash scripts/check-squash-message.sh --pr <number>
#   bash scripts/check-squash-message.sh --self-test
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
  tmp="$(mktemp -d 2>/dev/null)" || {
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

pr_json="$(gh pr view "$pr_number" --json title,body 2>&1)"
gh_rc=$?
if [ "$gh_rc" -ne 0 ]; then
  echo "check-squash-message: CANNOT-ASSESS — gh pr view $pr_number failed: $pr_json" >&2
  exit 2
fi

pr_title="$(printf '%s' "$pr_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["title"])' 2>/dev/null)"
pr_body="$(printf '%s' "$pr_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("body") or "", end="")' 2>/dev/null)"
if [ -z "$pr_title" ]; then
  echo "check-squash-message: CANNOT-ASSESS — could not parse title/body from gh pr view $pr_number" >&2
  exit 2
fi

message="$(render_message "$pr_title" "$pr_number" "$pr_body")"
finding="$(classify_message "$message")"
report_finding "$finding" "check-squash-message --pr $pr_number"
exit $?
