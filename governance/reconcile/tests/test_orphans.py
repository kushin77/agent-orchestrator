"""The orphan walk: five kinds named, reclaim only with evidence, red above budget (#1301)."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

from governance.reconcile import orphans as o

LANE = {"lane_id": "lane00000001", "session_id": "lane00000001", "issue": 1301, "branch": "issue-1301", "worktree": "/lanes/ao-1301"}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "gate@example.com")
    _git(repo, "config", "user.name", "Gate")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


class FakeOps:
    def __init__(self, **overrides):
        self.records = [dict(LANE)]
        self.trees = [
            o.Worktree("/repo", "master", "0" * 40, primary=True),
            o.Worktree("/lanes/ao-1301", "issue-1301", "1" * 40),
        ]
        self.branches = ["master", "issue-1301"]
        self.tips = {"issue-1301": "1" * 40}
        self.prs: list[o.PullRequest] | None = [o.PullRequest(1400, "issue-1301")]
        self.states: dict[int, str] | None = {1301: "open"}
        self.directives: list[o.Directive] = []
        self.landed: set[str] = set()
        self.dirt: dict[str, list[str]] = {}
        self.calls: list[tuple] = []
        self.__dict__.update(overrides)

    def lane_records(self):
        return self.records

    def worktrees(self):
        return self.trees

    def local_branches(self):
        return self.branches

    def branch_tip(self, branch):
        return self.tips.get(branch, "")

    def open_pull_requests(self):
        if self.prs is None:
            raise o.Unmeasured("gh could not be run")
        return self.prs

    def issue_states(self, issues):
        if self.states is None:
            raise o.Unmeasured("the board could not be read")
        return {n: self.states.get(n, "open") for n in issues}

    def sent_directives(self):
        return self.directives

    def content_landed(self, ref):
        return ref in self.landed

    def foreign_dirt(self, path):
        return self.dirt.get(path, [])

    def record_reaped(self, **kw):
        self.calls.append(("record_reaped", kw["head_sha"]))

    def remove_worktree(self, path):
        self.calls.append(("remove_worktree", path))
        return "removed"

    def delete_local_branch(self, branch):
        self.calls.append(("delete_local", branch))
        return "deleted"


BIG = {kind: 100 for kind in o.KINDS}


def names(report, kind):
    return [orphan.name for orphan in report.by_kind(kind)]


def test_a_lane_bound_fleet_has_no_orphans():
    report = o.walk(FakeOps(), budget=BIG)
    assert report.orphans == [] and report.assessable and report.ok


def test_a_worktree_no_lane_names_is_an_orphan_worktree():
    ops = FakeOps()
    ops.trees.append(o.Worktree("/repo/.claude/worktrees/agent-abc", "issue-1265", "2" * 40))
    report = o.walk(ops, budget=BIG)
    assert names(report, o.ORPHAN_WORKTREE) == ["/repo/.claude/worktrees/agent-abc"]
    found = report.by_kind(o.ORPHAN_WORKTREE)[0]
    assert found.reclaimable is False and found.outcome == o.REPORTED and "NOT content-landed" in found.detail


def test_a_branch_with_no_lane_no_worktree_no_pr_is_an_orphan_branch_with_its_sha():
    ops = FakeOps()
    ops.branches.append("issue-1003")
    ops.tips["issue-1003"] = "3" * 40
    report = o.walk(ops, budget=BIG)
    found = report.by_kind(o.ORPHAN_BRANCH)
    assert [f.name for f in found] == ["issue-1003"] and found[0].sha == "3" * 40
    assert "never deleted" in found[0].remedy


def test_a_branch_that_is_an_open_prs_head_is_not_an_orphan_branch():
    ops = FakeOps()
    ops.branches.append("issue-1378")
    ops.tips["issue-1378"] = "4" * 40
    ops.prs.append(o.PullRequest(1380, "issue-1378", "lane: lane00000001"))
    report = o.walk(ops, budget=BIG)
    assert names(report, o.ORPHAN_BRANCH) == []


def test_a_pr_with_no_lane_line_and_no_lane_branch_is_an_orphan_pr():
    ops = FakeOps()
    ops.prs.append(o.PullRequest(1377, "issue-1273", "## Summary\nno binding"))
    ops.prs.append(o.PullRequest(1378, "issue-1274", "lane: lane00000001"))
    ops.prs.append(o.PullRequest(1379, "issue-1275", "lane: deadbeefcafe"))
    report = o.walk(ops, budget=BIG)
    assert names(report, o.ORPHAN_PR) == ["1377", "1379"]
    assert "unknown" in report.by_kind(o.ORPHAN_PR)[1].detail


def test_a_lane_whose_issue_is_closed_is_an_orphan_issue_lane():
    ops = FakeOps(states={1301: "closed"})
    report = o.walk(ops, budget=BIG)
    found = report.by_kind(o.ORPHAN_ISSUE_LANE)
    assert [f.name for f in found] == ["lane00000001"]
    assert "close --lane lane00000001" in found[0].remedy
    assert found[0].reclaimable is False, "a lane reaches terminal only through its own close-out"


def test_the_walk_run_from_a_lane_worktree_reads_the_fleets_state(tmp_path: Path):
    """#1436: ``.fleet/`` lives beside the MAIN checkout's git dir and is never
    checked out into a linked worktree, so the two kinds read from it came back
    **0** and the three read from git were *inflated* (nothing was left to
    exclude) when the same walk was run from a lane — the disagreement between
    the gate's walk and the issue's own ``Verify:`` command. The port's own
    docstring already required the main checkout; this asserts it is true of a
    caller that passes a lane.
    """
    repo = _scratch_repo(tmp_path)
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-q", "-b", "issue-1436", str(lane))
    (repo / ".fleet" / "lanes").mkdir(parents=True)
    (repo / ".fleet" / "lanes" / "lane00000001.json").write_text(
        json.dumps({**LANE, "worktree": str(lane)}), encoding="utf-8"
    )
    # The premise the disagreement rests on: a linked worktree has no `.fleet`.
    assert not (lane / ".fleet").exists()

    assert o.fleet_root(repo) == repo.resolve()
    assert o.fleet_root(lane) == repo.resolve(), "a lane resolves to the checkout that owns the state"
    # ...and the consequence: the lane's port reads the FLEET's lane records, so
    # the lane it names is excluded rather than counted as an orphan.
    assert [record["lane_id"] for record in o.RepoOrphanOps(lane).lane_records()] == ["lane00000001"]
    assert o.RepoOrphanOps(lane).root == repo.resolve()
    # A root git cannot answer for is left alone rather than relocated.
    stranger = tmp_path / "not-a-repo"
    assert o.fleet_root(stranger) == stranger


def test_a_directive_naming_a_closed_issue_is_an_orphan_directive():
    ops = FakeOps(states={1301: "open", 467: "closed"})
    ops.directives = [o.Directive("d-467", 467), o.Directive("d-1301", 1301), o.Directive("d-control", None)]
    report = o.walk(ops, budget=BIG)
    assert names(report, o.ORPHAN_DIRECTIVE) == ["d-467"]


def test_reclaim_only_with_evidence_and_only_under_apply():
    ops = FakeOps()
    ops.trees.append(o.Worktree("/repo/.claude/worktrees/agent-landed", "issue-1265", "5" * 40))
    ops.trees.append(o.Worktree("/repo/.claude/worktrees/agent-dirty", "issue-1266", "6" * 40))
    ops.branches += ["issue-1265", "issue-1266", "issue-1267"]
    ops.tips.update({"issue-1267": "7" * 40})
    ops.landed = {"5" * 40, "6" * 40, "7" * 40}
    ops.dirt = {"/repo/.claude/worktrees/agent-dirty": ["governance/x.py"]}

    dry = o.walk(ops, apply=False, budget=BIG)
    by_name = {orphan.name: orphan for orphan in dry.orphans}
    assert by_name["/repo/.claude/worktrees/agent-landed"].outcome == o.WOULD_RECLAIM
    assert by_name["/repo/.claude/worktrees/agent-dirty"].outcome == o.REPORTED
    assert by_name["issue-1267"].outcome == o.WOULD_RECLAIM
    assert ops.calls == [], "a dry run touches nothing"

    applied = o.walk(ops, apply=True, budget=BIG)
    by_name = {orphan.name: orphan for orphan in applied.orphans}
    assert by_name["/repo/.claude/worktrees/agent-landed"].outcome == o.RECLAIMED
    assert by_name["/repo/.claude/worktrees/agent-dirty"].outcome == o.REPORTED
    assert by_name["issue-1267"].outcome == o.RECLAIMED
    order = [call[0] for call in ops.calls]
    assert order == ["record_reaped", "remove_worktree", "record_reaped", "delete_local"], "the tip is recorded BEFORE removal"
    assert ("remove_worktree", "/repo/.claude/worktrees/agent-dirty") not in ops.calls


def test_the_budget_reds_by_name_and_a_reclaimed_orphan_does_not_count():
    ops = FakeOps()
    ops.trees.append(o.Worktree("/w1", "issue-1", "a" * 40))
    ops.trees.append(o.Worktree("/w2", "issue-2", "b" * 40))
    ops.landed = {"a" * 40}
    budget = {**BIG, o.ORPHAN_WORKTREE: 1}
    assert o.walk(ops, apply=False, budget=budget).exceeded == ["orphan-budget-exceeded:orphan-worktree:2/1"]
    assert o.walk(ops, apply=True, budget=budget).exceeded == [], "reclaimed with evidence, so under budget"


def test_an_unmeasured_source_is_cannot_assess_never_zero():
    report = o.walk(FakeOps(prs=None), budget=BIG)
    assert not report.assessable and o.ORPHAN_PR in report.unmeasured
    assert not report.ok
    report = o.walk(FakeOps(states=None), budget=BIG)
    assert set(report.unmeasured) == {o.ORPHAN_ISSUE_LANE, o.ORPHAN_DIRECTIVE}


def test_the_budget_document_is_read_and_expires(tmp_path: Path):
    path = tmp_path / "budget.yaml"
    path.write_text("budget:\n  orphan-branch: 85\n  orphan-pr: 11\nexpires: \"2026-10-02\"\n", encoding="utf-8")
    budget, expired = o.load_budget(path, today=date(2026, 9, 18))
    assert budget[o.ORPHAN_BRANCH] == 85 and budget[o.ORPHAN_PR] == 11 and budget[o.ORPHAN_WORKTREE] == 0
    assert expired is False
    budget, expired = o.load_budget(path, today=date(2026, 10, 3))
    assert expired is True and all(value == 0 for value in budget.values())
    assert o.load_budget(tmp_path / "missing.yaml") == ({kind: 0 for kind in o.KINDS}, False)


def test_the_repos_declared_budget_is_readable_and_not_yet_expired():
    root = Path(__file__).resolve().parents[3]
    budget, expired = o.load_budget(root / o.BUDGET_PATH, today=date(2026, 9, 18))
    assert not expired and all(budget[kind] > 0 for kind in o.KINDS)
