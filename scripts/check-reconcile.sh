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
# It then proves the other half (issue #628): the worktree/branch **audit**, whose
# whole job is to name what no record explains. The defect it fixes was a wrong
# OK — `sweep` saw only sessions that beat, so a lane that left no heartbeat was
# invisible and `status` reported zero orphans while 41 closed issues' worktrees
# sat on disk. So the audit is proven two ways, and the second is the one that
# matters:
#
#   * an artifact nothing explains is NAMED, and an artifact explained by a beat,
#     by a claim record or by the landing history is not — with the evidence
#     removed again to prove each match is what did the explaining;
#   * a corrupt beat makes the verdict CANNOT-ASSESS, never OK — an audit that
#     cannot tell "nothing is there" from "I could not look" is the defect.
#
# The audit removes nothing, and this gate asserts that too: after it has named an
# unmatched worktree, the worktree and its work are still exactly where they were.
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

# --- 4. the worktree/branch audit, exercised for real (issue #628) ----------
#
# The provoke-and-observe shape is deliberate. It is not enough that a matching
# artifact is un-refused: the evidence is then REMOVED and the same artifact must
# become refused, which is what proves the beat / the claim record / the landing
# journal was what did the explaining. And the load-bearing control is the last
# one — an audit that cannot read the state must say CANNOT-ASSESS, because the
# defect it fixes was reporting OK.
if python3 - "$root" "$work" <<'PYAUDIT'
"""Live proofs for the worktree/branch audit (issue #628)."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2])
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from governance.reconcile.audit import audit
from governance.reconcile.heartbeat import clear, stamp
from governance.reconcile.sweep import RepoOps

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


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def names(report):
    return {item.artifact.name for item in report.unmatched}


# A scratch repository of its own, so the audit's verdict is not entangled with
# the teardown proofs above: those leave worktrees, branches and beats behind.
origin = work / "audit-origin.git"
repo = work / "audit-repo"
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

# --- an artifact each record DOES explain, one per source -------------------
beaten = work / "audit-lane-beaten"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "issue-905",
                str(beaten), "origin/master"], check=True)
stamp("audit-beaten", issue=905, agent="gate-agent", root=repo, worktree=str(beaten),
      branch="issue-905", at=datetime.now(timezone.utc).timestamp())

(repo / ".board").mkdir(exist_ok=True)
(repo / ".board" / "claims.jsonl").write_text(
    json.dumps({"event": "claim", "issue": 901, "agent": "gate-agent", "at": now_iso(),
                "lane": "gate", "reason": "next-in-milestone"}) + "\n",
    encoding="utf-8",
)
git(repo, "branch", "issue-901")

(repo / ".fleet" / "lifecycle").mkdir(parents=True, exist_ok=True)
(repo / ".fleet" / "lifecycle" / "902.json").write_text(
    json.dumps({"closing_evidence": True}) + "\n", encoding="utf-8"
)
git(repo, "branch", "issue-902")

# --- the provoked negatives: an artifact NO record explains -----------------
orphan = work / "audit-lane-orphan"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "orphan-branch",
                str(orphan), "origin/master"], check=True)
(orphan / "unmerged.txt").write_text("unmerged work\n", encoding="utf-8")
git(orphan, "add", "unmerged.txt")
git(orphan, "commit", "-q", "-m", "unmerged")
git(repo, "branch", "issue-903")

report = audit(repo, ops=ops)
refused = names(report)
check("the audit is assessable against a real repository", report.assessable, report.reason)
check("an unmatched worktree is refused by name", str(orphan) in refused, str(sorted(refused)))
check("an unmatched issue-* branch is refused by name", "issue-903" in refused, str(sorted(refused)))
check("the audit is NOT-OK (1) while an artifact is unexplained", report.exit_code == 1, str(report.exit_code))
check("a session beat explains its worktree, so it is not refused",
      str(beaten) not in refused, str(sorted(refused)))
check("a claim record explains a branch, so it is not refused",
      "issue-901" not in refused, str(sorted(refused)))
check("the landing history explains a branch, so it is not refused",
      "issue-902" not in refused, str(sorted(refused)))
check("the primary checkout is exempt, not refused",
      len(report.exempt) == 1 and report.exempt[0].artifact.name == str(repo)
      and str(repo) not in refused,
      f"exempt={[item.artifact.name for item in report.exempt]} refused={sorted(refused)}")

# --- the evidence is what does the explaining: remove it, observe the verdict
clear("audit-beaten", repo)
(repo / ".board" / "claims.jsonl").unlink()
(repo / ".fleet" / "lifecycle" / "902.json").unlink()
after = audit(repo, ops=ops)
after_refused = names(after)
check("removing the beat makes that worktree refused (the beat was doing the explaining)",
      str(beaten) in after_refused, str(sorted(after_refused)))
check("removing the claim record makes that branch refused (the claim was doing the explaining)",
      "issue-901" in after_refused, str(sorted(after_refused)))
check("removing the landing journal makes that branch refused (the journal was doing the explaining)",
      "issue-902" in after_refused, str(sorted(after_refused)))

# --- the load-bearing negative: unreadable state is CANNOT-ASSESS, not OK ---
sessions = repo / ".fleet" / "sessions"
sessions.mkdir(parents=True, exist_ok=True)
(sessions / "corrupt-beat.json").write_text("{ not json", encoding="utf-8")
broken = audit(repo, ops=ops)
check("a corrupt session beat is CANNOT-ASSESS, never a clean OK",
      (not broken.assessable) and broken.exit_code == 2, broken.reason)
check("the CANNOT-ASSESS verdict names the unreadable beat",
      "corrupt-beat.json" in broken.reason, broken.reason)
(sessions / "corrupt-beat.json").unlink()

not_a_repo = work / "audit-not-a-repo"
not_a_repo.mkdir()
blind = audit(not_a_repo, ops=RepoOps(not_a_repo))
check("a directory that is not a repository is CANNOT-ASSESS, never a clean OK",
      (not blind.assessable) and blind.exit_code == 2 and not blind.unmatched,
      f"exit={blind.exit_code} reason={blind.reason}")

# --- the command's own tri-state, end to end --------------------------------
# `status --disk` rather than a verb of its own: a new CLI verb is a surface the
# control-plane verb registry gates (contract-first), and that contract is its
# own lane's. The audit removes nothing, so it is a flag on the report that
# already exists.
cli = repo_root / "governance" / "reconcile" / "cli.py"
not_ok = subprocess.run([sys.executable, str(cli), "--root", str(repo), "status", "--disk"],
                        capture_output=True, text=True)
check("`status --disk` exits 1 (NOT-OK) and names the unmatched worktree on stderr",
      not_ok.returncode == 1 and str(orphan) in not_ok.stderr,
      f"rc={not_ok.returncode} stderr={not_ok.stderr.strip()[-200:]}")
check("`status --disk` names the unmatched branch on stderr too",
      "issue-903" in not_ok.stderr, not_ok.stderr.strip()[-200:])
blind_cli = subprocess.run([sys.executable, str(cli), "--root", str(not_a_repo), "status", "--disk"],
                           capture_output=True, text=True)
check("`status --disk` exits 2 (CANNOT-ASSESS), never 0, when it cannot look",
      blind_cli.returncode == 2, f"rc={blind_cli.returncode}")
plain = subprocess.run([sys.executable, str(cli), "--root", str(not_a_repo), "status"],
                       capture_output=True, text=True)
check("plain `status` is unchanged: rc 0 with no beats and no orphans",
      plain.returncode == 0, f"rc={plain.returncode} out={plain.stdout.strip()[-120:]}")

# --- it reports; it never removes -------------------------------------------
check("the audit removed nothing: the refused worktree and its unmerged work survive",
      orphan.exists()
      and (orphan / "unmerged.txt").read_text(encoding="utf-8") == "unmerged work\n"
      and "orphan-branch" in git(repo, "branch", "--list", "orphan-branch")
      and str(orphan) in git(repo, "worktree", "list"),
      "the worktree or its work was removed")
check("the audit removed nothing: every branch it examined still exists",
      all(name in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
          for name in ("issue-901", "issue-902", "issue-903")),
      git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads"))

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
PYAUDIT
then
  :
else
  fail=$((fail + 1))
fi

# --- 5. vacuity control: the declaration check must be able to fail ---------
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

# --- 6. the real tree, not only the fixture (#740 item 1) -------------------
# Every audit() call above proves the mechanism against a synthetic scratch
# repo. This step points the SAME audit at the real repository this gate runs
# in ($root), read-only, checked against an explicit, reviewed, provenanced
# baseline (governance/reconcile/real-tree-baseline.json) — the same shape as
# governance/isolation/landed-baseline.json / scripts/gate-coverage-baseline.txt,
# with one deliberate difference: a STALE entry here (a baselined worktree or
# branch the audit no longer reports unmatched — it was cleaned up, the
# DESIRED outcome) is reported and counted but does NOT fail the gate, because
# disk artifacts are meant to disappear (a landed commit never does). Only a
# NEW unbaselined artifact older than the age-grace window fails, named.
real_tree_baseline="governance/reconcile/real-tree-baseline.json"
if [ ! -f "$real_tree_baseline" ]; then
  echo "check-reconcile: FAIL — $real_tree_baseline is missing (#740)" >&2
  exit 1
fi
real_tree_baseline_sha_before="$(sha256sum "$real_tree_baseline" | awk '{print $1}')"

python3 - "$root" "$real_tree_baseline" <<'PYREALTREE'
import sys
sys.path.insert(0, sys.argv[1])
from governance.reconcile.real_tree_baseline import check_real_tree

root, baseline_path = sys.argv[1], sys.argv[2]
verdict = check_real_tree(root, baseline_path)
print(verdict.describe())
if not verdict.assessable:
    print("check-reconcile: CANNOT-ASSESS on the real tree", file=sys.stderr)
    raise SystemExit(2)
if not verdict.ok:
    print(
        f"check-reconcile: FAIL — real tree drifted from {baseline_path} "
        f"({len(verdict.new_violations)} new-and-old, {len(verdict.stale_entries)} stale, not fatal)",
        file=sys.stderr,
    )
    raise SystemExit(1)
if verdict.stale_entries:
    print(
        f"check-reconcile: {len(verdict.stale_entries)} stale baseline entr(y/ies) — "
        f"cleaned up on disk; safe to `status --disk --prune-stale`",
        file=sys.stderr,
    )
PYREALTREE
real_tree_rc=$?
if [ "$real_tree_rc" -ne 0 ]; then
  fail=$((fail + 1))
fi
real_tree_baseline_sha_after="$(sha256sum "$real_tree_baseline" | awk '{print $1}')"
if [ "$real_tree_baseline_sha_before" != "$real_tree_baseline_sha_after" ]; then
  echo "  FAIL  the read-only real-tree check modified $real_tree_baseline" >&2
  fail=$((fail + 1))
else
  echo "  OK    the real-tree check is read-only: $real_tree_baseline is unchanged"
fi

# --- 6b. the OLD-unbaselined-artifact provocation must be able to fail ------
# A gate that only ever passes is not proof of anything. Stale is deliberately
# non-fatal now (§ above), so the provocation that matters is the one that
# still must bite: a REAL, unbaselined worktree + branch, backdated past the
# age-grace window, in the ACTUAL repository this gate runs in. It must be
# refused by name — and removed again before this script exits, restoring the
# repository to exactly the state it found ($real_tree_baseline is untouched,
# proven by its checksum above; the fixture worktree/branch are pruned in a
# trap so a failure mid-provocation still cleans up).
provoke_branch="issue-check-reconcile-old-provocation-$$"
provoke_worktree="$work/old-provocation-wt"
cleanup_provocation() {
  git -C "$root" worktree remove --force "$provoke_worktree" >/dev/null 2>&1 || true
  git -C "$root" worktree prune >/dev/null 2>&1 || true
  git -C "$root" branch -D "$provoke_branch" >/dev/null 2>&1 || true
}
# Not installed as the EXIT trap: `work`'s own `trap 'rm -rf "$work"' EXIT`
# (above, §3) is already the script's one EXIT trap, and a second `trap ...
# EXIT` would replace it rather than chain. Cleaned up explicitly below on
# every path (success, provocation failure, or fixture-creation failure)
# instead.

if git -C "$root" worktree add -q -b "$provoke_branch" "$provoke_worktree" HEAD 2>/dev/null; then
  thirty_days_ago_epoch=$(( $(date +%s) - 30 * 24 * 3600 ))
  thirty_days_ago_git="$(date -u -d "@$thirty_days_ago_epoch" +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r "$thirty_days_ago_epoch" +%Y-%m-%dT%H:%M:%S)"
  GIT_AUTHOR_DATE="$thirty_days_ago_git" GIT_COMMITTER_DATE="$thirty_days_ago_git" \
    git -C "$provoke_worktree" commit -q --allow-empty -m "backdated provocation, never real work" >/dev/null 2>&1
  touch -d "@$thirty_days_ago_epoch" "$provoke_worktree" 2>/dev/null || touch -t "$(date -r "$thirty_days_ago_epoch" +%Y%m%d%H%M.%S)" "$provoke_worktree"

  if python3 - "$root" "$real_tree_baseline" "$provoke_branch" "$provoke_worktree" <<'PYPROVOKECHECK'
import sys
sys.path.insert(0, sys.argv[1])
from governance.reconcile.real_tree_baseline import check_real_tree

root, baseline_path, branch, worktree = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
verdict = check_real_tree(root, baseline_path)
new_names = {e.name for e in verdict.new_violations}
young_names = {e.name for e in verdict.young}
ok = (
    verdict.assessable
    and not verdict.ok
    and branch in new_names
    and worktree in new_names
    and branch not in young_names
    and worktree not in young_names
)
if not ok:
    print(
        f"  FAIL  old-unbaselined provocation did not fire (new={sorted(new_names)}, "
        f"young={sorted(young_names)})",
        file=sys.stderr,
    )
    raise SystemExit(1)
print("  OK    old-unbaselined provocation fires: a backdated, unbaselined worktree AND branch are refused by name (neither is 'young')")
PYPROVOKECHECK
  then
    :
  else
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  could not create the old-unbaselined provocation fixture" >&2
  fail=$((fail + 1))
fi

cleanup_provocation

if [ "$fail" -gt 0 ]; then
  echo "check-reconcile: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-reconcile: OK — heartbeats beat, orphans are flagged, landed lanes are reclaimed, unmerged work is never destroyed, and the disk audit names what no record explains (removing nothing, and refusing to guess when it cannot look), including against the real tree against its reviewed baseline"
exit 0
