#!/usr/bin/env bash
# check-reconcile-orphans.sh — every artifact kind has a lane or is an orphan by
# name, reclaimed only with evidence, red above the declared budget (#1301 step 3).
#
# `governance/reconcile/cli.py sweep --orphans` walks the five artifact kinds —
# orphan-worktree / orphan-branch / orphan-pr / orphan-issue-lane /
# orphan-directive — from files, git and the board, and holds the count per
# kind to governance/reconcile/orphan-budget.yaml (measured counts + expiry,
# the ratchet shape of #1335). This gate proves:
#
#   1. the budget is DECLARED and readable, and names every kind;
#   2. every finding fires on an injected port (the suite), including the two
#      load-bearing negatives: an unlanded worktree/branch survives `--apply`,
#      and an unmeasured source is CANNOT-ASSESS, never zero;
#   3. on a REAL scratch repository: a subagent-shaped worktree under
#      `.claude/worktrees/agent-*` with no lane record is named orphan-worktree
#      and NOT removed; the same tree, once its content is on the default
#      branch, is reclaimed under --apply with its tip recorded first; a branch
#      with unpushed work is named with its SHA and never deleted;
#   4. the REAL tree is walked (from the MAIN checkout, wherever this runs) and
#      held to the budget — the walk's own tri-state is honoured, so a box where
#      `gh` cannot be run is CANNOT-ASSESS with the precondition printed.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# Usage: bash scripts/check-reconcile-orphans.sh [--skip-real-tree]
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

skip_real=0
for arg in "$@"; do
  case "$arg" in
    --skip-real-tree) skip_real=1 ;;
    *) echo "check-reconcile-orphans: unknown argument $arg" >&2; exit 2 ;;
  esac
done

for tool in python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-reconcile-orphans: CANNOT-ASSESS — $tool not found" >&2
    exit 2
  fi
done
unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
export GIT_CONFIG_GLOBAL=/dev/null

budget="governance/reconcile/orphan-budget.yaml"
work="${TMPDIR:-/tmp}/reconcile-orphans.$$.$(date +%s)"
mkdir -p "$work" || { echo "check-reconcile-orphans: CANNOT-ASSESS — cannot create a scratch directory" >&2; exit 2; }
trap 'rm -rf "$work"' EXIT

fail=0
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

# --- 1. the budget is declared ------------------------------------------------
echo "== 1. the declared budget =="
if [ ! -f "$budget" ]; then
  echo "  FAIL  $budget is missing — the walk would be held to a budget of 0 with nothing reviewed" >&2
  fail=$((fail + 1))
else
  out="$(python3 -c '
import sys
from datetime import date
from governance.reconcile import orphans
budget, expired = orphans.load_budget(sys.argv[1])
missing = [k for k in orphans.KINDS if k not in budget]
print("expired" if expired else "live", ",".join(f"{k}={v}" for k, v in budget.items()))
sys.exit(1 if missing else 0)
' "$budget" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ] && contains "$out" "live"; then
    echo "  OK    $budget declares every kind and is within its expiry ($out)"
  elif contains "$out" "expired"; then
    echo "  NOTE  $budget has EXPIRED — the allowance is 0 for every kind; every orphan on the real tree is red ($out)"
  else
    echo "  FAIL  $budget could not be read as a budget: $out" >&2
    fail=$((fail + 1))
  fi
fi

# --- 2. the injected port: every finding, both negatives ----------------------
echo "== 2. the injected port: every finding fires, and the two negatives hold =="
if python3 -m pytest -q governance/reconcile/tests/test_orphans.py >"$work/pytest.log" 2>&1; then
  echo "  OK    $(tail -1 "$work/pytest.log")"
else
  echo "  FAIL  governance/reconcile/tests/test_orphans.py" >&2
  tail -20 "$work/pytest.log" >&2
  fail=$((fail + 1))
fi

# --- 3. a real repository: named, never deleted unevidenced; reclaimed with ---
echo "== 3. a real scratch repository =="
python3 - "$root" "$work" >"$work/real.log" 2>&1 <<'PY'
import json, subprocess, sys
from pathlib import Path

root, work = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from governance.reconcile import orphans as o

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
git(main, "fetch", "-q", "origin")

# A subagent-shaped worktree no lane record knows, with unlanded work.
sub = main / ".claude" / "worktrees" / "agent-0123456789abcdef0"
git(main, "worktree", "add", "-q", "-b", "issue-1265", str(sub), "master")
sub_tip = commit(sub, "sub.txt", "subagent work\n\nRefs kushin77/agent-orchestrator#1265")
# A local branch with unpushed work, no lane, no worktree, no PR.
git(main, "branch", "issue-1003", "master")
scratch_wt = work / "scratch-1003"
git(main, "worktree", "add", "-q", str(scratch_wt), "issue-1003")
branch_tip = commit(scratch_wt, "b.txt", "unpushed\n\nRefs kushin77/agent-orchestrator#1003")
git(main, "worktree", "remove", "--force", str(scratch_wt))

class Ops(o.RepoOrphanOps):
    """The real git half; the board half is fixed (no gh in a scratch repo)."""
    def open_pull_requests(self):
        return []
    def issue_states(self, issues):
        return {n: "open" for n in issues}

ok = True
def check(label, cond, detail=""):
    global ok
    print(("  OK    " if cond else "  FAIL  ") + label + (f" ({detail})" if detail and not cond else ""))
    ok = ok and cond

BIG = {k: 100 for k in o.KINDS}
report = o.walk(Ops(main), apply=True, budget=BIG)
names = {orphan.kind: [x.name for x in report.by_kind(orphan.kind)] for orphan in report.orphans}
check("the subagent worktree with no lane record is named orphan-worktree", str(sub) in names.get(o.ORPHAN_WORKTREE, []), str(names))
check("...and survives --apply because its HEAD is NOT content-landed", sub.exists())
found = next(x for x in report.by_kind(o.ORPHAN_BRANCH) if x.name == "issue-1003")
check("the unpushed branch is named orphan-branch with its SHA recorded", found.sha == branch_tip, found.sha)
check("...and survives --apply", git(main, "rev-parse", "--verify", "--quiet", "refs/heads/issue-1003", check=False) == branch_tip)
check("no reap was recorded for either", not (main / ".fleet" / "reaped-branches.jsonl").exists())

# Land the subagent tree's content on master (a squash), then walk again.
git(main, "checkout", "-q", "master")
(main / "sub.txt").write_text("sub.txt\n")
git(main, "add", "sub.txt")
git(main, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "commit", "-q", "-m", "squash #1265\n\nRefs kushin77/agent-orchestrator#1265\nCloses #1265")
git(main, "push", "-q", "origin", "master")
git(main, "fetch", "-q", "origin")
dry = o.walk(Ops(main), apply=False, budget=BIG)
tree = next(x for x in dry.by_kind(o.ORPHAN_WORKTREE) if x.name == str(sub))
check("once content-landed the tree is would-reclaim on a dry run", tree.outcome == o.WOULD_RECLAIM and sub.exists(), tree.outcome)
applied = o.walk(Ops(main), apply=True, budget=BIG)
tree = next(x for x in applied.by_kind(o.ORPHAN_WORKTREE) if x.name == str(sub))
check("...and reclaimed under --apply", tree.outcome == o.RECLAIMED and not sub.exists(), tree.outcome)
ledger = (main / ".fleet" / "reaped-branches.jsonl").read_text() if (main / ".fleet" / "reaped-branches.jsonl").exists() else ""
check("its tip was recorded to .fleet/reaped-branches.jsonl first", sub_tip in ledger and "orphan-walk" in ledger, ledger[:200])
check("the unpushed branch is STILL there after the second --apply", git(main, "rev-parse", "--verify", "--quiet", "refs/heads/issue-1003", check=False) == branch_tip)

# The budget: one orphan-branch against a budget of 0 is red by name.
tight = o.walk(Ops(main), apply=False, budget={**BIG, o.ORPHAN_BRANCH: 0})
# Two branches by now: issue-1003 (unpushed) and issue-1265 (its worktree reclaimed above, the branch left for the walk).
check("above the budget the walk is NOT-OK by name", tight.exceeded == ["orphan-budget-exceeded:orphan-branch:2/0"], str(tight.exceeded))
raise SystemExit(0 if ok else 1)
PY
rc=$?
cat "$work/real.log"
if [ "$rc" -ne 0 ]; then
  echo "  FAIL  the real-repository controls did not all hold (rc=$rc)" >&2
  fail=$((fail + 1))
fi

# --- 4. the real tree, from the main checkout ---------------------------------
echo "== 4. the real tree =="
if [ "$skip_real" -eq 1 ]; then
  echo "  NOTE  --skip-real-tree: the real tree was not walked"
else
  common="$(git rev-parse --git-common-dir 2>/dev/null)" || common=""
  case "$common" in
    "") main_root="" ;;
    /*) main_root="$(cd "$common/.." && pwd)" ;;
    *) main_root="$(cd "$root/$common/.." && pwd)" ;;
  esac
  if [ -z "$main_root" ]; then
    echo "check-reconcile-orphans: CANNOT-ASSESS — the main checkout could not be resolved from $root" >&2
    exit 2
  fi
  if ! command -v gh >/dev/null 2>&1; then
    echo "check-reconcile-orphans: CANNOT-ASSESS — gh not found; orphan-pr / orphan-issue-lane / orphan-directive cannot be measured on the real tree" >&2
    exit 2
  fi
  # The walk ALONE, not the `sweep` verb: `sweep`'s exit code also folds in the
  # session sweep (a stale beat past the TTL is NOT-OK without --apply) and the
  # filed-finding recheck (a board read), neither of which this gate owns — and
  # since #917 stamps a beat for every lane, a dead lane's beat would red every
  # verify run through a verdict about sessions, not orphans.
  real_out="$(python3 - "$main_root" "$root/$budget" <<'PY' 2>&1
import sys
from pathlib import Path
from governance.reconcile import orphans
budget, expired = orphans.load_budget(sys.argv[2])
report = orphans.walk(orphans.RepoOrphanOps(Path(sys.argv[1])), apply=False, budget=budget, budget_expired=expired)
print("orphan-walk (dry-run): " + ", ".join(f"{k}={v}" for k, v in report.counts.items()))
for kind, reason in report.unmeasured.items():
    print(f"  unmeasured {kind}: {reason}")
for name in report.exceeded:
    print(f"  NOT-OK {name}")
sys.exit(2 if not report.assessable else (1 if report.exceeded else 0))
PY
)"
  real_rc=$?
  printf '%s\n' "$real_out" | sed 's/^/  /'
  if [ "$real_rc" -eq 2 ]; then
    echo "check-reconcile-orphans: CANNOT-ASSESS — the real tree could not be walked; that is never a pass" >&2
    exit 2
  elif [ "$real_rc" -ne 0 ]; then
    if contains "$real_out" "orphan-budget-exceeded"; then
      echo "  FAIL  the real tree is above its declared orphan budget ($main_root)" >&2
    else
      echo "  FAIL  the real-tree walk exited $real_rc ($main_root)" >&2
    fi
    fail=$((fail + 1))
  else
    echo "  OK    the real tree ($main_root) is within governance/reconcile/orphan-budget.yaml"
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo "check-reconcile-orphans: FAIL ($fail control(s) did not hold)" >&2
  exit 1
fi
echo "check-reconcile-orphans: OK — every orphan kind is named, reclaimed only with evidence, and the real tree is within budget"
