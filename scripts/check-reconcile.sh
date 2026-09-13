#!/usr/bin/env bash
# check-reconcile.sh — the session-reconciliation gate (issue #304).
#
# A worker that removes worktrees and deletes branches is only an institution if
# its *refusals* are as real as its actions. This gate therefore proves the
# destructive half against a real git repository — a scratch repo with a real bare
# remote and real linked worktrees — and requires each of the sweep's five
# outcomes to be provoked:
#
#   * heartbeats advance while a session runs, and a killed process is flagged;
#   * `preserved_on_main` / `preserved_remotely` really discriminate, since the
#     whole teardown decision rests on them;
#   * an orphan whose work exists nowhere else is SHELVED and its worktree
#     SURVIVES (the load-bearing negative — this is the work the rule protects);
#   * a landed lane's worktree is REMOVED and disappears from `git worktree list`;
#   * a step that cannot complete reports FAILED, never a silent success;
#   * a dry run decides (reclaimed / parked / reported) without acting.
#
# The bookkeeping steps against the repo's own CLIs are covered by the unit suite
# (`governance/reconcile/tests`), which asserts them on the happy path; this gate
# covers what a scratch repository cannot fake — the git mechanics and the
# decision that decides whether work survives.
#
# No network is used or required (the bare "remote" is a local directory).
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-reconcile.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

cli="governance/reconcile/cli.py"
terminal="fleet/terminal.py"
suites="scripts/pytest-suites.txt"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-reconcile: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "check-reconcile: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
for required in "$cli" "$terminal" "$suites" "governance/reconcile/README.md"; do
  if [ ! -f "$required" ]; then
    echo "check-reconcile: FAIL — $required is missing" >&2
    exit 1
  fi
done

fail=0

# --- 1. the rule is declared institutionally --------------------------------
declare -a declarations=(
  "AGENTS.md|Orphan reconciliation|governance/reconcile|heartbeat|shelved|unmerged"
  "docs/GOVERNANCE.md|governance/reconcile|heartbeat|shelved|unmerged"
)

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
    echo "  OK    ${parts[0]} declares the reconciliation rule"
  else
    fail=$((fail + 1))
  fi
done

# --- 2. the execution loop beats a per-session heartbeat --------------------
if grep -qF -- "governance.reconcile" "$terminal" && grep -qF -- "SessionBeater" "$terminal"; then
  echo "  OK    $terminal beats a session heartbeat through governance/reconcile"
else
  echo "  FAIL  $terminal does not beat a session heartbeat through governance/reconcile" >&2
  fail=$((fail + 1))
fi

if grep -qF -- "governance/reconcile" "$suites"; then
  echo "  OK    $suites declares the governance/reconcile suite"
else
  echo "  FAIL  $suites does not declare the governance/reconcile suite" >&2
  fail=$((fail + 1))
fi

# --- 3. the mechanism, exercised for real ----------------------------------
work="/tmp/reconcile.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-reconcile: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

if python3 - "$root" "$work" <<'PY'
"""Live proofs for the reconciliation worker (issue #304)."""
import os
import subprocess
import sys
import time
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2])
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from governance.reconcile.heartbeat import ORPHAN, SUSPECT, judge, read, stamp
from governance.reconcile.sweep import (
    FAILED_OUTCOME,
    PARKED,
    RECLAIMED,
    REPORTED,
    SHELVED_OUTCOME,
    RepoOps,
    sweep,
)

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}")
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr)


def git(cwd, *args):
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()[-200:]}")
    return result.stdout.strip()


# --- a scratch repository with a real (local) origin -------------------------
origin = work / "origin.git"
repo = work / "repo"
repo.mkdir(parents=True)
subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
git(repo, "config", "user.name", "Gate Human")
git(repo, "config", "user.email", "gate-human@example.com")
(repo / "seed.txt").write_text("seed\n", encoding="utf-8")
git(repo, "add", "seed.txt")
git(repo, "commit", "-q", "-m", "seed")
git(repo, "remote", "add", "origin", str(origin))
git(repo, "push", "-q", "-u", "origin", "master")
git(repo, "fetch", "-q", "origin")

ops = RepoOps(repo)

# --- 1. a heartbeat advances while the session runs -------------------------
stamp("live-1", issue=1, agent="a", root=repo, worktree="/none", branch="b")
first = read("live-1", repo)
time.sleep(0.01)
stamp("live-1", issue=1, agent="a", root=repo, worktree="/none", branch="b")
second = read("live-1", repo)
check("a heartbeat advances while the session runs", second.at > first.at)

# --- 2. a killed process is flagged ----------------------------------------
child = subprocess.Popen(["sleep", "30"])
stamp("killed-1", issue=2, agent="b", root=repo, pid=child.pid, worktree="/none", branch="b")
child.kill()
child.wait()
time.sleep(0.05)
killed = read("killed-1", repo)
verdict = judge(killed, 15, alive=False)
check("a killed process is flagged as suspect, not reclaimed on absence alone",
      verdict.status == SUSPECT, verdict.reason)
verdict = judge(killed, 15, at=killed.at + 16 * 60, alive=False)
check("a killed process past the TTL is an orphan", verdict.status == ORPHAN, verdict.reason)

# --- 3. the preservation signals really discriminate ------------------------
landed = work / "lane-landed"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "lane-landed",
                str(landed), "origin/master"], check=True)
check("a lane at origin/master is preserved_on_main", ops.preserved_on_main(str(landed)))

at_risk = work / "lane-at-risk"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "lane-at-risk",
                str(at_risk), "origin/master"], check=True)
(at_risk / "work.txt").write_text("unmerged work\n", encoding="utf-8")
git(at_risk, "add", "work.txt")
git(at_risk, "commit", "-q", "-m", "unmerged")
check("a lane with an unpushed commit is not preserved_on_main",
      not ops.preserved_on_main(str(at_risk)))
check("a lane with an unpushed commit is not preserved_remotely",
      not ops.preserved_remotely(str(at_risk), "lane-at-risk"))

# --- 4. an orphan whose work exists nowhere else SURVIVES the sweep ---------
stamp("shelter-1", issue=3, agent="c", root=repo, worktree=str(at_risk), branch="lane-at-risk",
      at=time.time() - 20 * 60)
report = sweep(repo, ttl_minutes=15, apply=True, ops=ops)
outcomes = {action.session_id: action.outcome for action in report.actions}
check("an orphan with unmerged work is SHELVED", outcomes.get("shelter-1") == SHELVED_OUTCOME,
      str(outcomes))
check("an orphan with unmerged work keeps its worktree", at_risk.exists() and "lane-at-risk" in
      git(repo, "worktree", "list"), "the worktree was removed")
check("an orphan with unmerged work keeps its branch",
      "lane-at-risk" in git(repo, "branch", "--list", "lane-at-risk"))

# --- 5. a landed lane is reclaimed and disappears from the worktree list ----
stamp("landed-1", issue=4, agent="d", root=repo, worktree=str(landed), branch="lane-landed",
      at=time.time() - 20 * 60)
landed_report = sweep(repo, ttl_minutes=15, apply=True, ops=ops)
action = next(a for a in landed_report.actions if a.session_id == "landed-1")
check("a landed lane's worktree is removed", not landed.exists(), "the worktree survived")
check("a landed lane disappears from `git worktree list`",
      "lane-landed" not in git(repo, "worktree", "list"))
check("a step that cannot complete is reported FAILED, never a silent success",
      action.outcome == FAILED_OUTCOME and any(s.outcome == "failed" for s in action.steps),
      f"outcome={action.outcome}")

# --- 6. a parked lane: the worktree goes, the remote branch stays -----------
# This lane's work is pushed, so it is preserved *remotely* and nothing else: the
# sweep may reclaim the disk but must not delete that branch, which is the work.
parked_lane = work / "lane-parked"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "lane-parked",
                str(parked_lane), "origin/master"], check=True)
(parked_lane / "p.txt").write_text("pushed work\n", encoding="utf-8")
git(parked_lane, "add", "p.txt")
git(parked_lane, "commit", "-q", "-m", "pushed")
git(parked_lane, "push", "-q", "-u", "origin", "lane-parked")
git(repo, "fetch", "-q", "origin")
stamp("parked-1", issue=8, agent="h", root=repo, worktree=str(parked_lane), branch="lane-parked",
      at=time.time() - 20 * 60)

# A dry run decides every case and acts on none of them. It is also how the
# parked decision is observed: in a scratch repository the bookkeeping steps
# (forget-lane, release-claim) have no `governance/` of their own to call, so an
# applied pass reports FAILED while still doing the git work — which is asserted
# separately, and is itself the never-a-silent-success property.
dry_lane = work / "lane-dry"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "lane-dry",
                str(dry_lane), "origin/master"], check=True)
stamp("dry-1", issue=5, agent="e", root=repo, worktree=str(dry_lane), branch="lane-dry",
      at=time.time() - 20 * 60)
dry = sweep(repo, ttl_minutes=15, apply=False, ops=ops)
dry_outcomes = {a.session_id: a.outcome for a in dry.actions}
check("a dry run reports a parked lane as parked",
      dry_outcomes.get("parked-1") == PARKED, str(dry_outcomes))
check("a dry run reports a landed lane as reclaimed",
      dry_outcomes.get("dry-1") == RECLAIMED, str(dry_outcomes))
check("a dry run acts on nothing",
      dry_lane.exists() and parked_lane.exists() and at_risk.exists())

stamp("parked-2", issue=9, agent="i", root=repo, worktree=str(parked_lane), branch="lane-parked",
      at=time.time() - 20 * 60)
applied = sweep(repo, ttl_minutes=15, apply=True, ops=ops)
check("a parked lane's worktree is reclaimed but its remote branch survives",
      not parked_lane.exists() and bool(git(repo, "ls-remote", "--heads", "origin", "lane-parked")))
check("a parked lane's removed worktree is gone from the worktree list",
      "lane-parked" not in git(repo, "worktree", "list"))

stamp("live-2", issue=6, agent="f", root=repo, worktree="/none", branch="b")
live = sweep(repo, ttl_minutes=15, apply=False, ops=ops, alive={"live-2": True})
check("a live session is reported, not acted on",
      any(a.outcome == REPORTED and a.session_id == "live-2" for a in live.actions),
      str({a.session_id: a.status for a in live.actions}))

# Every outcome the worker can produce must be observed by this gate, not
# asserted from a list: the union of the reports above is the vocabulary.
observed = {a.outcome for r in (report, landed_report, dry, applied, live) for a in r.actions}
check("every outcome the worker can produce is provoked by this gate",
      observed == {RECLAIMED, PARKED, SHELVED_OUTCOME, REPORTED, FAILED_OUTCOME}, str(sorted(observed)))

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
PY
then
  :
else
  fail=$((fail + 1))
fi

# --- 4. vacuity control: the declaration check must be able to fail ---------
grep -vF "Orphan reconciliation" AGENTS.md > "$work/agents-without-the-rule.md"
IFS='|' read -r -a parts <<< "${declarations[0]}"
if missing_declarations "$work/agents-without-the-rule.md" "${parts[@]:1}" >/dev/null 2>&1; then
  echo "  FAIL  vacuity control: removing the rule from AGENTS.md went undetected" >&2
  fail=$((fail + 1))
else
  echo "  OK    vacuity control: removing the rule from AGENTS.md is detected"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-reconcile: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-reconcile: OK — heartbeats beat, orphans are flagged, landed lanes are reclaimed, and unmerged work is never destroyed"
exit 0
