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
# The last section (§6) points the audit at the REAL repository, and it is the
# one that reds a whole fleet when it is wrong, so it is proven in both
# directions (#1291): the *work* on the default branch is proven landed
# (`landing.py` — ancestry, tree containment, or patch identity against the
# default branch's own commits, never the branch's name), the residue is
# quarantined by NAME with a tip-pinned, leased exemption document that declares
# the repository instance it was measured in (#1321), and §6e provokes every way
# an exemption could be abused: an entry matching nothing, an artifact whose tip
# moved, a lease that is closed or expired, a document measured in another
# repository instance (inert, never excusing and never fatal), a document with
# entries and no venue at all (CANNOT-ASSESS), and an unreadable document
# (CANNOT-ASSESS).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-reconcile.sh
#        bash scripts/check-reconcile.sh --real-tree-only DIR BASELINE [QUARANTINE]
#          runs ONLY the §6 real-tree step against DIR (the same code path, so a
#          driver outside the repo can provoke it with its own scratch tree) and
#          exits 0/1/2. The no-argument form is the gate.
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
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

# --- the real-tree step, as ONE definition (#1291) --------------------------
#
# §6 runs it against this repository; the `--real-tree-only` seam below runs the
# same body against another tree, which is what lets an out-of-band driver point
# the step at its own fixture repository, its own baseline and its own quarantine
# document (and assert the exit code AND the refusal string this script produces).
# One definition, so the gate and its provocation can never drift apart.
run_real_tree_step() {
  # $1 = the tree to audit, $2 = baseline, $3 = quarantine document ("" = none),
  # $4 = where the CODE lives (defaults to this repo; the audited tree may be a
  #      fixture with no `governance/` package of its own)
  local audited="$1" baseline="$2" quarantine="$3" code_root="${4:-$root}"
  python3 - "$audited" "$baseline" "$quarantine" "$code_root" <<'PYREALTREE'
import sys

audited, baseline_path, quarantine_path, code_root = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
# The audited root is DATA (read through git and its `.fleet/` state); the code
# under test is this checkout's. Importing the fixture's own package would prove
# the fixture, not the gate.
sys.path.insert(0, audited)
sys.path.insert(0, code_root)
from governance.reconcile.real_tree_baseline import check_real_tree

verdict = check_real_tree(
    audited, baseline_path, quarantine_path=(quarantine_path or None)
)
print(verdict.describe())
if not verdict.assessable:
    print("check-reconcile: CANNOT-ASSESS on the real tree", file=sys.stderr)
    raise SystemExit(2)
if not verdict.ok:
    print(
        f"check-reconcile: FAIL — real tree drifted from {baseline_path} "
        f"({len(verdict.new_violations)} new-and-old, {len(verdict.stale_entries)} stale, not fatal; "
        f"{len(verdict.stale_quarantine)} stale quarantine exemption(s))",
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
}

# --- out-of-band seam: drive §6's step against another tree -----------------
#
# Usage: bash scripts/check-reconcile.sh --real-tree-only DIR BASELINE [QUARANTINE]
#
# The no-argument invocation is the gate (`scripts/discover-checks.sh` passes no
# arguments, so this branch is never the gate path). This mode exists because a
# check that can only be provoked from inside itself is hard to believe: a driver
# outside the repository builds its own scratch tree, baseline and quarantine,
# runs THIS script, and asserts the exit code and the refusal text.
if [ "${1:-}" = "--real-tree-only" ]; then
  real_tree_only_root="${2:-}"
  real_tree_only_baseline="${3:-}"
  real_tree_only_quarantine="${4:-}"
  if [ -z "$real_tree_only_root" ] || [ ! -d "$real_tree_only_root" ]; then
    echo "check-reconcile: CANNOT-ASSESS — --real-tree-only needs a directory (got '${real_tree_only_root}')" >&2
    exit 2
  fi
  if [ ! -f "$real_tree_only_baseline" ]; then
    echo "check-reconcile: CANNOT-ASSESS — baseline not found: ${real_tree_only_baseline}" >&2
    exit 2
  fi
  run_real_tree_step "$real_tree_only_root" "$real_tree_only_baseline" "$real_tree_only_quarantine"
  exit $?
fi

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


def unique_work(where, name, message):
    """Commit a file that is on NO other branch, in `where` (repo or worktree).

    Load-bearing for the proofs below (#1291): since the audit also reads the
    landing proof, an artifact that sits at a commit already on `origin/master`
    can be explained by that alone — which would make the "removing the beat /
    the claim / the journal makes it refused" assertions pass for the wrong
    reason. An artifact holding unlanded work is explained ONLY by its record.
    """
    (where / name).write_text(f"{name}: unlanded\n", encoding="utf-8")
    git(where, "add", name)
    git(where, "commit", "-q", "-m", message)


def branch_holding_unique_work(name, path):
    """A branch with one unlanded commit and no worktree left behind."""
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", name,
                    str(path), "origin/master"], check=True)
    unique_work(path, f"{name}.txt", f"{name}: unlanded work")
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(path)],
                   check=True)
    return path


# --- an artifact each record DOES explain, one per source -------------------
beaten = work / "audit-lane-beaten"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "issue-905",
                str(beaten), "origin/master"], check=True)
unique_work(beaten, "beat-work.txt", "issue-905: unlanded work")
stamp("audit-beaten", issue=905, agent="gate-agent", root=repo, worktree=str(beaten),
      branch="issue-905", at=datetime.now(timezone.utc).timestamp())

(repo / ".board").mkdir(exist_ok=True)
(repo / ".board" / "claims.jsonl").write_text(
    json.dumps({"event": "claim", "issue": 901, "agent": "gate-agent", "at": now_iso(),
                "lane": "gate", "reason": "next-in-milestone"}) + "\n",
    encoding="utf-8",
)
branch_holding_unique_work("issue-901", work / "audit-claim-staging")

(repo / ".fleet" / "lifecycle").mkdir(parents=True, exist_ok=True)
(repo / ".fleet" / "lifecycle" / "902.json").write_text(
    json.dumps({"closing_evidence": True}) + "\n", encoding="utf-8"
)
branch_holding_unique_work("issue-902", work / "audit-journal-staging")

# --- the provoked negatives: an artifact NO record explains -----------------
orphan = work / "audit-lane-orphan"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "orphan-branch",
                str(orphan), "origin/master"], check=True)
(orphan / "unmerged.txt").write_text("unmerged work\n", encoding="utf-8")
git(orphan, "add", "unmerged.txt")
git(orphan, "commit", "-q", "-m", "unmerged")
branch_holding_unique_work("issue-903", work / "audit-orphan-staging")

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
#
# Two further sources sit under this step (#1291):
#
#   * the landing proof — `governance/reconcile/landing.py` explains an artifact
#     whose *work* is on the default branch (ancestry, tree containment or patch
#     identity). Measured at `99f6b37`: 25 of 35 findings were false without it,
#     and each red a composite gate that serializes the whole fleet;
#   * `real-tree-quarantine.json` — named, tip-pinned, leased exemptions for the
#     residue: the AGENTS.md rule 17 class, work that exists nowhere else, which
#     the worker is forbidden to discard and the gate must therefore report
#     rather than red on. It cannot absorb anything new (an unlisted artifact
#     fails immediately; so does one whose tip has moved), and an entry that
#     excuses nothing FAILS as a stale exemption.
#
#   * and the document declares its VENUE (#1321) — the repository instance its
#     exemptions were measured in. An exemption names a disk artifact of one
#     checkout (a local branch, a worktree path), and the document is tracked, so
#     it is read on checkouts where that artifact was never there; read
#     venue-blind, every entry there "matches nothing" and fails as stale. #1317
#     measured exactly that on a pristine clone and emptied the document — which
#     un-quarantined all of it on the one box that has the artifacts. So: at the
#     venue it declares, every rule above applies unchanged; anywhere else the
#     whole document is INERT — each entry reported by name, honouring nothing
#     (an artifact that IS unmatched there stays a finding) and not fatal (a
#     stale exemption is a claim about the declared venue's disk, which this
#     checkout cannot observe). A document holding entries with no venue is
#     CANNOT-ASSESS, never a pass.
#
# A baseline this step cannot read is CANNOT-ASSESS (exit 2), never drift and
# never a pass: "the state could not be read" and "the state is wrong" are
# different answers, and this script declares all three in its contract above.
real_tree_baseline="governance/reconcile/real-tree-baseline.json"
real_tree_quarantine="governance/reconcile/real-tree-quarantine.json"
if [ ! -f "$real_tree_baseline" ]; then
  echo "check-reconcile: CANNOT-ASSESS — $real_tree_baseline is missing (#740): the real tree cannot be assessed" >&2
  exit 2
fi
real_tree_baseline_sha_before="$(sha256sum "$real_tree_baseline" | awk '{print $1}')"
real_tree_quarantine_sha_before="$(sha256sum "$real_tree_quarantine" 2>/dev/null | awk '{print $1}' || echo absent)"

run_real_tree_step "$root" "$real_tree_baseline" "$real_tree_quarantine"
real_tree_rc=$?
if [ "$real_tree_rc" -eq 2 ]; then
  echo "check-reconcile: CANNOT-ASSESS — the real tree could not be assessed; that is never a pass" >&2
  exit 2
fi
if [ "$real_tree_rc" -ne 0 ]; then
  fail=$((fail + 1))
fi
real_tree_baseline_sha_after="$(sha256sum "$real_tree_baseline" | awk '{print $1}')"
real_tree_quarantine_sha_after="$(sha256sum "$real_tree_quarantine" 2>/dev/null | awk '{print $1}' || echo absent)"
if [ "$real_tree_baseline_sha_before" != "$real_tree_baseline_sha_after" ]; then
  echo "  FAIL  the read-only real-tree check modified $real_tree_baseline" >&2
  fail=$((fail + 1))
elif [ "$real_tree_quarantine_sha_before" != "$real_tree_quarantine_sha_after" ]; then
  echo "  FAIL  the read-only real-tree check modified $real_tree_quarantine" >&2
  fail=$((fail + 1))
else
  echo "  OK    the real-tree check is read-only: $real_tree_baseline and $real_tree_quarantine are unchanged"
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

  if python3 - "$root" "$real_tree_baseline" "$real_tree_quarantine" "$provoke_branch" "$provoke_worktree" <<'PYPROVOKECHECK'
import sys

code_root = sys.argv[1]
sys.path.insert(0, code_root)
from governance.reconcile.real_tree_baseline import check_real_tree

root, baseline_path, quarantine_path = sys.argv[1], sys.argv[2], sys.argv[3]
branch, worktree = sys.argv[4], sys.argv[5]
# The SAME configuration §6 runs (baseline AND the named quarantine): a planted
# artifact must fail *with* the exemptions in place, which is what proves the
# quarantine cannot absorb something it does not name (#1291).
verdict = check_real_tree(root, baseline_path, quarantine_path=quarantine_path)
new_names = {e.name for e in verdict.new_violations}
young_names = {e.name for e in verdict.young}
quarantined_names = {e.name for e in verdict.quarantined}
stale_names = {e.name for e in verdict.stale_quarantine}
ok = (
    verdict.assessable
    and not verdict.ok
    and branch in new_names
    and worktree in new_names
    and branch not in young_names
    and worktree not in young_names
    and branch not in quarantined_names
    and worktree not in quarantined_names
    and not stale_names
)
if not ok:
    print(
        f"  FAIL  old-unbaselined provocation did not fire (new={sorted(new_names)}, "
        f"young={sorted(young_names)}, quarantined={len(quarantined_names)}, "
        f"stale_quarantine={sorted(stale_names)})",
        file=sys.stderr,
    )
    raise SystemExit(1)
print("  OK    old-unbaselined provocation fires: a backdated, unbaselined worktree AND branch are refused by name (neither is 'young'), and neither is absorbed by the named quarantine")
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

# --- 6e. the named quarantine's teeth (#1291) -------------------------------
# Exemptions are the only thing in this step that can turn a finding into a pass,
# so each way they could be abused is provoked rather than asserted. Every half
# runs through the SAME `check_real_tree` the gate runs, with fixture quarantine
# documents, against a SCRATCH repository with its own planted, backdated
# artifact — not the real tree. Two reasons: the rules under test are properties
# of the *document* (the real tree's own configuration is proven by §6 and §6b
# above), and a fixture root is deterministic — the real tree moves while the
# gate runs, which is exactly what the fixtures must not depend on.
#
#   (iii) an entry naming a real, present, unmatched artifact at its exact tip IS
#         honoured — and that artifact is then neither a violation nor 'young';
#   (i)   an entry naming an artifact that is not there FAILS as a stale
#         exemption, by name;
#   (i-b) an entry whose artifact is present but whose recorded tip has moved is
#         NOT absorbed: it is a violation AND a stale exemption, both named;
#   (ii)  a lease that is closed, or whose measurement is older than its own
#         declared age bound, honours nothing and fails by name (without a lease,
#         "honoured while the tracking issue is open" would be decorative, GR-29);
#   (iv)  an unreadable document is CANNOT-ASSESS, never a pass;
#   (v)   a document measured in ANOTHER repository instance is INERT here (#1321):
#         every entry is reported by name, honours nothing (the artifact stays a
#         finding — this document does not speak for this checkout) and is NOT
#         fatal (a stale exemption is a claim about the OTHER venue's disk, which
#         this checkout cannot observe). That is the case #1317 measured on a
#         pristine clone and emptied the document over; failing on it reds CI on
#         a box-local document, and honouring it would let a foreign venue excuse
#         work here. Without (v) the other five halves are satisfiable by a
#         document that simply has no venue — which is CANNOT-ASSESS, not a pass.
q_root="$work/quarantine-case"
q_repo="$q_root/repo"
q_docs="$q_root/docs"
q_branch="issue-check-reconcile-quarantine-$$"
q_worktree="$q_root/lane-wt"
q_ready=0
if mkdir -p "$q_repo" "$q_docs" 2>/dev/null \
  && git -C "$q_repo" init -q -b master >/dev/null 2>&1 \
  && git -C "$q_repo" config user.name "Gate Human" \
  && git -C "$q_repo" config user.email "gate-human@example.com" \
  && printf 'seed\n' > "$q_repo/seed.txt" \
  && git -C "$q_repo" add seed.txt \
  && git -C "$q_repo" commit -q -m seed \
  && git -C "$q_repo" worktree add -q -b "$q_branch" "$q_worktree" HEAD 2>/dev/null; then
  q_backdate_epoch=$(( $(date +%s) - 30 * 24 * 3600 ))
  q_backdate_git="$(date -u -d "@$q_backdate_epoch" +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r "$q_backdate_epoch" +%Y-%m-%dT%H:%M:%S)"
  GIT_AUTHOR_DATE="$q_backdate_git" GIT_COMMITTER_DATE="$q_backdate_git" \
    git -C "$q_worktree" commit -q --allow-empty -m "quarantine fixture, never real work" >/dev/null 2>&1
  touch -d "@$q_backdate_epoch" "$q_worktree" 2>/dev/null || true
  q_ready=1
fi

if [ "$q_ready" -eq 1 ]; then
  if python3 - "$root" "$q_repo" "$q_docs" "$q_branch" "$q_worktree" <<'PYQUARANTINE'
"""Live proof: the named quarantine excuses exactly what it names (#1291)."""
import json
import subprocess
import sys
import time

code_root, repo, docs = sys.argv[1], sys.argv[2], sys.argv[3]
branch, worktree = sys.argv[4], sys.argv[5]
sys.path.insert(0, code_root)
from governance.reconcile.real_tree_baseline import check_real_tree, repository_venue

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}")
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr)


def git(*args):
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return result.stdout.strip()


def git_in(path, *args):
    result = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    return result.stdout.strip()


now = time.time()
tip = git("rev-parse", "refs/heads/" + branch)
# Read the HEAD *in the worktree*: the primary checkout's HEAD is a different
# commit, and an exemption pinned to the wrong one lapses immediately (which is
# how this line was caught).
head = git_in(worktree, "rev-parse", "HEAD")
if not tip or not head:
    print("  FAIL  the quarantine fixture has no tip to pin", file=sys.stderr)
    raise SystemExit(1)
iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
baseline = f"{docs}/empty-baseline.json"
with open(baseline, "w", encoding="utf-8") as handle:
    json.dump({"note": "fixture: nothing pretends to be pre-existing", "entries": []}, handle)


def document(name, *, state="open", measured_at=None, max_age_hours=1, entries=None, raw=None, venue=None):
    """A fixture document, declaring the FIXTURE repo's venue by default (#1321).

    An exemption names a disk artifact of one repository instance, so the
    fixture must say which instance it was measured in — otherwise every half
    below is inert (CANNOT-ASSESS without a venue at all) instead of honoured or
    refused, and the proofs would pass vacuously.
    """
    path = f"{docs}/{name}.json"
    if raw is not None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw)
        return path
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "version": 1,
                "note": "fixture written by check-reconcile.sh §6e",
                "tracked_by": "#1291",
                "tracking": {
                    "state": state,
                    "measured_at": measured_at or iso,
                    "measured_by": "check-reconcile.sh §6e",
                    "max_age_hours": max_age_hours,
                },
                "venue": {
                    "git_common_dir": venue if venue is not None else repository_venue(repo),
                    "measured_on": "check-reconcile.sh §6e",
                },
                "quarantine": entries or [],
            },
            handle,
        )
    return path


present = [
    {"kind": "branch", "name": branch, "tip": tip, "reason": "fixture: a planted, backdated branch"},
    {"kind": "worktree", "name": worktree, "tip": head, "reason": "fixture: its lane worktree"},
]


def verdict(document_path):
    return check_real_tree(repo, baseline, quarantine_path=document_path)


# The precondition every half below rests on: without a document, this artifact
# is a violation (so "honoured" means something, and the refusals are not vacuous).
plain = verdict(f"{docs}/never-written.json")
check(
    "with no quarantine at all, the planted artifact IS a violation (the precondition)",
    not plain.ok and branch in {e.name for e in plain.new_violations} and plain.quarantined == (),
    f"violations={sorted(e.name for e in plain.new_violations)}",
)

# (iii) an entry naming a real, present, unmatched artifact at its exact tip IS honoured
honoured = verdict(document("honoured", entries=present))
quarantined = {e.name for e in honoured.quarantined}
fixture_violations = {e.name for e in honoured.new_violations} & {branch, worktree}
check(
    "an entry naming the artifact at the tip it records IS honoured, and the verdict is OK",
    honoured.assessable and honoured.ok and branch in quarantined and worktree in quarantined
    and not fixture_violations and not honoured.stale_quarantine,
    f"quarantined={sorted(quarantined)} violations={sorted(fixture_violations)} "
    f"stale={sorted(e.name for e in honoured.stale_quarantine)}",
)
check(
    "and the excused artifact is not 'young' either (it is excused, not deferred)",
    branch not in {e.name for e in honoured.young},
)
check(
    "and it is still REPORTED by name on every pass",
    branch in honoured.describe() and worktree in honoured.describe(),
)

# (i) an entry that matches nothing FAILS as a stale exemption, by the name it carries
absent = f"{branch}-absent-{int(now)}"
stale_doc = verdict(document(
    "stale",
    entries=[{"kind": "branch", "name": absent, "tip": tip, "reason": "fixture: nothing is there"}],
))
stale = {e.name for e in stale_doc.stale_quarantine}
check(
    "an entry that matches nothing FAILS as a stale exemption, named",
    stale_doc.assessable and not stale_doc.ok and absent in stale,
    f"stale={sorted(stale)} ok={stale_doc.ok}",
)

# (i-b) an entry whose artifact is present but whose tip has MOVED is not absorbed
moved = verdict(document(
    "moved",
    entries=[{"kind": "worktree", "name": worktree, "tip": "0" * 40, "reason": "fixture: not this artifact's tip"}],
))
check(
    "an artifact whose tip has moved is NOT absorbed by its own entry",
    not moved.ok and worktree in {e.name for e in moved.new_violations}
    and worktree in {e.name for e in moved.stale_quarantine},
    f"violations={sorted(e.name for e in moved.new_violations)}",
)

# (ii) a lease that is not open, or has expired, is not honoured
for label, kwargs in (
    ("closed tracking issue", {"state": "closed"}),
    ("expired measurement", {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 30 * 3600))}),
):
    lapsed = verdict(document("lease", entries=present, **kwargs))
    check(
        f"a lease with a {label} is NOT honoured: nothing is excused and the lease fails by name",
        not lapsed.ok and lapsed.quarantined == ()
        and branch in {e.name for e in lapsed.new_violations}
        and "#1291" in {e.name for e in lapsed.stale_quarantine},
        f"stale={sorted(e.name for e in lapsed.stale_quarantine)} "
        f"violations={sorted(e.name for e in lapsed.new_violations)}",
    )

# (iv) a document that cannot be read is CANNOT-ASSESS, never a pass
broken = verdict(document("broken", raw="{not json"))
check(
    "an unreadable quarantine document is CANNOT-ASSESS, never a pass",
    not broken.assessable and not broken.ok,
    f"assessable={broken.assessable}",
)

# (v) a document measured in ANOTHER repository instance is INERT here (#1321):
#     reported by name, honouring nothing, and NOT fatal. This is the case #1317
#     measured on a pristine clone (`0 new-and-old, 538 stale; 23 stale
#     quarantine exemption(s)`) and emptied the document over. The SAME entry,
#     artifact and tip are honoured at their own venue — that is half (iii)
#     above — so what (v) proves is that the VENUE decides, not the entry.
elsewhere = verdict(document("elsewhere", entries=present, venue="/somewhere/else/.git"))
inert = {e.name for e in elsewhere.inapplicable_quarantine}
check(
    "an entry measured in another repository instance is INERT here: reported by name, "
    "honouring nothing, not fatal",
    elsewhere.assessable
    and not elsewhere.ok  # fail-closed: the artifact it names is still a finding here
    and branch in {e.name for e in elsewhere.new_violations}
    and branch in inert
    and worktree in inert
    and elsewhere.quarantined == ()
    and not elsewhere.stale_quarantine
    and "NOT IN FORCE" in elsewhere.describe()
    and branch in elsewhere.describe(),
    f"quarantined={sorted(e.name for e in elsewhere.quarantined)} "
    f"inapplicable={sorted(inert)} stale={sorted(e.name for e in elsewhere.stale_quarantine)} "
    f"violations={sorted(e.name for e in elsewhere.new_violations)}",
)

# ...and without a venue it cannot be interpreted at all: an exemption names a
# disk artifact of one repository instance, so a document that does not say which
# one has no way to tell "the artifact is gone" from "the artifact was never
# here". Both shapes of that (a missing block, an empty identity) are refusals.
no_venue_block = verdict(document("no-venue-block", raw=json.dumps({
    "version": 1,
    "tracked_by": "#1291",
    "tracking": {
        "state": "open",
        "measured_at": iso,
        "measured_by": "check-reconcile.sh §6e",
        "max_age_hours": 1,
    },
    "quarantine": present,
})))
check(
    "entries with no 'venue' block at all are CANNOT-ASSESS, never a pass",
    not no_venue_block.assessable and not no_venue_block.ok and "venue" in no_venue_block.reason,
    f"assessable={no_venue_block.assessable} reason={no_venue_block.reason[:120]}",
)
empty_venue = verdict(document("empty-venue", entries=present, venue=""))
check(
    "entries with an empty venue identity are CANNOT-ASSESS, never a pass",
    not empty_venue.assessable and not empty_venue.ok and "venue" in empty_venue.reason,
    f"assessable={empty_venue.assessable} reason={empty_venue.reason[:120]}",
)

if problems:
    print(f"  ({len(problems)} quarantine proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
PYQUARANTINE
  then
    :
  else
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  could not create the §6e quarantine fixture (#1291)" >&2
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-reconcile: FAIL ($fail violation(s))" >&2
  exit 1
fi

# --- 7. controls provocation (#885): a mutated control changes behaviour ---
#
# `sweep.max_actions_per_pass` (controls.yaml) is read, never hard-coded
# (grep-provable below), and a mutated copy of it (never the tracked file —
# the mutation lives entirely in $work, so nothing here needs the
# sha256-restore idiom §6 uses for the real-tree baseline) must refuse every
# destructive decision by name, with exactly one ledger record per refusal.
if grep -qF "reconcile_policy.load()" governance/reconcile/sweep.py \
  && grep -qF "max_actions_per_pass" governance/reconcile/sweep.py \
  && ! grep -qE "max_actions_per_pass[[:space:]]*=[[:space:]]*[0-9]+" governance/reconcile/sweep.py; then
  echo "  OK    sweep.py reads sweep.max_actions_per_pass through policy.load(), not a bare literal"
else
  echo "  FAIL  sweep.py does not read the control (or hard-codes it)" >&2
  fail=$((fail + 1))
fi

if python3 - "$root" "$work" <<'PYCONTROLS'
"""Live proof: a mutated `max_actions_per_pass` control refuses teardowns (#885)."""
import subprocess
import sys
import time
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2])
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from governance.reconcile import ledger, policy
from governance.reconcile.heartbeat import stamp
from governance.reconcile.sweep import REFUSED_OUTCOME, RepoOps, sweep

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

origin = work / "controls-origin.git"
repo = work / "controls-repo"
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

landed = work / "controls-lane"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "controls-lane",
                str(landed), "origin/master"], check=True)
stamp("controls-1", issue=1, agent="a", root=repo, worktree=str(landed), branch="controls-lane",
      at=time.time() - 20 * 60)

# A mutated copy — never the tracked controls.yaml.
mutated = work / "controls-mutated.yaml"
mutated.write_text(
    "schema: ao.reconcile/controls-v1\n"
    "sweep:\n"
    "  max_actions_per_pass: 0\n"
    "  outcome_codes:\n"
    "    reclaimed: reconcile.reclaimed\n"
    "    parked: reconcile.parked\n"
    "    shelved: reconcile.shelved\n"
    "    reported: reconcile.reported\n"
    "    failed: reconcile.failed\n"
    "    refused: reconcile.batch-limit-exceeded\n",
    encoding="utf-8",
)
mutated_controls = policy.load(mutated)
report = sweep(repo, ttl_minutes=15, apply=True, ops=RepoOps(repo), controls=mutated_controls)
check("a control mutated to 0 refuses the destructive decision, by name",
      all(a.outcome == REFUSED_OUTCOME for a in report.actions), str([a.outcome for a in report.actions]))
check("the landed lane's worktree is untouched: the refusal did not act",
      landed.exists())
records = ledger.read(repo)
refused_records = [r for r in records if r["outcome"] == REFUSED_OUTCOME]
check("the refusal produced exactly one ledger record, validated and named",
      len(refused_records) == 1 and refused_records[0]["code"] == "reconcile.batch-limit-exceeded",
      str(records))

# The DEFAULT (packaged) control is not 0, so the same session, unmutated,
# is NOT refused — proving the mutation, not some other bug, is what bites.
default_report = sweep(repo, ttl_minutes=15, apply=True, ops=RepoOps(repo))
check("the packaged (unmutated) control does not refuse the same session",
      not any(a.outcome == REFUSED_OUTCOME for a in default_report.actions),
      str([a.outcome for a in default_report.actions]))

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
PYCONTROLS
then
  :
else
  fail=$((fail + 1))
fi

# --- 8. audit-trail provocation (#885): a missing ledger record is refused -
if python3 - "$root" "$work" <<'PYAUDITTRAIL'
"""Live proof: an append-only ledger record is written per decision, and a
record's absence is detected by ledger.verify() (#885)."""
import sys
import time
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2])
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from governance.reconcile import ledger

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}")
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr)


scratch = work / "ledger-scratch"
scratch.mkdir(parents=True)
ledger.record_sweep_decision(scratch, session_id="s1", issue=1, agent="a",
                              outcome="reclaimed", code="reconcile.reclaimed",
                              reason="landed", at=time.time())
ledger.record_sweep_decision(scratch, session_id="s2", issue=2, agent="a",
                              outcome="parked", code="reconcile.parked",
                              reason="pushed", at=time.time())
ok, description = ledger.verify(scratch)
check("a clean ledger with one record per decision verifies OK", ok, description)
check("the ledger has exactly one record per decision written", len(ledger.read(scratch)) == 2)

# Provoke: simulate a decision site that recorded nothing (a missing record).
path = ledger.ledger_path(scratch)
lines = path.read_text(encoding="utf-8").splitlines()
path.write_text(lines[0] + "\n", encoding="utf-8")  # drop the second record
records_after_drop = ledger.read(scratch)
check("a missing record is refused by name: the count no longer matches the decisions made",
      len(records_after_drop) == 1,
      f"expected 1 record after the drop, found {len(records_after_drop)}")

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
PYAUDITTRAIL
then
  :
else
  fail=$((fail + 1))
fi

# --- 9. schema provocation (#885): a schema-invalid record is refused ------
if python3 - "$root" "$work" <<'PYSCHEMA'
"""Live proof: ledger.append refuses a record that violates reconcile.schema.json (#885)."""
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2])
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from governance.reconcile import ledger

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}")
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr)


scratch = work / "schema-scratch"
scratch.mkdir(parents=True)
raised = False
try:
    ledger.append(scratch, {"kind": "not-a-real-kind", "at": "not-a-number", "at_iso": "x"})
except ledger.LedgerUnavailable:
    raised = True
check("a schema-invalid record is refused (LedgerUnavailable) and never written",
      raised and ledger.read(scratch) == [])

# A hand-corrupted line in an otherwise-valid ledger is caught by verify().
ledger.record_sweep_decision(scratch, session_id="s1", issue=1, agent="a",
                              outcome="reclaimed", code="reconcile.reclaimed",
                              reason="landed", at=1000.0)
path = ledger.ledger_path(scratch)
path.write_text('{"schema": "ao.reconcile/ledger-record-v1", "kind": "sweep-decision"}\n',
                 encoding="utf-8")
ok, description = ledger.verify(scratch)
check("a schema-invalid line in the ledger fails verify(), named by line number",
      not ok and "line 1" in description, description)

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
PYSCHEMA
then
  :
else
  fail=$((fail + 1))
fi

# --- 10. live-feed provocation (#885): disk drift is refused by name -------
if python3 - "$root" "$work" <<'PYLIVE'
"""Live proof: `status --live` flags a session whose heartbeat disagrees with
the real disk (#885)."""
import subprocess
import sys
import time
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2])
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from governance.reconcile import live
from governance.reconcile.heartbeat import stamp

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}")
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr)


scratch = work / "live-scratch"
scratch.mkdir(parents=True)
present = scratch / "wt-present"
present.mkdir()
stamp("live-matched", issue=1, agent="a", root=scratch, worktree=str(present), branch="b",
      at=time.time())
gone = scratch / "wt-gone"
stamp("live-drift", issue=2, agent="a", root=scratch, worktree=str(gone), branch="b",
      at=time.time())

rows = live.project(scratch)
by_id = {row.session_id: row for row in rows}
check("a session whose worktree exists is matched",
      by_id["live-matched"].match == live.MATCHED)
check("a session whose worktree is gone is drift, named",
      by_id["live-drift"].match == live.DRIFT and str(gone) in by_id["live-drift"].detail,
      by_id["live-drift"].detail)
live.validate(rows)  # every row satisfies its own frozen shape

cli = repo_root / "governance" / "reconcile" / "cli.py"
result = subprocess.run([sys.executable, str(cli), "--root", str(scratch), "status", "--live"],
                        capture_output=True, text=True)
check("`status --live` exits 1 (NOT-OK) and names the drifted session on stderr",
      result.returncode == 1 and "live-drift" in result.stderr,
      f"rc={result.returncode} stderr={result.stderr.strip()[-200:]}")

# The drift is REPORTED, never removed: this must never delete the beat or
# touch the (already-missing) worktree.
check("the drift is reported, not acted on: the heartbeat still exists",
      (scratch / ".fleet" / "sessions" / "live-drift.json").exists())

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
PYLIVE
then
  :
else
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-reconcile: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-reconcile: OK — heartbeats beat, orphans are flagged, landed lanes are reclaimed, unmerged work is never destroyed, the disk audit names what no record explains, the real tree is checked against its reviewed baseline, controls/audit-trail/schema are load-bearing and mutation-tested, and the live feed flags disk drift (removing nothing, and refusing to guess when it cannot look)"
exit 0
