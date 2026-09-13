#!/usr/bin/env bash
# check-session-isolation.sh — the lane-isolation gate (issue #263).
#
# "One issue = one lane = one branch" is only an institution if breaking it
# fails a check (no-false-green doctrine, GR-12). This gate proves three things:
#
#   * the rule is DECLARED in the canonical docs (AGENTS.md golden rule 15,
#     docs/EXECUTION-PLAN.md dispatch contract, docs/GOVERNANCE.md branch rules),
#     and removing a declaration is a named failure;
#   * the mechanism WORKS — a real lane is provisioned in a scratch repository,
#     its signature is lane-local (the shared config is left untouched), and a
#     commit authored under that signature is accepted;
#   * the audit CAN FAIL — every violation is provoked for real (missing ticket
#     trailer, wrong session signature, non-lane branch, signature leaked into
#     the shared config, worktree-scoped identity unavailable) and must be
#     detected by name. A check whose pass and fail paths collapse is a
#     formality.
#
# It also pins that the execution loop provisions through this module, so the
# isolation is applied to dispatched agents rather than only available to them.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-session-isolation.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

cli="governance/isolation/cli.py"
terminal="fleet/terminal.py"
suites="scripts/pytest-suites.txt"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-session-isolation: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "check-session-isolation: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
for required in "$cli" "$terminal" "$suites"; do
  if [ ! -f "$required" ]; then
    echo "check-session-isolation: FAIL — $required is missing" >&2
    exit 1
  fi
done

fail=0

# --- 1. the rule is declared institutionally --------------------------------
# Each entry is "file|marker|marker|...". The markers are the substance of the
# rule, not its wording: the canonical branch, the per-worktree signature, the
# environment the session runs under, and the gate that enforces it.
declare -a declarations=(
  "AGENTS.md|Session identity & lane isolation|governance/isolation|issue-<n>|git config --worktree|check-session-isolation.sh|AO_SESSION_ID"
  "docs/EXECUTION-PLAN.md|governance/isolation/cli.py open|issue-<n>|git config --worktree|AO_SESSION_ID"
  "docs/GOVERNANCE.md|governance/isolation|issue-<n>|git config --worktree|agents.invalid|check-session-isolation.sh"
)

# missing_declarations <file> <marker>... — one named finding per missing marker.
missing_declarations() {
  local file="$1" marker missing=0
  shift
  for marker in "$@"; do
    if ! grep -qF -- "$marker" "$file"; then
      printf '  FAIL  %s (missing declaration: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  return "$missing"
}

for entry in "${declarations[@]}"; do
  IFS='|' read -r -a parts <<< "$entry"
  if [ ! -f "${parts[0]}" ]; then
    echo "  FAIL  ${parts[0]} is missing" >&2
    fail=$((fail + 1))
    continue
  fi
  if missing_declarations "${parts[0]}" "${parts[@]:1}"; then
    echo "  OK    ${parts[0]} declares the lane-isolation rule"
  else
    fail=$((fail + 1))
  fi
done

# --- 2. the execution loop actually provisions through the module -----------
if grep -qF -- "governance/isolation" "$terminal"; then
  echo "  OK    $terminal provisions lanes through governance/isolation"
else
  echo "  FAIL  $terminal does not provision lanes through governance/isolation" >&2
  fail=$((fail + 1))
fi

if grep -qF -- "governance/isolation" "$suites"; then
  echo "  OK    $suites declares the governance/isolation suite"
else
  echo "  FAIL  $suites does not declare the governance/isolation suite" >&2
  fail=$((fail + 1))
fi

# --- 3. the mechanism, exercised for real in a scratch repository -----------
work="/tmp/session-isolation.$$.$(date +%s)"
if ! mkdir -p "$work/repo" 2>/dev/null; then
  echo "check-session-isolation: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

scratch="$work/repo"
lanes="$work/lanes"

git init -q -b master "$scratch" >/dev/null 2>&1 || {
  echo "check-session-isolation: CANNOT-ASSESS — cannot create a scratch repository" >&2
  exit 2
}
git -C "$scratch" config user.name "Gate Human"
git -C "$scratch" config user.email "gate-human@example.com"
echo seed > "$scratch/seed.txt"
git -C "$scratch" add seed.txt >/dev/null 2>&1
git -C "$scratch" commit -q -m seed >/dev/null 2>&1

# The scratch identity must not be picked up from the machine running the gate.
export GIT_CONFIG_GLOBAL=/dev/null

open_lane() { # open_lane <issue> <agent> <lane> — prints the lane JSON
  python3 "$cli" open --issue "$1" --agent "$2" --lane "$3" \
    --main "$scratch" --root "$lanes" --base HEAD 2>/dev/null
}

jfield() { # jfield <field> — the identity field from the lane JSON on stdin
  python3 -c 'import json,sys; print(json.load(sys.stdin)["identity"][sys.argv[1]])' "$1"
}

lane_session() { # lane_session <issue> <agent> <lane> — prints "<sid> <worktree>"
  local payload session_id worktree
  payload="$(open_lane "$1" "$2" "$3")" || return 1
  session_id="$(printf '%s' "$payload" | jfield session_id)"
  worktree="$(printf '%s' "$payload" | jfield worktree)"
  [ -n "$session_id" ] && [ -n "$worktree" ] || return 1
  printf '%s %s\n' "$session_id" "$worktree"
}

expect_ok() { # expect_ok <label> <session>
  if python3 "$cli" audit --main "$scratch" --session "$2" >/dev/null 2>&1; then
    echo "  OK    $1"
  else
    echo "  FAIL  $1 (a valid lane was reported as not isolated)" >&2
    python3 "$cli" audit --main "$scratch" --session "$2" 2>&1 | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_fail() { # expect_fail <label> <session> <expected-violation-code>
  local output rc
  output="$(python3 "$cli" audit --main "$scratch" --session "$2" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  FAIL  $1 (the violation went undetected; expected $3)" >&2
    fail=$((fail + 1))
  elif ! printf '%s' "$output" | grep -qF -- "$3"; then
    echo "  FAIL  $1 (audit failed without naming $3)" >&2
    printf '%s\n' "$output" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  else
    echo "  OK    $1 (audit refused: $3)"
  fi
}

commit_in() { # commit_in <worktree> <file> <trailer-or-empty>
  echo "$2" > "$1/$2"
  git -C "$1" add "$2" >/dev/null 2>&1
  if [ -n "$3" ]; then
    git -C "$1" commit -q -m "work on $2" -m "$3" >/dev/null 2>&1
  else
    git -C "$1" commit -q -m "work on $2" >/dev/null 2>&1
  fi
}

# Lane A — a correctly provisioned lane, with a traceable commit.
read -r a_sid a_wt < <(lane_session 263 gate-agent foundation) || {
  echo "check-session-isolation: FAIL — could not provision a lane" >&2
  exit 1
}
commit_in "$a_wt" "work.txt" "Refs kushin77/agent-orchestrator#263"
expect_ok "a provisioned lane on issue-263 is isolated" "$a_sid"

# The signature must be lane-local, and the shared config must be untouched.
stamped_email="$(git -C "$a_wt" config --worktree --get user.email 2>/dev/null)"
shared_email="$(git -C "$scratch" config --local --get user.email 2>/dev/null)"
if [ "$stamped_email" = "agent+gate-agent@agents.invalid" ] && [ "$shared_email" = "gate-human@example.com" ]; then
  echo "  OK    the lane signature is worktree-scoped and the shared config is untouched"
else
  echo "  FAIL  lane signature is '$stamped_email' and shared config is '$shared_email'" >&2
  fail=$((fail + 1))
fi

branch="$(git -C "$a_wt" symbolic-ref --short HEAD 2>/dev/null)"
if [ "$branch" = "issue-263" ]; then
  echo "  OK    the lane branch is named after the issue (issue-263)"
else
  echo "  FAIL  the lane branch is '$branch', not issue-263" >&2
  fail=$((fail + 1))
fi

author="$(git -C "$a_wt" log --format=%ae -1 2>/dev/null)"
if [ "$author" = "agent+gate-agent@agents.invalid" ]; then
  echo "  OK    the commit is authored by the session, not by a human"
else
  echo "  FAIL  the commit is authored by '$author'" >&2
  fail=$((fail + 1))
fi

# Lane B — a commit that never references its ticket.
read -r b_sid b_wt < <(lane_session 264 gate-agent foundation)
commit_in "$b_wt" "untraced.txt" ""
expect_fail "a commit without the ticket trailer is refused" "$b_sid" "commit-missing-ticket-trailer"

# A later, properly referenced commit must not repair it: the rule is history.
commit_in "$b_wt" "later.txt" "Refs kushin77/agent-orchestrator#264"
expect_fail "a later commit does not repair an untraceable one" "$b_sid" "commit-missing-ticket-trailer"

# Lane C — another session's signature inside the lane.
read -r c_sid c_wt < <(lane_session 265 gate-agent foundation)
git -C "$c_wt" config --worktree user.email "agent+someone-else@agents.invalid" >/dev/null 2>&1
expect_fail "a worktree signing as another session is refused" "$c_sid" "identity-mismatch"

# Lane D — a branch that does not name the issue.
read -r d_sid d_wt < <(lane_session 266 gate-agent foundation)
git -C "$d_wt" checkout -q -b not-a-ticket-branch >/dev/null 2>&1
expect_fail "a lane checked out off its issue branch is refused" "$d_sid" "branch-mismatch"

# Lane E — the lane's signature leaked into the shared config, where every other
# lane would inherit it. Done last-but-one: it makes the shared config agent-owned.
read -r e_sid e_wt < <(lane_session 267 leaky foundation)
git -C "$scratch" config --local user.name "agent-leaky" >/dev/null 2>&1
git -C "$scratch" config --local user.email "agent+leaky@agents.invalid" >/dev/null 2>&1
expect_fail "a signature in the shared config is refused" "$e_sid" "identity-leaked-to-shared-config"

# Lane F — per-worktree config disabled, so no lane-local identity exists. An
# inherited signature must read as ABSENT, never as valid.
read -r f_sid _f_wt < <(lane_session 268 gate-agent foundation)
git -C "$scratch" config --unset extensions.worktreeConfig >/dev/null 2>&1
expect_fail "a lane without a worktree-scoped identity is refused" "$f_sid" "identity-not-lane-local"

# --- 4. vacuity control: the declaration check must be able to fail ---------
grep -vF "Session identity & lane isolation" AGENTS.md > "$work/agents-without-the-rule.md"
IFS='|' read -r -a parts <<< "${declarations[0]}"
if missing_declarations "$work/agents-without-the-rule.md" "${parts[@]:1}" >/dev/null 2>&1; then
  echo "  FAIL  vacuity control: removing the rule from AGENTS.md went undetected" >&2
  fail=$((fail + 1))
else
  echo "  OK    vacuity control: removing the rule from AGENTS.md is detected"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-session-isolation: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-session-isolation: OK — the rule is declared, lanes are isolated, and every violation is refused"
exit 0
