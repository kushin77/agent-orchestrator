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
#      branch, is reclaimed under --apply with its tip recorded; a branch
#      with unpushed work is named with its SHA and never deleted;
#  3b. on a REAL scratch repository, IN USE is refused by name (#1440): the tree
#      a live process is sitting in, the tree a venue record names, and the tree
#      git holds its own lock on are each refused under --apply with the
#      offending PROCESS, RECORD or LOCK REASON in the finding — while a tree
#      nothing is using, with exactly the same content-landed proof, is still
#      reclaimed; an unreadable liveness signal is CANNOT-ASSESS, never "not in
#      use"; and the whole section FALSIFIES itself by re-running the same
#      controls against a mutant of orphans.py with the liveness guard removed,
#      which must red on the in-use arms and stay green on the dead one.
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
# shellcheck source=scripts/lib/unset-git-env.sh
source "$root/scripts/lib/unset-git-env.sh"
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
check("its tip was recorded to .fleet/reaped-branches.jsonl (after the removal it describes, #1440)", sub_tip in ledger and "orphan-walk" in ledger, ledger[:200])
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

# --- 3b. in use is refused, and the refusal provably bites (#1440) ------------
# The reclaim proof above answers "would this lose work?". It does NOT answer
# "is anything USING this?", and on 2026-09-18 that gap deleted a live Claude
# agent's worktree and a worktree another lane had recorded as its venue. This
# section puts four content-LANDED trees on a real repository, each in use a
# different way, and requires the walk to refuse the three in-use ones BY NAME
# while still reclaiming the one nothing is using.
#
# It then FALSIFIES itself: the same controls run again against a mutant of
# orphans.py with the liveness guard neutralised (`if positive:` -> `if False:`),
# and must RED there — on the in-use arms specifically, while the dead-tree arm
# stays green, so what the arms measure is the liveness verdict and not a
# general breakage. A mutation that does not apply is CANNOT-ASSESS, because an
# arm that silently tests nothing is the formality this section exists to avoid.
echo "== 3b. a real repository: IN USE is refused by name, and the control bites =="
python3 - "$root" "$work" >"$work/liveness.log" 2>&1 <<'PY'
import importlib.util, json, os, subprocess, sys, time
from pathlib import Path

root, work = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from governance.reconcile import orphans as o

# The one line this section falsifies. Named once, so the mutant and the guard
# it removes cannot drift apart silently.
GUARD = "    positive = [signal for signal in measured if signal.hit]\n    if positive:"

BIG = {k: 100 for k in o.KINDS}
CASES = "ABCD"
KEY = {"A": "a", "B": "b", "C": "c", "D": "d"}
rows = []


def emit(label, cond, detail=""):
    """Report one arm. ``rows`` is the program's own tally and is NEVER handed to
    a loop that emits — appending to a list while iterating it is an infinite
    loop (measured here: a 4.8 GB log in under a minute)."""
    rows.append((label, bool(cond)))
    print(("  OK    " if cond else "  FAIL  ") + label + (f" ({detail})" if detail and not cond else ""))


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r.stdout.strip()


def commit(cwd, name, text, msg):
    (Path(cwd) / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "commit", "-q", "-m", msg)
    return git(cwd, "rev-parse", "HEAD")


def land(main, base, case, branch):
    """One worktree whose content is LANDED on master, so the OLD proof is true of it."""
    tree = base / f"tree-{KEY[case]}"
    git(main, "worktree", "add", "-q", "-b", branch, str(tree), "master")
    commit(tree, f"{KEY[case]}.txt", f"{KEY[case]}\n", f"{case}\n\nRefs kushin77/agent-orchestrator#1265")
    git(main, "checkout", "-q", "master")
    (main / f"{KEY[case]}.txt").write_text(f"{KEY[case]}\n")
    git(main, "add", ".")
    git(main, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "commit", "-q",
        "-m", f"squash {case}\n\nRefs kushin77/agent-orchestrator#1265\nCloses #1265")
    git(main, "push", "-q", "origin", "master")
    git(main, "fetch", "-q", "origin")
    return tree


def ops_class(module):
    """The port, built from ``module``'s OWN OrphanOps.

    It has to be the module's own class, not one subclass: the liveness read
    lives on ``RepoOrphanOps.in_use``, so an Ops subclassing the REAL module
    would keep calling the real ``judge_use`` and the mutant would be falsified
    against itself — every arm green, the falsification vacuous. (Measured here:
    the first version of this section did exactly that, and the mutant came back
    with zero reds.)
    """

    class Ops(module.RepoOrphanOps):
        def open_pull_requests(self):
            return []

        def issue_states(self, issues):
            return {n: "open" for n in issues}

    return Ops


def walk(module, ops, apply=False):
    report = module.walk(ops, apply=apply, budget=BIG)
    return {orphan.name: orphan for orphan in report.orphans}, report


def reaps(main):
    path = main / ".fleet" / "reaped-branches.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def controls(module, tag):
    """The liveness control, one arm per behaviour — run against the real module
    AND against the mutant, so the arms must come out green on one and red on
    the other. Built fresh per run: the --apply arms remove what they reclaim.

    Returns its OWN list of ``(label, condition)``; the caller emits them into
    the program's tally. The two must never be the same list.
    """
    arms = []

    def check(label, cond, detail=""):
        arms.append((label, bool(cond)))

    base = work / tag
    base.mkdir(parents=True, exist_ok=True)
    spool = base / "ao-orch"
    holder = None
    try:
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(base / "origin.git")], check=True)
        main = base / "main"
        subprocess.run(["git", "clone", "-q", str(base / "origin.git"), str(main)], check=True, capture_output=True)
        commit(main, "seed.txt", "seed\n", "seed")
        git(main, "push", "-q", "origin", "master")
        git(main, "fetch", "-q", "origin")
        trees = {case: land(main, base, case, f"issue-91{ord(KEY[case]) % 10}") for case in CASES}

        # A: a live process whose working directory is inside it.
        holder = subprocess.Popen(["sleep", "600"], cwd=str(trees["A"]), start_new_session=True)
        # B: a recorded venue naming it — the measured shape, one absolute path.
        spool.mkdir(parents=True, exist_ok=True)
        (spool / "master-venue.txt").write_text(f"{trees['B']}\n")
        # C: git's own lock, with a reason.
        git(main, "worktree", "lock", "--reason", "claude agent fixture holder", str(trees["C"]))
        # D: deliberately nothing.

        ops = ops_class(module)(main, venue_roots=[spool])
        dry, _ = walk(module, ops, apply=False)
        found = {case: dry[str(trees[case])] for case in CASES}
        check("a live holder is refused BY NAME (A)", (not found["A"].reclaimable)
              and found["A"].outcome == module.REPORTED and "IN USE" in found["A"].detail
              and str(holder.pid) in found["A"].detail, found["A"].detail)
        check("a recorded venue is refused BY NAME (B)", (not found["B"].reclaimable)
              and "IN USE" in found["B"].detail and "master-venue.txt" in found["B"].detail, found["B"].detail)
        check("git's own lock is refused BY NAME (C)", (not found["C"].reclaimable)
              and "IN USE" in found["C"].detail and "claude agent fixture holder" in found["C"].detail,
              found["C"].detail)
        check("a genuinely dead tree is still reclaimable (D)", found["D"].reclaimable
              and found["D"].outcome == module.WOULD_RECLAIM)

        applied, _ = walk(module, ops, apply=True)
        check("--apply leaves the three in-use trees exactly where they were", trees["A"].exists()
              and trees["B"].exists() and trees["C"].exists())
        check("--apply still reclaims the dead tree", not trees["D"].exists()
              and applied[str(trees["D"])].outcome == module.RECLAIMED)
        records = reaps(main)
        check("the ledger holds exactly one reap, and it names the dead tree",
              len(records) == 1 and records[0]["worktree"] == str(trees["D"]),
              json.dumps(records)[:200])

        # An UNREADABLE liveness signal is CANNOT-ASSESS — never "not in use".
        tree_e = base / "tree-e"
        git(main, "worktree", "add", "-q", "-b", "issue-9199", str(tree_e), "master")
        commit(tree_e, "e.txt", "e\n", "E\n\nRefs kushin77/agent-orchestrator#1265")
        git(main, "checkout", "-q", "master")
        (main / "e.txt").write_text("e\n")
        git(main, "add", ".")
        git(main, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "commit", "-q",
            "-m", "squash E\n\nRefs kushin77/agent-orchestrator#1265\nCloses #1265")
        git(main, "push", "-q", "origin", "master")
        git(main, "fetch", "-q", "origin")
        (spool / "bad-venue.json").write_text("{not json", encoding="utf-8")
        blind, report = walk(module, ops, apply=True)
        check("an unreadable venue record is CANNOT-ASSESS, never 'not in use' (E)",
              module.LIVENESS_UNMEASURED in report.unmeasured and not report.assessable
              and not report.ok and blind[str(tree_e)].outcome == module.REPORTED and tree_e.exists(),
              json.dumps(report.unmeasured)[:200])
    finally:
        if holder is not None:
            for signal in (15, 9):
                try:
                    os.kill(holder.pid, signal)
                except OSError:
                    break
                time.sleep(0.3)
    return arms


def load_mutant():
    src = (root / "governance" / "reconcile" / "orphans.py").read_text(encoding="utf-8")
    if src.count(GUARD) != 1:
        raise SystemExit(
            "check-reconcile-orphans: CANNOT-ASSESS — the liveness guard this section falsifies is not in "
            "governance/reconcile/orphans.py any more, so the mutant would not have applied and the "
            "falsification would silently test nothing"
        )
    mutant = work / "mutant-orphans.py"
    mutant.write_text(src.replace(GUARD, GUARD.replace("if positive:", "if False:  # the guard, removed"), 1),
                      encoding="utf-8")
    if mutant.read_text(encoding="utf-8") == src:
        raise SystemExit("check-reconcile-orphans: CANNOT-ASSESS — the mutation did not change orphans.py")
    spec = importlib.util.spec_from_file_location("reconcile_orphans_mutant", mutant)
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_orphans_mutant"] = module
    spec.loader.exec_module(module)
    # Measuring that the mutation DID something, not assuming it: a mutant whose
    # guard survived would make the whole falsification vacuous.
    probe = module.judge_use([module.Signal("probe", True, hit="a positive signal")])
    if probe.verdict == module.IN_USE:
        raise SystemExit(
            "check-reconcile-orphans: CANNOT-ASSESS — the mutant still answers IN USE for a positive signal, "
            f"so the guard was not removed ({probe})"
        )
    return module


# The venue spool honours its env seam, and .fleet/venues is a default store.
os.environ[o.VENUE_SPOOL_ENV] = str(work / "seam-spool")
try:
    declared = o.RepoOrphanOps(work / "no-such-root").venue_roots
finally:
    os.environ.pop(o.VENUE_SPOOL_ENV, None)
emit("the venue spool honours $AO_VENUE_SPOOL", (work / "seam-spool") in declared, str(declared))
emit(".fleet/venues is a default venue store",
     (work / "no-such-root" / o.VENUE_DIR) in declared, str(declared))

print("  -- the control, on the code under test --")
for label, cond in list(controls(o, "liveness-real")):
    emit(label, cond)

print("  -- the same control, with the liveness guard removed (the falsification) --")
reds = [label for label, cond in list(controls(load_mutant(), "liveness-mutant")) if not cond]
for label in reds:
    print(f"    RED   {label}")
# Matched on the arm's own PREFIX: a looser substring matches the ledger arm,
# whose label mentions the dead tree too (measured — it made this arm fail on a
# falsification that had in fact worked).
emit("the in-use arms red on the mutant — the counterfactual that makes them controls",
     any(label.startswith("a live holder") for label in reds)
     and any(label.startswith("a recorded venue") for label in reds)
     and any(label.startswith("git's own lock") for label in reds), str(reds))
emit("the dead-tree arm stays GREEN on the mutant, so what the arms measure is the liveness verdict",
     not any(label.startswith("a genuinely dead tree") for label in reds), str(reds))
raise SystemExit(0 if all(cond for _, cond in rows) else 1)
PY
rc=$?
cat "$work/liveness.log"
if [ "$rc" -ne 0 ]; then
  echo "  FAIL  the liveness controls or their falsification did not hold (rc=$rc)" >&2
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
  real_out="$(AO_GATE_VENUE="${AO_GATE_VENUE:-lane}" python3 - "$main_root" "$root/$budget" <<'PY' 2>&1
import os
import sys
from pathlib import Path
from governance.reconcile import orphans

# #1620/#1655: orphan-worktree / orphan-branch / orphan-issue-lane / orphan-pr
# are counted against the WHOLE box's state (every concurrent session's
# worktrees, branches, lane records, open PRs — including the merge trains
# themselves), so they swing with unrelated fleet activity, not this
# checkout's own diff — orphans.venue_classify() is the single source of
# truth for which kinds are advisory in the default "lane" venue.
# orphan-directive is the one kind that stays blocking everywhere.
venue = os.environ.get("AO_GATE_VENUE", "lane")

budget, expired = orphans.load_budget(sys.argv[2])
report = orphans.walk(orphans.RepoOrphanOps(Path(sys.argv[1])), apply=False, budget=budget, budget_expired=expired)
print("orphan-walk (dry-run): " + ", ".join(f"{k}={v}" for k, v in report.counts.items()))
for kind, reason in report.unmeasured.items():
    print(f"  unmeasured {kind}: {reason}")

blocking_names, advisory_names = orphans.venue_classify(report.exceeded, venue)
for name in advisory_names:
    print(f"  NOTE {name} (advisory in lane venue, #1620; blocking in AO_GATE_VENUE=attestation)")
for name in blocking_names:
    print(f"  NOT-OK {name}")

sys.exit(2 if not report.assessable else (1 if blocking_names else 0))
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
