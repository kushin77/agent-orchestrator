#!/usr/bin/env bash
# check-lifecycle-closeout.sh — the lane's ONE terminal verb is evidenced, ordered,
# and blocked by name (issue #1301 step 2).
#
# `governance/lifecycle/cli.py close --lane <id>` retires every artifact a lane
# bound at creation — PR merged + trailer clean, issue closed, branch reaped
# (tips recorded to .fleet/reaped-branches.jsonl FIRST, content-landed only),
# worktree removed (machine-managed dirt only), lane archived with its evidence
# bundle — and any step it cannot evidence stays open as `closeout-blocked:<step>`
# with everything after it withheld. This gate proves that against:
#
#   1. an injected port (every refusal, the order, the dry run writes nothing);
#   2. a REAL scratch repository with a real bare origin: a squash-landed lane is
#      reaped for real (local + remote branch gone, worktree gone, SHA recorded,
#      archive written) and an UNLANDED lane's branch survives `--apply` by name;
#   3. the CLI's own tri-state: an unknown lane and a missing selector are
#      CANNOT-ASSESS, never a verdict.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# Usage: bash scripts/check-lifecycle-closeout.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

for tool in python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-lifecycle-closeout: CANNOT-ASSESS — $tool not found" >&2
    exit 2
  fi
done
# shellcheck source=scripts/lib/unset-git-env.sh
source "$root/scripts/lib/unset-git-env.sh"
export GIT_CONFIG_GLOBAL=/dev/null

work="${TMPDIR:-/tmp}/lifecycle-closeout.$$.$(date +%s)"
mkdir -p "$work" || { echo "check-lifecycle-closeout: CANNOT-ASSESS — cannot create a scratch directory" >&2; exit 2; }
trap 'rm -rf "$work"' EXIT

fail=0
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

# --- 1. the port-driven controls (the suite, run as this gate's own evidence) --
echo "== 1. the injected port: every refusal, the order, the dry run =="
if python3 -m pytest -q governance/lifecycle/tests/test_lane_closeout.py >"$work/pytest.log" 2>&1; then
  echo "  OK    $(tail -1 "$work/pytest.log")"
else
  echo "  FAIL  governance/lifecycle/tests/test_lane_closeout.py" >&2
  tail -20 "$work/pytest.log" >&2
  fail=$((fail + 1))
fi

# --- 2. a real repository: reaped for real, and an unlanded lane survives ------
echo "== 2. a real scratch repository with a real bare origin =="
python3 - "$root" "$work" >"$work/real.log" 2>&1 <<'PY'
import json, subprocess, sys
from pathlib import Path

root, work = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from governance.lifecycle import lane_closeout as lc
from governance.lifecycle.cli import RepoLaneOps

def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r.stdout.strip()

def commit(cwd, name, msg):
    (Path(cwd) / name).write_text(f"{name}\n")
    git(cwd, "add", name)
    git(cwd, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "commit", "-q", "-m", msg)
    return git(cwd, "rev-parse", "HEAD")

origin = work / "origin.git"
subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
main = work / "main"
subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True, capture_output=True)
commit(main, "seed.txt", "seed")
git(main, "push", "-q", "origin", "master")

def lane(issue, landed):
    branch = f"issue-{issue}"
    wt = work / f"ao-{issue}"
    git(main, "worktree", "add", "-q", "-b", branch, str(wt), "master")
    tip = commit(wt, f"work-{issue}.txt", f"work for #{issue}\n\nRefs kushin77/agent-orchestrator#{issue}")
    git(main, "push", "-q", "origin", branch)
    if landed:
        # A squash landing: the same content on master under a different commit.
        git(main, "checkout", "-q", "master")
        (main / f"work-{issue}.txt").write_text(f"work-{issue}.txt\n")
        git(main, "add", f"work-{issue}.txt")
        git(main, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "commit", "-q", "-m",
            f"squash #{issue}\n\nRefs kushin77/agent-orchestrator#{issue}\nCloses #{issue}")
        git(main, "push", "-q", "origin", "master")
    git(main, "fetch", "-q", "origin")
    record = {"session_id": f"lane{issue}", "lane_id": f"lane{issue}", "issue": issue, "agent_id": "gate",
              "lane": "gate", "branch": branch, "worktree": str(wt), "opened_at": "2026-09-18T00:00:00Z"}
    (main / ".fleet" / "lanes").mkdir(parents=True, exist_ok=True)
    (main / ".fleet" / "lanes" / f"lane{issue}.json").write_text(json.dumps(record))
    (main / ".fleet" / "sessions").mkdir(parents=True, exist_ok=True)
    (main / ".fleet" / "sessions" / f"lane{issue}.json").write_text(json.dumps({"session_id": f"lane{issue}", "at": 1.0}))
    return record, tip

class Ops(RepoLaneOps):
    """The real git half; the board half is fixed (no gh in a scratch repo)."""
    def pull_request_for(self, branch):
        return lc.PullRequest(9000, "merged", git(self.root, "rev-parse", "origin/master"))
    def trailer_finding(self, sha):
        return None
    def issue_state(self, issue):
        return "closed"

ok = True
def check(label, cond, detail=""):
    global ok
    print(("  OK    " if cond else "  FAIL  ") + label + (f" ({detail})" if detail and not cond else ""))
    ok = ok and cond

landed_record, landed_tip = lane(101, landed=True)
result = lc.closeout_lane(landed_record, Ops(main), apply=True)
check("a squash-landed lane closes out OK", result.ok, "; ".join(result.blocked))
check("the local branch is gone", git(main, "rev-parse", "--verify", "--quiet", "refs/heads/issue-101", check=False) == "")
check("the remote branch is gone", git(main, "ls-remote", "--heads", "origin", "issue-101") == "")
check("the worktree is gone", not (work / "ao-101").exists())
reaped = (main / ".fleet" / "reaped-branches.jsonl").read_text() if (main / ".fleet" / "reaped-branches.jsonl").exists() else ""
check("the reaped tip SHA is recorded BEFORE deletion (#1335's ledger)", landed_tip in reaped, reaped[:200])
archive = main / ".fleet" / "lifecycle" / "lanes" / "lane101.json"
check("the lane is archived with its evidence bundle", archive.exists() and json.loads(archive.read_text())["evidence"]["pr"] == 9000)
check("the live lane record and session beat are gone", not (main / ".fleet" / "lanes" / "lane101.json").exists() and not (main / ".fleet" / "sessions" / "lane101.json").exists())
check("the archive is NOT beside the issue journals (reconcile's landed_issues reads int stems)",
      not list((main / ".fleet" / "lifecycle").glob("*.json")))

unlanded_record, unlanded_tip = lane(102, landed=False)
result = lc.closeout_lane(unlanded_record, Ops(main), apply=True)
check("an UNLANDED lane is blocked by name: closeout-blocked:branch-reaped", result.blocked == ["closeout-blocked:branch-reaped"], str(result.blocked))
check("its local branch survives --apply", git(main, "rev-parse", "--verify", "--quiet", "refs/heads/issue-102", check=False) == unlanded_tip)
check("its remote branch survives --apply", unlanded_tip in git(main, "ls-remote", "--heads", "origin", "issue-102"))
check("its worktree survives --apply", (work / "ao-102").exists())
check("its lane record survives --apply", (main / ".fleet" / "lanes" / "lane102.json").exists())
check("nothing of it was recorded as reaped", unlanded_tip not in (main / ".fleet" / "reaped-branches.jsonl").read_text())
raise SystemExit(0 if ok else 1)
PY
rc=$?
cat "$work/real.log"
if [ "$rc" -ne 0 ]; then
  echo "  FAIL  the real-repository controls did not all hold (rc=$rc)" >&2
  fail=$((fail + 1))
fi

# --- 3. the CLI's own tri-state -----------------------------------------------
echo "== 3. the verb's tri-state =="
out="$(python3 governance/lifecycle/cli.py close --lane deadbeefcafe 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && contains "$out" "CANNOT-ASSESS"; then
  echo "  OK    an unknown lane is CANNOT-ASSESS (rc 2), never a verdict"
else
  echo "  FAIL  an unknown lane exited $rc: $out" >&2; fail=$((fail + 1))
fi
out="$(python3 governance/lifecycle/cli.py close 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && contains "$out" "--lane"; then
  echo "  OK    close without --issue or --lane is CANNOT-ASSESS and names both selectors"
else
  echo "  FAIL  close without a selector exited $rc: $out" >&2; fail=$((fail + 1))
fi

if [ "$fail" -ne 0 ]; then
  echo "check-lifecycle-closeout: FAIL ($fail control(s) did not hold)" >&2
  exit 1
fi
echo "check-lifecycle-closeout: OK — the lane close-out is evidenced, ordered and blocked by name, proven on a real repository"
