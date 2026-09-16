#!/usr/bin/env bash
# check-isolation-landed.sh — the ticket-trailer rule, enforced over LANDED history
# (issue #287).
#
# THE TWO GAPS #287 MEASURED
#   1. `governance/isolation/audit.py` implemented the history rule as a substring
#      test over the whole commit message, so a reference in the SUBJECT line — or
#      in prose — satisfied it, and a commit with no trailer paragraph at all
#      audited clean. That half is fixed and proved elsewhere: the predicate is the
#      single implementation in `scripts/check-pr-contract.sh` (#288), reached
#      through `governance/isolation/trailer.py`, and
#      `scripts/check-session-isolation.sh` provokes the subject-only and prose
#      shapes through the real audit by name. It is not re-provoked here.
#   2. Nothing audited history that had already LANDED. The audit re-derives
#      isolation from the live worktrees, and `cli.py audit --all` was wired into
#      nothing, so a landed commit that never carried its ticket reference was
#      checked by no gate — and `audit --all` reported OK on a checkout with no
#      lane records at all, which is a vacuous green: the same answer as a machine
#      where lanes were never provisioned.
#
# WHAT THIS CHECKS
#   * A. the lane audit's TRI-STATE. `cli.py audit` must be CANNOT-ASSESS (rc 2)
#     when there are no lane records, NOT-OK (rc 1) when a recorded lane breaks the
#     contract, and OK (rc 0) only when a recorded lane is genuinely isolated. All
#     three are provoked here through the real CLI, so a regression back to
#     "OK (no lanes provisioned)" fails this check instead of nobody noticing.
#   * B. real landed history, through `cli.py enforce`: the whole landed range is
#     re-checked with the one predicate, and the verdict must be OK with a non-zero
#     number of commits reachable. A run that assessed nothing cannot report OK.
#   * C. the enforcement itself CAN FAIL, provoked for real in scratch
#     repositories — an unrecorded non-compliant commit, a subject-only reference,
#     a recorded-legacy commit that is accepted, a STALE baseline entry, an entry
#     outside the assessed range, an empty range, and a missing or malformed
#     baseline. Each must be named. A check whose pass and fail paths collapse is a
#     formality (GR-12).
#
# THE DESIGN FOR PRE-EXISTING NON-COMPLIANCE, AND WHY IT CANNOT PASS VACUOUSLY
#   Measured at `9707146`: thirteen commits that landed AFTER the shared
#   predicate's enforcement boundary (`a7e73129`, PR #308) still do not carry the
#   reference in a trailing trailer block. A check that simply failed on them would
#   turn `make verify` red for every lane on the day it landed, so:
#     * the LEGACY CLASS is grandfathered by the shared predicate's own frozen
#       boundary — a boundary is provably frozen, because a new commit is always a
#       descendant and never an ancestor — and this check does not keep a second
#       copy of it;
#     * the post-boundary RESIDUE is recorded BY COMMIT in
#       `governance/isolation/landed-baseline.json`, with the finding the predicate
#       measured for each, and reported as recorded legacy on every run. It is
#       recorded, not accepted;
#     * the baseline can only SHRINK. An entry whose commit now complies is a
#       failure (`quarantine-entry-stale`); an entry outside the assessed range
#       leaves the rule unproven, and CANNOT-ASSESS is never a pass; a missing or
#       malformed baseline FAILS, because otherwise deleting the file would be a
#       way to switch the check off. Adding an entry for a newly landed
#       non-compliant commit is the defect this check exists to catch, not a fix.
#   So a genuinely bad NEW commit fails (provoked in C), historical non-compliance
#   does not red the gate (B on real history), and there is no input whose absence
#   or emptiness produces green.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# THE AMBIENT IDENTITY IS NOT AN INPUT (issue #934)
#   This gate commits into scratch lanes, and `GIT_AUTHOR_*`/`GIT_COMMITTER_*`
#   outrank `git config --worktree` (measured, git 2.53.0) — so with rule 15's
#   identity env exported, its own provoked "a commit with no ticket reference"
#   was authored by the ambient session instead of the lane session. The audit
#   selects a lane's commits BY AUTHOR ADDRESS, found none of them, and answered
#   OK where the rule requires NOT-OK: the control was blinded, not merely red.
#   The four variables are therefore removed at the top, and the contamination is
#   re-provoked as the second half of section A so the clearing cannot stand in
#   for the rule.
#
# Usage: bash scripts/check-isolation-landed.sh [--range <git-range>]
set -uo pipefail

# `GIT_IDENTITY_VARS` in governance/isolation/identity.py is the same four names.
unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

cli="governance/isolation/cli.py"
baseline="governance/isolation/landed-baseline.json"
real_range="${AO_ISOLATION_LANDED_RANGE:-HEAD}"

while [ $# -gt 0 ]; do
  case "$1" in
    --range) real_range="${2:-}"; shift 2 ;;
    -h|--help) printf 'usage: %s [--range <git-range>]\n' "$0"; exit 0 ;;
    *) printf 'check-isolation-landed: unknown argument %s\n' "$1" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-isolation-landed: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "check-isolation-landed: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
for required in "$cli" "$baseline" scripts/check-pr-contract.sh; do
  if [ ! -f "$required" ]; then
    echo "check-isolation-landed: FAIL — $required is missing" >&2
    exit 1
  fi
done

fail=0
# The machine's global git config must never leak into the scratch repositories,
# and a gate must not read the developer's global config either.
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1

work="/tmp/isolation-landed.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-isolation-landed: CANNOT-ASSESS — cannot create a scratch directory at $work" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

# --- helpers ----------------------------------------------------------------
# Bash-native containment, not a pipe (#871). `printf '%s' "$out" | grep -qF -- "$s"`
# is NOT the same test: `grep -q` exits on its first match, the producer is then
# killed by SIGPIPE while still writing, and `set -o pipefail` promotes that 141 to
# the status of the whole pipeline — so a report past the 64 KiB pipe buffer reports
# ABSENT for text that is PRESENT. In this polarity that is a FALSE RED, and it is
# not hypothetical: this file's own line below produced one in the gate of record,
# where the check printed the output as evidence with the name it claimed was
# missing visible in it twice.
contains() { # contains <haystack> <needle>
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

expect_rc() { # expect_rc <label> <want-rc> <cmd...>
  local label="$1" want="$2" out rc
  shift 2
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq "$want" ]; then
    printf '  OK    %s (rc=%s)\n' "$label" "$rc"
  else
    printf '  FAIL  %s — expected rc=%s, got rc=%s\n' "$label" "$want" "$rc" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_rc_named() { # expect_rc_named <label> <want-rc> <code> <cmd...>
  # The exit code is asserted as well as the name, deliberately: a control that
  # only asked "did it fail" would accept a CANNOT-ASSESS where the rule requires a
  # NOT-OK. Deleting the baseline, for instance, must FAIL the check — a skip is
  # not a red, so a skip there would be a way to switch the check off.
  local label="$1" want="$2" name="$3" out rc
  shift 3
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -ne "$want" ]; then
    printf '  FAIL  %s — expected rc=%s, got rc=%s\n' "$label" "$want" "$rc" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  elif ! contains "$out" "$name"; then
    printf '  FAIL  %s (rc=%s but %s was never named)\n' "$label" "$rc" "$name" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  else
    printf '  OK    %s (rc=%s, naming %s)\n' "$label" "$rc" "$name"
  fi
}

# A scratch repository whose seed commit IS the enforcement boundary, and whose
# seed carries a proper trailer so the boundary itself never produces a finding.
# Anything the cases below commit lands strictly after it, exactly like a commit
# landing after the shared predicate's boundary in the real repository.
seed_repo() { # seed_repo <dir>
  mkdir -p "$1"
  git init -q -b master "$1" >/dev/null 2>&1 || return 1
  git -C "$1" config user.name "Gate Human" >/dev/null 2>&1
  git -C "$1" config user.email "gate-human@example.invalid" >/dev/null 2>&1
  printf 'seed\n' >"$1/seed.txt"
  git -C "$1" add seed.txt >/dev/null 2>&1
  git -C "$1" commit -q -m "seed: the enforcement boundary" \
    -m "Refs kushin77/agent-orchestrator#287" >/dev/null 2>&1 || return 1
  git -C "$1" rev-parse HEAD
}

sha_of() { git -C "$1" rev-parse HEAD; }

write_baseline() { # write_baseline <path> <sha|-> [<code>] — '-' records nothing
  python3 - "$1" "$2" "${3:-commit-missing-ticket-trailer}" <<'PY'
import json, sys

path, sha, code = sys.argv[1], sys.argv[2], sys.argv[3]
entries = []
if sha != "-":
    entries.append(
        {
            "sha": sha,
            "code": code,
            "why": "provoked by scripts/check-isolation-landed.sh",
        }
    )
json.dump(
    {"measured_at": "2026-09-15", "measured_head": "" if sha == "-" else sha, "entries": entries},
    open(path, "w", encoding="utf-8"),
    indent=2,
)
PY
}

# --- A. the lane audit's tri-state (the vacuity control) ---------------------
# The defect this half closes: `audit --all` printed "OK (no lanes provisioned)"
# and exited 0 on a checkout with no lane records, so the honest answer for
# "nothing was audited" was a green. All three verdicts are provoked through the
# real CLI, in a repository the gate owns.
lanes_repo="$work/lanes-repo"
seed_repo "$lanes_repo" >/dev/null || {
  echo "check-isolation-landed: CANNOT-ASSESS — cannot create a scratch repository" >&2
  exit 2
}
lanes_root="$work/lanes"

printf '\n== the containment test at the size that broke it (#871) ==\n'
# The form this file used, `printf '%s' "$out" | grep -qF -- "$name"`, reported a name
# ABSENT in a report the size of the one this check builds on a busy box — and it did so
# in the gate of record, printing the name as evidence while claiming it was missing. The
# replacement is only a fix if it holds at that size, so it is asserted rather than assumed.
big_report="$(printf 'filler-line-%s\n' $(seq 1 30000))"
big_report="$(printf 'the-name-it-must-find\n%s\n' "$big_report")"
if contains "$big_report" 'the-name-it-must-find'; then
  printf '  OK    a name is found in a %s-byte report (past the 64 KiB pipe buffer)\n' "${#big_report}"
else
  printf '  FAIL  the containment test lost a name in a %s-byte report\n' "${#big_report}" >&2
  fail=$((fail + 1))
fi
if ! contains "$big_report" 'a-name-that-is-not-there'; then
  printf '  OK    ... and a name that is absent is still reported absent (vacuity)\n'
else
  printf '  FAIL  the containment test matched text that is not in the report\n' >&2
  fail=$((fail + 1))
fi

expect_rc_named "no lane records is CANNOT-ASSESS, never OK" \
  2 "CANNOT-ASSESS" python3 "$cli" audit --main "$lanes_repo"

open_lane() { # open_lane <issue> — prints "<session_id> <worktree>"
  local payload session_id worktree
  payload="$(python3 "$cli" open --issue "$1" --agent gate-agent --lane isolation-landed \
    --main "$lanes_repo" --root "$lanes_root" --base HEAD --allow-tmpfs-root 2>/dev/null)" || return 1
  session_id="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["identity"]["session_id"])')"
  worktree="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["identity"]["worktree"])')"
  [ -n "$session_id" ] && [ -n "$worktree" ] || return 1
  printf '%s %s\n' "$session_id" "$worktree"
}

read -r good_sid good_wt < <(open_lane 263) || {
  echo "check-isolation-landed: CANNOT-ASSESS — could not provision a scratch lane" >&2
  exit 2
}
printf 'work\n' >"$good_wt/work.txt"
git -C "$good_wt" add work.txt >/dev/null 2>&1
git -C "$good_wt" commit -q -m "work on work.txt" -m "Refs kushin77/agent-orchestrator#263" >/dev/null 2>&1
expect_rc "an isolated lane with a trailed commit is OK" 0 \
  python3 "$cli" audit --main "$lanes_repo" --session "$good_sid"

read -r bad_sid bad_wt < <(open_lane 264) || {
  echo "check-isolation-landed: CANNOT-ASSESS — could not provision a scratch lane" >&2
  exit 2
}
printf 'work\n' >"$bad_wt/work.txt"
git -C "$bad_wt" add work.txt >/dev/null 2>&1
git -C "$bad_wt" commit -q -m "work on work.txt" >/dev/null 2>&1
expect_rc_named "a recorded lane whose commit omits the trailer is NOT-OK" \
  1 "commit-missing-ticket-trailer" python3 "$cli" audit --main "$lanes_repo" --session "$bad_sid"

# A30 — the contamination, provoked rather than assumed (issue #934). The lane's
#       worktree signature is the session's; only the COMMIT was made under another
#       session's pair, which is what a shell shared between lanes produces. Its
#       message carries a proper trailer, so authorship is the only defect, and the
#       lane must NOT be reported isolated — that report is exactly what the
#       ambient env used to produce for every lane in this gate.
read -r foreign_sid foreign_wt < <(open_lane 271) || {
  echo "check-isolation-landed: CANNOT-ASSESS — could not provision a scratch lane" >&2
  exit 2
}
foreign_email="agent+someone-else@agents.invalid"
printf 'work\n' >"$foreign_wt/work.txt"
git -C "$foreign_wt" add work.txt >/dev/null 2>&1
env GIT_AUTHOR_NAME=agent-someone-else GIT_AUTHOR_EMAIL="$foreign_email" \
  GIT_COMMITTER_NAME=agent-someone-else GIT_COMMITTER_EMAIL="$foreign_email" \
  git -C "$foreign_wt" commit -q -m "work on work.txt" \
  -m "Refs kushin77/agent-orchestrator#271" >/dev/null 2>&1
if [ "$(git -C "$foreign_wt" config --worktree --get user.email 2>/dev/null)" = "agent+gate-agent@agents.invalid" ] &&
  [ "$(git -C "$foreign_wt" log --format=%ae -1 2>/dev/null)" = "$foreign_email" ]; then
  printf '  OK    the contamination under test holds: session signature, foreign author\n'
else
  printf '  FAIL  the contamination under test does not hold (signature or author is wrong)\n' >&2
  fail=$((fail + 1))
fi
expect_rc_named "a commit another session authored inside the lane is NOT-OK" \
  1 "commit-authored-by-another-session" python3 "$cli" audit --main "$lanes_repo" --session "$foreign_sid"

# --- B. real landed history, through the enforcement surface -----------------
real_out="$(python3 "$cli" enforce --main "$root" --range "$real_range" 2>&1)"
real_rc=$?
printf '%s\n' "$real_out" | sed 's/^/        /'
if [ "$real_rc" -eq 0 ]; then
  landed_re='INFO  [1-9][0-9]* non-merge commit\(s\) reachable'
  if [[ $real_out =~ $landed_re ]]; then
    printf '  OK    real landed history is enforced over %s with a non-zero commit count\n' "$real_range"
  else
    printf '  FAIL  the landed enforcement reported OK without assessing a single commit\n' >&2
    fail=$((fail + 1))
  fi
elif [ "$real_rc" -eq 2 ]; then
  printf '  SKIP  the landed enforcement could not assess %s (CANNOT-ASSESS is not a pass)\n' "$real_range"
  real_cannot_assess=1
else
  printf '  FAIL  the landed enforcement refused real history in %s\n' "$real_range" >&2
  fail=$((fail + 1))
fi

# --- C. the enforcement can fail, provoked for real --------------------------
# Each case is a scratch repository whose seed commit is the boundary, so the
# range `seed..HEAD` contains exactly the commits under test.

# C1 — a commit with no ticket reference at all, and nothing recorded.
c1="$work/c1"
c1_base="$(seed_repo "$c1")"
printf 'one\n' >"$c1/one.txt"
git -C "$c1" add one.txt >/dev/null 2>&1
git -C "$c1" commit -q -m "a commit that never references its ticket" >/dev/null 2>&1
write_baseline "$work/c1.json" -
expect_rc_named "an unrecorded non-compliant landed commit is refused" \
  1 "commit-missing-ticket-trailer" python3 "$cli" enforce --main "$c1" \
  --range "$c1_base..HEAD" --gate "$c1_base" --baseline "$work/c1.json"

# C1b — the reference in the SUBJECT only. A substring test accepts this; the
#       positional predicate must not, and this check must not either.
c1b="$work/c1b"
c1b_base="$(seed_repo "$c1b")"
printf 'one\n' >"$c1b/one.txt"
git -C "$c1b" add one.txt >/dev/null 2>&1
git -C "$c1b" commit -q -m "Refs kushin77/agent-orchestrator#287: the ref is only in the subject" >/dev/null 2>&1
expect_rc_named "a subject-only reference is refused by the shared predicate" \
  1 "commit-ref-only-in-subject" python3 "$cli" enforce --main "$c1b" \
  --range "$c1b_base..HEAD" --gate "$c1b_base" --baseline "$work/c1.json"

# C2 (vacuity control for C1) — the SAME repository shape with a real trailer
#      paragraph is accepted. Without this control, C1 would also pass if the
#      enforcement simply refused everything.
c2="$work/c2"
c2_base="$(seed_repo "$c2")"
printf 'one\n' >"$c2/one.txt"
git -C "$c2" add one.txt >/dev/null 2>&1
git -C "$c2" commit -q -m "a commit that carries its trailer" \
  -m "Refs kushin77/agent-orchestrator#287" >/dev/null 2>&1
expect_rc "a really trailed commit in the same shape is accepted" 0 \
  python3 "$cli" enforce --main "$c2" --range "$c2_base..HEAD" --gate "$c2_base" \
  --baseline "$work/c1.json"

# C3 — recorded legacy is what makes the difference: the C1 commit, recorded,
#      passes AND is reported (a check that silently ignored recorded commits
#      would be a check that accepts anything).
c3="$work/c3"
c3_base="$(seed_repo "$c3")"
printf 'one\n' >"$c3/one.txt"
git -C "$c3" add one.txt >/dev/null 2>&1
git -C "$c3" commit -q -m "a commit that never references its ticket" >/dev/null 2>&1
write_baseline "$work/c3.json" "$(sha_of "$c3")"
c3_out="$(python3 "$cli" enforce --main "$c3" --range "$c3_base..HEAD" --gate "$c3_base" \
  --baseline "$work/c3.json" 2>&1)"
if [ $? -eq 0 ] && contains "$c3_out" "recorded legacy"; then
  printf '  OK    a recorded legacy commit is accepted and reported as recorded\n'
else
  printf '  FAIL  a recorded legacy commit was not accepted and reported\n' >&2
  printf '%s\n' "$c3_out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

# C4 — a recorded entry that now COMPLIES is stale, and staleness is a failure:
#      the baseline can only shrink.
c4="$work/c4"
c4_base="$(seed_repo "$c4")"
printf 'one\n' >"$c4/one.txt"
git -C "$c4" add one.txt >/dev/null 2>&1
git -C "$c4" commit -q -m "a commit that carries its trailer" \
  -m "Refs kushin77/agent-orchestrator#287" >/dev/null 2>&1
write_baseline "$work/c4.json" "$(sha_of "$c4")"
expect_rc_named "a baseline entry whose commit now complies is stale" \
  1 "quarantine-entry-stale" python3 "$cli" enforce --main "$c4" \
  --range "$c4_base..HEAD" --gate "$c4_base" --baseline "$work/c4.json"

# C5 — a recorded entry outside the assessed range leaves the baseline
#      unreconciled: CANNOT-ASSESS, never a pass (the entry is real, and the
#      check refuses to claim a rule it could not reconcile).
write_baseline "$work/c5.json" "$c4_base"
expect_rc_named "a baseline entry outside the range is not assessed" \
  2 "quarantine-entry-not-assessed" python3 "$cli" enforce --main "$c4" \
  --range "$c4_base..HEAD" --gate "$c4_base" --baseline "$work/c5.json"

# C6 — an empty range. Nothing assessed is not a pass.
expect_rc_named "an empty landed range is CANNOT-ASSESS" \
  2 "no non-merge commit" python3 "$cli" enforce --main "$c4" \
  --range "HEAD..HEAD" --gate "$c4_base" --baseline "$work/c1.json"

# C7 — a missing baseline FAILS: otherwise deleting the file switches the check
#      off, and a disabled check that reports green is worse than no check.
expect_rc_named "a missing baseline is refused, not skipped" \
  1 "baseline-missing" python3 "$cli" enforce --main "$c2" \
  --range "$c2_base..HEAD" --gate "$c2_base" --baseline "$work/absent.json"

# C8 — a malformed baseline is refused too.
printf 'not json at all\n' >"$work/malformed.json"
expect_rc_named "a malformed baseline is refused" \
  1 "baseline-malformed" python3 "$cli" enforce --main "$c2" \
  --range "$c2_base..HEAD" --gate "$c2_base" --baseline "$work/malformed.json"

# C9 — a CLEAN commit whose enforcement boundary carries no trailer is still OK.
#      This is not decoration: the predicate prints the boundary argument it was
#      given, so the boundary finding's commit field can be a rev expression
#      ("<sha>^") rather than a hex id, and an over-strict parser reads that as
#      "unparseable", reports the rule unproven, and turns a genuinely clean
#      commit into CANNOT-ASSESS. That regression was measured while building
#      this check, and this control is what would have caught it.
c9="$work/c9"
mkdir -p "$c9"
git init -q -b master "$c9" >/dev/null 2>&1
git -C "$c9" config user.name "Gate Human" >/dev/null 2>&1
git -C "$c9" config user.email "gate-human@example.invalid" >/dev/null 2>&1
printf 'seed\n' >"$c9/seed.txt"
git -C "$c9" add seed.txt >/dev/null 2>&1
git -C "$c9" commit -q -m "a boundary with no trailer of its own" >/dev/null 2>&1
printf 'one\n' >"$c9/one.txt"
git -C "$c9" add one.txt >/dev/null 2>&1
git -C "$c9" commit -q -m "a commit that carries its trailer" \
  -m "Refs kushin77/agent-orchestrator#287" >/dev/null 2>&1
expect_rc "a clean commit whose boundary lacks a trailer is still OK" 0 \
  python3 "$cli" landed --main "$c9" --commit "$(sha_of "$c9")"

if [ "$fail" -gt 0 ]; then
  echo "check-isolation-landed: FAIL ($fail violation(s))" >&2
  exit 1
fi
if [ "${real_cannot_assess:-0}" -eq 1 ]; then
  echo "check-isolation-landed: CANNOT-ASSESS — the lane tri-state and every provoked control passed, but real landed history in $real_range could not be assessed" >&2
  exit 2
fi
echo "check-isolation-landed: OK — the lane audit's tri-state is enforced, real landed history in $real_range is enforced against recorded legacy, and every provoked violation is refused"
exit 0
