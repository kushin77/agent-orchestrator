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
#     formality;
#   * the trailer rule is POSITIONAL, not a substring test — a commit whose only
#     reference is in the subject line, and one whose reference sits in a prose
#     paragraph, are each provoked for real and must be refused by name (issue
#     #287, measured on the real commits `6d89618` and `576edce`);
#   * the predicate is SINGLE-SOURCED — the audit delegates it to the
#     PR-contract gate (issue #288) instead of carrying a second copy, and the
#     finding it quotes for a commit is the finding that gate itself prints for
#     that commit. Two implementations of one rule disagree silently.
#
# It also pins that the execution loop provisions through this module, so the
# isolation is applied to dispatched agents rather than only available to them.
#
# THE AMBIENT IDENTITY MUST NOT DECIDE THIS GATE'S VERDICT (issue #934)
#   Rule 15 tells every session to load its identity, and
#   `governance/isolation/cli.py env` emits four `GIT_*` variables alongside the
#   `AO_*` ones. Measured with git 2.53.0, those four OUTRANK `git config
#   --worktree` *and* a per-commit `git -c user.email=` override — so in a lane
#   that followed the doctrine they, not the lane's signature, author the commit.
#   Letting that reach this gate broke it two ways, and the second is the serious
#   one:
#     * its own scratch commits were authored by the ambient session, so the
#       author assertion in section 3 failed while every lane was in fact isolated;
#     * worse, `governance/isolation/audit.py` selects a lane's commits BY AUTHOR
#       ADDRESS. The provoked violations were authored by the ambient identity, so
#       the audit found nothing to refuse and four controls reported "the violation
#       went undetected". The gate was not merely red — it was BLINDED, and a gate
#       that is blind on the day it matters is worse than no gate.
#   This gate's work is not the agent's work: it provisions throwaway scratch lanes
#   and re-derives every signature from `git config --worktree`, so it needs no
#   ambient identity. The variables are removed below. That is NOT the same as
#   absorbing the defect — the contamination is re-provoked deliberately in lane I,
#   where a commit made UNDER a foreign identity in a correctly signed lane must
#   still be refused by name. The clearing removes the ambient value; the lane-I
#   provocation proves the rule still bites without it.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. A control whose report
# the audit declined to produce (its own rc 2, or a signal death) is NOT-OK for
# nothing: it is CANNOT-ASSESS, and the run says so rather than reporting FAIL
# (issue #843: a false red costs a full gate cycle and teaches re-run-until-green).
#
# Usage: bash scripts/check-session-isolation.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

# The ambient identity is removed before anything else runs (issue #934; the
# reasoning is in the header). `GIT_IDENTITY_VARS` in governance/isolation/identity.py
# is the same four names, and this gate's own lanes are signed by
# `git config --worktree` — never by the caller's environment.
unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL

cli="governance/isolation/cli.py"
terminal="fleet/terminal.py"
suites="scripts/pytest-suites.txt"
#: The one implementation of the trailing-trailer predicate (issue #288).
predicate="scripts/check-pr-contract.sh"

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
#: Controls whose report the audit declined to produce. A control that could not
#: read the report has MEASURED NOTHING: it is CANNOT-ASSESS, and the check says
#: so on exit rather than claiming the code is wrong (issue #843).
cannot=0

# --- 0. the presence rule and the verdict rule ------------------------------
# `verdict_of` maps one audit invocation onto a verdict, and it is the single
# place the two halves of a control meet:
#
#   undetected  rc 0            the violation was NOT refused — a real failure
#   unmeasured  rc 2            the audit's own CANNOT-ASSESS — no verdict
#   unmeasured  rc >= 128       the audit died by signal — no verdict
#   unnamed     rc 1, no text   refused, but the finding is not in the report
#   ok          rc 1, text met  refused and named — the control holds
#
# The presence test is computed by bash itself (no second process, no pipe).
# The previous form — `printf '%s' "$output" | grep -qF -- "$code"` under
# `set -o pipefail` — can report ABSENT for text that is PRESENT: `grep -q`
# exits on its first match, SIGPIPE then terminates the producer, and pipefail
# promotes that 141 to the status of the whole pipeline (measured 2026-09-15:
# a 150 KB report that DOES contain the string reported NO-MATCH). grep is also
# an extra process that under load can fail to start or be killed, which is the
# same false verdict by another route. Bash substring matching cannot fail that
# way, so a control that CAN be measured always is.
contains() { # contains <haystack> <needle>
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

verdict_of() { # verdict_of <rc> <output> <expected-code> -> one verdict word
  local rc="$1" output="$2" code="$3"
  if [ "$rc" -eq 0 ]; then
    printf 'undetected\n'
  elif [ "$rc" -eq 2 ] || [ "$rc" -ge 128 ]; then
    printf 'unmeasured\n'
  elif contains "$output" "$code"; then
    printf 'ok\n'
  else
    printf 'unnamed\n'
  fi
}

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

# --- 2b. one rule, one implementation ---------------------------------------
# The audit must ASK the PR-contract gate's predicate, not carry a second copy
# of it: two implementations of one rule disagree silently, and the weaker one
# is the one that lets a commit through. The equivalence control for the same
# commit follows the position controls in section 3.
trailer_module="governance/isolation/trailer.py"
if [ ! -f "$trailer_module" ] || ! grep -qF -- "$predicate" "$trailer_module"; then
  echo "  FAIL  $trailer_module does not delegate the trailer predicate to $predicate" >&2
  fail=$((fail + 1))
else
  echo "  OK    the trailer predicate is delegated to $predicate (one implementation, not two)"
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

# The gate's own lanes are throwaway scratch inside a directory the EXIT trap
# removes, so they use the RAM-backed root /tmp provides. The refusal itself
# (issue #516) is provoked separately, WITHOUT the flag — see section 3b.
open_lane() { # open_lane <issue> <agent> <lane> — prints the lane JSON
  python3 "$cli" open --issue "$1" --agent "$2" --lane "$3" \
    --main "$scratch" --root "$lanes" --base HEAD --allow-tmpfs-root 2>/dev/null
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
  # One invocation, one verdict: the audit is run once and its own tri-state is
  # honoured, so an audit that declined to assess (rc 2) or died by signal is
  # never reported as "a valid lane was reported as not isolated" (issue #843).
  local output rc
  output="$(python3 "$cli" audit --main "$scratch" --session "$2" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  OK    $1"
  elif [ "$rc" -eq 2 ] || [ "$rc" -ge 128 ]; then
    echo "  CANNOT-ASSESS  $1 (the audit exited $rc, so it produced no report to read)" >&2
    printf '%s\n' "$output" | sed 's/^/        /' >&2
    cannot=$((cannot + 1))
  else
    echo "  FAIL  $1 (a valid lane was reported as not isolated)" >&2
    printf '%s\n' "$output" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

expect_fail() { # expect_fail <label> <session> <expected-code> [<also-named-finding>]
  local output rc verdict
  output="$(python3 "$cli" audit --main "$scratch" --session "$2" 2>&1)"
  rc=$?
  verdict="$(verdict_of "$rc" "$output" "$3")"
  case "$verdict" in
    undetected)
      echo "  FAIL  $1 (the violation went undetected; expected $3)" >&2
      fail=$((fail + 1))
      ;;
    unmeasured)
      # The audit produced no report to read — rc 2 is the audit's own
      # CANNOT-ASSESS, and a signal death measured nothing either. Reporting
      # FAIL here would be a claim about the code that no measurement supports.
      echo "  CANNOT-ASSESS  $1 (the audit exited $rc, so it produced no report to read)" >&2
      printf '%s\n' "$output" | sed 's/^/        /' >&2
      cannot=$((cannot + 1))
      ;;
    unnamed)
      echo "  FAIL  $1 (audit refused but the report names no $3)" >&2
      printf '%s\n' "$output" | sed 's/^/        /' >&2
      fail=$((fail + 1))
      ;;
    *)
      if [ -n "${4:-}" ] && ! contains "$output" "$4"; then
        echo "  FAIL  $1 (audit named $3 but never quoted the predicate's own finding $4)" >&2
        printf '%s\n' "$output" | sed 's/^/        /' >&2
        fail=$((fail + 1))
      else
        echo "  OK    $1 (audit refused: $3${4:+, and named $4})"
      fi
      ;;
  esac
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

# --- 3a2. the speculative-base re-verify gate (issue #699, DG-3) ------------
# A lane blocked only by file ownership may be cut from the UPSTREAM LANE's
# branch instead of waiting for its squash-merge (speculative execution). The
# one new risk that creates is a PR whose attestation still names a stale
# base — checked here with a real upstream/downstream pair, a real squash
# landing, and the record file proven byte-identical across every read-only
# provocation (governance/isolation/speculative.py never mutates on `verify`).
sha256_of() { # sha256_of <file> — empty string if it does not exist
  [ -f "$1" ] && sha256sum "$1" | awk '{print $1}'
}

up_session="$(open_lane 645645 spec-copilot erp-upstream)" || {
  echo "  CANNOT-ASSESS  speculative-base fixture (could not open the upstream lane)" >&2
  cannot=$((cannot + 1))
}
if [ -n "${up_session:-}" ]; then
  up_sid="$(printf '%s' "$up_session" | jfield session_id)"
  up_branch="$(printf '%s' "$up_session" | jfield branch)"
  up_worktree="$(printf '%s' "$up_session" | jfield worktree)"
  commit_in "$up_worktree" erp.txt "Refs kushin77/agent-orchestrator#645645"

  down_payload="$(python3 "$cli" open --issue 671671 --agent spec-copilot --lane erp-downstream \
    --main "$scratch" --root "$lanes" --base "$up_branch" --speculative-base "$up_branch" \
    --allow-tmpfs-root 2>/dev/null)"
  down_sid="$(printf '%s' "$down_payload" | jfield session_id)"
  down_worktree="$(printf '%s' "$down_payload" | jfield worktree)"
  commit_in "$down_worktree" consumer.txt "Refs kushin77/agent-orchestrator#671671"

  record="$scratch/.fleet/lanes/speculative/$down_sid.json"
  if [ -z "$down_sid" ] || [ ! -f "$record" ]; then
    echo "  CANNOT-ASSESS  speculative-base fixture (no attestation was recorded for the downstream lane)" >&2
    cannot=$((cannot + 1))
  else
    expect_fail "speculative lane, upstream not yet landed" "$down_sid" "speculative-base-not-landed"
    before_sha="$(sha256_of "$record")"
    expect_fail "speculative lane, re-provoked (attestation must not mutate on a read-only audit)" \
      "$down_sid" "speculative-base-not-landed"
    after_sha="$(sha256_of "$record")"
    if [ "$before_sha" != "$after_sha" ]; then
      echo "  FAIL  speculative-base attestation changed across a read-only audit ($before_sha -> $after_sha)" >&2
      fail=$((fail + 1))
    else
      echo "  OK    speculative-base attestation is sha256-identical across the read-only audit ($before_sha)"
    fi

    # The upstream lane lands (squash, exactly as this repo's own landing path
    # does it), and the downstream lane pulls the now-landed master into its
    # own branch — the ordinary way a speculative lane picks up a real landing.
    git -C "$scratch" merge --squash "$up_branch" >/dev/null 2>&1
    git -C "$scratch" commit -q -m "erp integration (squash)" -m "Refs kushin77/agent-orchestrator#645645" >/dev/null 2>&1
    git -C "$down_worktree" merge -q master -m "merge landed master" >/dev/null 2>&1

    stale_before_sha="$(sha256_of "$record")"
    expect_fail "speculative lane, landed but not re-verified (stale merge_base)" \
      "$down_sid" "speculative-base-stale-merge-base"
    stale_after_sha="$(sha256_of "$record")"
    if [ "$stale_before_sha" != "$stale_after_sha" ]; then
      echo "  FAIL  speculative-base attestation changed across the stale-merge-base audit ($stale_before_sha -> $stale_after_sha)" >&2
      fail=$((fail + 1))
    else
      echo "  OK    speculative-base attestation is sha256-identical across the stale-merge-base audit"
    fi

    reverify_out="$(python3 "$cli" audit --main "$scratch" --session "$down_sid" --reverify-speculative-base 2>&1)"
    reverify_rc=$?
    if [ "$reverify_rc" -ne 0 ]; then
      echo "  FAIL  speculative lane did not become OK after re-verifying against the final merge base (rc=$reverify_rc)" >&2
      printf '%s\n' "$reverify_out" | sed 's/^/        /' >&2
      fail=$((fail + 1))
    else
      echo "  OK    speculative lane accepted once its attestation names the final merge base"
    fi
  fi
fi

# --- 3b. the tmpfs refusal (issue #516) -------------------------------------
# A lane rooted on a RAM-backed filesystem costs RAM and inodes instead of disk
# and is lost on reboot, so it must be refused BY NAME — and before
# `git worktree add`, which is why the probe root must not exist afterwards.
tmpfs_root=""
for candidate in /tmp /dev/shm /run; do
  if [ -d "$candidate" ] && [ "$(stat -f -c %T "$candidate" 2>/dev/null)" = "tmpfs" ]; then
    tmpfs_root="$candidate"
    break
  fi
done
if [ -z "$tmpfs_root" ]; then
  echo "  FAIL  no RAM-backed mount available to provoke the tmpfs refusal" >&2
  fail=$((fail + 1))
else
  probe_root="$tmpfs_root/ao-isolation-probe.$$"
  rm -rf "$probe_root"
  output="$(python3 "$cli" open --issue 999 --agent probe --lane tmpfs-probe \
    --main "$scratch" --root "$probe_root" --base HEAD 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  FAIL  a lane rooted on $tmpfs_root was provisioned anyway" >&2
    fail=$((fail + 1))
  elif [ "$rc" -ne 1 ]; then
    echo "  FAIL  the tmpfs refusal exited $rc, not NOT-OK (1)" >&2
    fail=$((fail + 1))
  elif ! contains "$output" "lane-worktree-on-tmpfs"; then
    echo "  FAIL  the tmpfs refusal did not name lane-worktree-on-tmpfs" >&2
    printf '%s\n' "$output" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  elif [ -e "$probe_root" ]; then
    echo "  FAIL  $probe_root was created anyway (the guard ran after the write)" >&2
    fail=$((fail + 1))
  else
    echo "  OK    a lane rooted on $tmpfs_root ($(stat -f -c %T "$tmpfs_root")) is refused by name, before git worktree add"
  fi
  # Vacuity control: the refusal is about the filesystem, not about this probe —
  # the same root is accepted the moment a caller says the scratch is throwaway.
  if python3 "$cli" open --issue 999 --agent probe --lane tmpfs-probe --allow-tmpfs-root \
      --main "$scratch" --root "$probe_root" --base HEAD >/dev/null 2>&1; then
    echo "  OK    vacuity control: the same root is accepted with --allow-tmpfs-root"
    git -C "$scratch" worktree remove --force "$probe_root" >/dev/null 2>&1
    rm -rf "$probe_root"
  else
    echo "  FAIL  vacuity control: the root is refused even with --allow-tmpfs-root" >&2
    fail=$((fail + 1))
  fi
fi

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

# --- 3f. the session plane: stamped by open, judged by the audit (#917) -----
# Measured 2026-09-16: `.fleet/sessions/` empty while 78 lane records existed,
# so `reconcile status` swept nothing. The mint now stamps the lane's session
# in the sweeper's own vocabulary, and a session-minted lane whose session is
# GONE (beat past the TTL AND owning process dead) is refused by name.
beat_file="$scratch/.fleet/sessions/$a_sid.json"
if [ -f "$beat_file" ]; then
  echo "  OK    open stamped the lane's session beat ($beat_file)"
else
  echo "  FAIL  open did not stamp a session beat for lane $a_sid" >&2
  fail=$((fail + 1))
fi
record_file="$scratch/.fleet/lanes/$a_sid.json"
if python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("opened_at") else 1)' "$record_file"; then
  echo "  OK    the lane record carries opened_at, so it owes a session"
else
  echo "  FAIL  the lane record carries no opened_at" >&2
  fail=$((fail + 1))
fi
rewrite_beat() { # rewrite_beat <file> <age-seconds> <pid> — age the beat and re-own it
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys, time
path, age, pid = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
beat = json.load(open(path, encoding="utf-8"))
beat["at"] = time.time() - age
beat["pid"] = pid
json.dump(beat, open(path, "w", encoding="utf-8"))
PY
}
# A dead pid: the largest pid the kernel can hand out is never this process.
dead_pid=4194303
saved_beat="$(cat "$beat_file" 2>/dev/null)"
rewrite_beat "$beat_file" 960 "$dead_pid"
expect_fail "a session-minted lane whose beat is past the TTL and whose process is dead" "$a_sid" "lane-session-gone"
rewrite_beat "$beat_file" 960 "$$"
expect_ok "vacuity control: a stale beat behind a LIVE process is suspect, not gone" "$a_sid"
rm -f "$beat_file"
expect_fail "a session-minted lane with no beat at all" "$a_sid" "lane-session-gone"
printf '%s' "$saved_beat" > "$beat_file"
# Re-opening the same lane refreshes the beat and keeps the original opened_at.
opened_before="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["opened_at"])' "$record_file")"
open_lane 263 gate-agent foundation >/dev/null || true
opened_after="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["opened_at"])' "$record_file")"
if [ -f "$beat_file" ] && [ "$opened_before" = "$opened_after" ]; then
  echo "  OK    re-opening a lane refreshes its beat and keeps opened_at ($opened_after)"
else
  echo "  FAIL  re-opening the lane lost the beat or rewrote opened_at ($opened_before -> $opened_after)" >&2
  fail=$((fail + 1))
fi
expect_ok "the re-opened lane is isolated again (its session is live)" "$a_sid"

# --- 3g. bound at creation: the runtime is validated against the registry (#1301)
output="$(python3 "$cli" open --issue 998 --agent probe --lane runtime-probe --runtime not-a-runtime \
  --main "$scratch" --root "$lanes" --base HEAD --allow-tmpfs-root 2>&1)"
rc=$?
if [ "$rc" -eq 1 ] && contains "$output" "runtime-unregistered:not-a-runtime" && [ ! -e "$lanes/ao-998-"* ]; then
  echo "  OK    an unregistered runtime is refused by name before anything is created (runtime-unregistered)"
else
  echo "  FAIL  an unregistered runtime exited $rc (expected 1, named, nothing created)" >&2
  printf '%s\n' "$output" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi
output="$(python3 "$cli" open --issue 998 --agent probe --lane runtime-probe --runtime claude-subagent --actor gate \
  --main "$scratch" --root "$lanes" --base HEAD --allow-tmpfs-root 2>/dev/null)"
rc=$?
bound_runtime="$(printf '%s' "$output" | python3 -c 'import json,sys; b=json.load(sys.stdin)["binding"]; print(b["runtime"], b["actor"], b["lane_id"])' 2>/dev/null)"
if [ "$rc" -eq 0 ] && [ "$bound_runtime" = "claude-subagent gate $(printf '%s' "$output" | jfield session_id)" ]; then
  echo "  OK    vacuity control: a registered runtime is accepted and the record is bound (runtime, actor, lane_id)"
else
  echo "  FAIL  vacuity control: a registered runtime exited $rc with binding '$bound_runtime'" >&2
  fail=$((fail + 1))
fi
runtime_sid="$(printf '%s' "$output" | jfield session_id)"
python3 - "$scratch/.fleet/lanes/$runtime_sid.json" <<'PY'
import json, sys
path = sys.argv[1]
record = json.load(open(path, encoding="utf-8"))
record["runtime"] = "deregistered-runtime"
json.dump(record, open(path, "w", encoding="utf-8"))
PY
expect_fail "a record hand-edited to name a runtime the registry lacks" "$runtime_sid" "runtime-unregistered"

# --- 3b. the audit does not read the ambient identity -----------------------
# The control that makes the clearing at the top of this file an assertion rather
# than a hope (issue #934). The same lane, audited from a shell that exports a
# FOREIGN pair, must reach the same verdict: the audit re-derives the signature
# from `git config --worktree`, so the caller's environment is not an input. If
# this ever goes red, the verdict of every lane in this gate depends on who ran it.
if env GIT_AUTHOR_NAME=agent-someone-else GIT_AUTHOR_EMAIL="agent+someone-else@agents.invalid" \
    GIT_COMMITTER_NAME=agent-someone-else GIT_COMMITTER_EMAIL="agent+someone-else@agents.invalid" \
    python3 "$cli" audit --main "$scratch" --session "$a_sid" >/dev/null 2>&1; then
  echo "  OK    the audit's verdict for a lane is independent of the ambient identity env"
else
  echo "  FAIL  the audit's verdict changed when the caller exported a foreign identity" >&2
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

# Lane G — the ticket reference is in the SUBJECT line only. The rule is
# positional: a subject is not a trailer block, so a substring test would accept
# this commit while the rule refuses it. This is the measured shape of the real
# commits `6d89618` and `576edce` (issue #287).
read -r g_sid g_wt < <(lane_session 269 gate-agent foundation)
echo "subject-only.txt" > "$g_wt/subject-only.txt"
git -C "$g_wt" add subject-only.txt >/dev/null 2>&1
git -C "$g_wt" commit -q -m "Refs kushin77/agent-orchestrator#269: the ref is only in the subject" >/dev/null 2>&1
expect_fail "a reference only in the subject line is refused" "$g_sid" \
  "commit-missing-ticket-trailer" "commit-ref-only-in-subject"

# The audit's verdict must BE the shared gate's verdict for the same commit —
# one rule with two surfaces, not two rules that happen to agree today.
g_sha="$(git -C "$g_wt" rev-parse HEAD 2>/dev/null)"
gate_out="$(bash "$predicate" --repo "$scratch" --landed --range "$g_sha^..$g_sha" --enforcement-gate "$g_sha^" 2>&1)"
if contains "$gate_out" "commit-ref-only-in-subject:${g_sha:0:12}"; then
  echo "  OK    $predicate names the same defect: commit-ref-only-in-subject:${g_sha:0:12}"
else
  echo "  FAIL  $predicate did not name commit-ref-only-in-subject:${g_sha:0:12} for the same commit" >&2
  printf '%s\n' "$gate_out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

# Lane H — the reference sits in a PROSE paragraph, not in the trailing block
# (a substring test accepts this too).
read -r h_sid h_wt < <(lane_session 270 gate-agent foundation)
echo "prose.txt" > "$h_wt/prose.txt"
git -C "$h_wt" add prose.txt >/dev/null 2>&1
git -C "$h_wt" commit -q -m "work on prose.txt" \
  -m "The change is tracked as Refs kushin77/agent-orchestrator#270 in prose." \
  -m "Co-authored-by: gate <gate@example.invalid>" >/dev/null 2>&1
expect_fail "a reference buried in prose is refused" "$h_sid" \
  "commit-missing-ticket-trailer" "commit-ref-outside-the-trailer-block"

# Lane I — the contamination the ambient identity USED to cause, provoked on
# purpose (issue #934). The lane's own signature is correct in
# `git config --worktree`; only the COMMIT was made under another session's pair,
# which is exactly the shape a shell shared between lanes produces. The message
# carries a perfectly good ticket trailer, so the trailer rule is satisfied and
# AUTHORSHIP is the only defect — if the audit reports this lane isolated, the
# contamination has been absorbed rather than trapped.
foreign_identity_email="agent+someone-else@agents.invalid"
read -r i_sid i_wt < <(lane_session 271 gate-agent foundation)
echo "contaminated.txt" > "$i_wt/contaminated.txt"
git -C "$i_wt" add contaminated.txt >/dev/null 2>&1
env GIT_AUTHOR_NAME=agent-someone-else GIT_AUTHOR_EMAIL="$foreign_identity_email" \
  GIT_COMMITTER_NAME=agent-someone-else GIT_COMMITTER_EMAIL="$foreign_identity_email" \
  git -C "$i_wt" commit -q -m "work on contaminated.txt" \
  -m "Refs kushin77/agent-orchestrator#271" >/dev/null 2>&1
expect_fail "a commit another session authored inside the lane is refused" "$i_sid" \
  "commit-authored-by-another-session"
# ...and the lane's own signature was NOT the defect, so the refusal above came
# from authorship alone and not from a config mismatch the audit already knew.
if [ "$(git -C "$i_wt" log --format=%ae -1 2>/dev/null)" = "$foreign_identity_email" ] &&
  [ "$(git -C "$i_wt" config --worktree --get user.email 2>/dev/null)" = "agent+gate-agent@agents.invalid" ]; then
  echo "  OK    the contaminated lane's worktree signature is still the session's — authorship is the only defect"
else
  echo "  FAIL  the contaminated lane does not hold the shape under test" >&2
  fail=$((fail + 1))
fi

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

# --- 3c. the verdict rule is provoked, the unmeasured case included ---------
# `verdict_of` decides whether a control can be measured at all, so it is the
# one rule here that must not be a formality: a rule that cannot report
# CANNOT-ASSESS brings the false red straight back the next time the box is
# loaded. Two of the cases below drive the REAL audit into an unmeasured exit
# rather than feeding the classifier a fabricated status.
verdict_fail=0
provoke_verdict() { # provoke_verdict <expected> <rc> <output> <code>
  local got
  got="$(verdict_of "$2" "$3" "$4")"
  if [ "$got" = "$1" ]; then
    echo "  OK    verdict(rc=$2, code=$4) is $got"
  else
    echo "  FAIL  verdict(rc=$2, code=$4) is $got, expected $1" >&2
    verdict_fail=1
  fi
}
provoke_verdict ok 1 "the report refuses and names commit-missing-ticket-trailer here" \
  "commit-missing-ticket-trailer"
provoke_verdict unnamed 1 "a report that refused for some other reason" \
  "commit-missing-ticket-trailer"
provoke_verdict undetected 0 "" "commit-missing-ticket-trailer"
provoke_verdict unmeasured 2 "audit: CANNOT-ASSESS - no lane record for session deadbeef" \
  "commit-missing-ticket-trailer"
provoke_verdict unmeasured 137 "" "commit-missing-ticket-trailer"

# Resource-starved, through the real CLI: an unreadable main is the audit's own
# CANNOT-ASSESS, and the control must read it as unmeasured, never as a FAIL.
missing_main="$work/absent-repo"
missing_out="$(python3 "$cli" audit --main "$missing_main" --session "$b_sid" 2>&1)"
missing_rc=$?
provoke_verdict unmeasured "$missing_rc" "$missing_out" "commit-missing-ticket-trailer"
if [ "$missing_rc" -ne 2 ]; then
  echo "  FAIL  the audit exited $missing_rc for an unreadable main, not CANNOT-ASSESS (2)" >&2
  verdict_fail=1
else
  echo "  OK    the real audit returns CANNOT-ASSESS for an unreadable main, and the control calls that unmeasured"
fi

# A report that died by signal measured nothing either.
killed_rc="$( { python3 -c 'import os, signal; os.kill(os.getpid(), signal.SIGKILL)' 2>&1; printf '%s' "$?"; } 2>/dev/null )"
provoke_verdict unmeasured "$killed_rc" "" "commit-missing-ticket-trailer"
if [ "$killed_rc" -lt 128 ]; then
  echo "  FAIL  a signal-killed audit exited $killed_rc, not >= 128" >&2
  verdict_fail=1
else
  echo "  OK    a signal-killed audit (rc=$killed_rc) is unmeasured, not a claim about the code"
fi

# --- 3e. the elite-rung artifacts (controls/audit/schema/live, issue #885) --
# Each new artifact is provoked for real, sha256-restore idiom as elsewhere in
# this gate (section 4's hermes-style byte-identical restore): mutate a
# SCRATCH COPY, prove the mutation is refused BY NAME, then restore and prove
# the restore is byte-identical. The real tree is never touched.

py_controls="$scratch/controls.yaml"
cp "$root/governance/isolation/controls.yaml" "$py_controls"
controls_before_sha="$(sha256sum "$py_controls" | awk '{print $1}')"

# (1) control mutated -> refused by name: drop a declared refusal code the
# surface can still emit; policy.load() must refuse it BY NAME rather than
# silently accepting a weaker declaration.
python3 - "$py_controls" <<'PY'
import sys
import yaml
path = sys.argv[1]
doc = yaml.safe_load(open(path, encoding="utf-8"))
doc["refusal_codes"] = [c for c in doc["refusal_codes"] if c != "identity-mismatch"]
yaml.safe_dump(doc, open(path, "w", encoding="utf-8"), sort_keys=False)
PY
mutant_sha="$(sha256sum "$py_controls" | awk '{print $1}')"
if [ "$mutant_sha" = "$controls_before_sha" ]; then
  echo "check-session-isolation: CANNOT-ASSESS — the controls mutation left the fixture unchanged" >&2
  exit 2
fi
control_out="$(PYTHONPATH="$root" python3 -c '
import sys
sys.path.insert(0, sys.argv[2])
from governance.isolation import policy
try:
    policy.load(sys.argv[1])
    print("NO-REFUSAL")
except policy.PolicyUnavailable as exc:
    print(f"REFUSED: {exc}")
' "$py_controls" "$root" 2>&1)"
if contains "$control_out" "REFUSED" && contains "$control_out" "identity-mismatch"; then
  echo "  OK    a controls.yaml missing a declared refusal code is refused by name (identity-mismatch)"
else
  echo "check-session-isolation: FAIL — mutating controls.yaml went undetected" >&2
  printf '%s\n' "$control_out" >&2
  fail=$((fail + 1))
fi
cp "$root/governance/isolation/controls.yaml" "$py_controls"
controls_restored_sha="$(sha256sum "$py_controls" | awk '{print $1}')"
if [ "$controls_restored_sha" != "$controls_before_sha" ]; then
  echo "check-session-isolation: CANNOT-ASSESS — the controls.yaml scratch copy did not restore byte-identical" >&2
  exit 2
fi
echo "  OK    the controls.yaml scratch copy is restored byte-identical (sha256=$controls_restored_sha)"

# (2) audit record missing -> refused: journal.last_for on a lane nothing was
# ever audited for must read as absent, never as a fabricated clean pass.
missing_journal_out="$(PYTHONPATH="$root" python3 -c '
import sys
sys.path.insert(0, sys.argv[2])
from governance.isolation import journal
entry = journal.last_for(sys.argv[1], "no-such-lane-ever-audited")
print("ABSENT" if entry is None else "PRESENT")
' "$scratch" "$root" 2>&1)"
if contains "$missing_journal_out" "ABSENT"; then
  echo "  OK    a lane journal has never been written for is reported ABSENT, not a fabricated pass"
else
  echo "check-session-isolation: FAIL — a missing audit-journal record was not reported absent" >&2
  printf '%s\n' "$missing_journal_out" >&2
  fail=$((fail + 1))
fi

# (3) schema-invalid record -> refused: a hand-crafted journal line missing a
# required field must be refused by governance/isolation/schema.py, never
# silently accepted as data.
bad_schema_out="$(PYTHONPATH="$root" python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
from governance.isolation import schema
try:
    schema.validate(schema.JOURNAL_ENTRY, {"schema": "ao.isolation/journal-entry-v1", "ok": True})
    print("NO-REFUSAL")
except schema.RecordSchemaViolation as exc:
    print(f"REFUSED: {exc}")
' "$root" 2>&1)"
if contains "$bad_schema_out" "REFUSED"; then
  echo "  OK    a schema-invalid journal record is refused by governance/isolation/schema.py"
else
  echo "check-session-isolation: FAIL — a schema-invalid journal record was not refused" >&2
  printf '%s\n' "$bad_schema_out" >&2
  fail=$((fail + 1))
fi

# (4) live feed vs real store drift -> refused: switch a real, provisioned
# lane worktree onto a foreign branch behind git's back (never through the
# CLI) and prove live.py reports the drift by name rather than trusting the
# recorded branch.
read -r live_sid live_wt < <(lane_session 887 gate-agent livefeed)
git -C "$scratch" branch drift-branch >/dev/null 2>&1
git -C "$live_wt" checkout -q drift-branch >/dev/null 2>&1
live_out="$(PYTHONPATH="$root" python3 -c '
import sys
sys.path.insert(0, sys.argv[2])
from governance.isolation import live
print(live.render(sys.argv[1]))
' "$scratch" "$root" 2>&1)"
if contains "$live_out" "DRIFT"; then
  echo "  OK    live.py detects a worktree switched off its recorded branch (DRIFT)"
else
  echo "check-session-isolation: FAIL — live.py did not detect real branch drift" >&2
  printf '%s\n' "$live_out" >&2
  fail=$((fail + 1))
fi

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
if [ "$cannot" -gt 0 ]; then
  echo "check-session-isolation: CANNOT-ASSESS ($cannot control(s) produced no report to read; not a pass and not a failure)" >&2
  exit 2
fi
if [ "$verdict_fail" -ne 0 ]; then
  echo "check-session-isolation: FAIL (the verdict rule did not hold)" >&2
  exit 1
fi
echo "check-session-isolation: OK — the rule is declared, lanes are isolated, and every violation is refused"
exit 0
