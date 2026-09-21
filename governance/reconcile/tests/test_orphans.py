"""The orphan walk: five kinds named, reclaim only with evidence, red above budget (#1301)."""

from __future__ import annotations

import json
import subprocess

import pytest

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
        self.use: dict[str, o.Use] = {}
        self.default_use = o.Use(o.NOT_IN_USE, "fake: no git lock, no holder, no venue record")
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

    def in_use(self, entry):
        return self.use.get(entry.path, self.default_use)

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
    # #1440: the reap is recorded only AFTER the removal it describes happened,
    # so a removal that fails can never leave a ledger entry claiming a reap.
    assert order == ["remove_worktree", "record_reaped", "delete_local", "record_reaped"], (
        "the tip is recorded only after a removal that happened"
    )
    assert ("remove_worktree", "/repo/.claude/worktrees/agent-dirty") not in ops.calls


def test_the_budget_reds_by_name_and_a_reclaimed_orphan_does_not_count():
    ops = FakeOps()
    ops.trees.append(o.Worktree("/w1", "issue-1", "a" * 40))
    ops.trees.append(o.Worktree("/w2", "issue-2", "b" * 40))
    ops.landed = {"a" * 40}
    budget = {**BIG, o.ORPHAN_WORKTREE: 1}
    assert o.walk(ops, apply=False, budget=budget).exceeded == ["orphan-budget-exceeded:orphan-worktree:2/1"]
    assert o.walk(ops, apply=True, budget=budget).exceeded == [], "reclaimed with evidence, so under budget"


# --- venue_classify (#1620, #1655): box-wide counts are advisory in the lane
# venue. orphan-pr joined worktree/branch/issue-lane 2026-09-20 by explicit
# owner decision — it counts every open PR without a lane record across the
# WHOLE box (including the merge trains themselves), the same box-wide census
# its three siblings already are, not a per-artifact fact. orphan-directive
# is the only kind left blocking everywhere: one directive names one closed
# issue, settleable from this checkout alone.


def test_venue_classify_downgrades_box_wide_kinds_in_the_lane_venue():
    exceeded = [
        "orphan-budget-exceeded:orphan-worktree:50/36",
        "orphan-budget-exceeded:orphan-branch:99/95",
        "orphan-budget-exceeded:orphan-issue-lane:42/25",
        "orphan-budget-exceeded:orphan-pr:30/20",
        "orphan-budget-exceeded:orphan-directive:120/115",
    ]
    blocking, advisory = o.venue_classify(exceeded, "lane")
    assert advisory == exceeded[:4], "worktree/branch/issue-lane/pr are box-wide, advisory in the lane venue"
    assert blocking == exceeded[4:], "directive is the one per-artifact fact, blocking everywhere"


def test_venue_classify_blocks_everything_in_the_attestation_venue():
    exceeded = [
        "orphan-budget-exceeded:orphan-worktree:50/36",
        "orphan-budget-exceeded:orphan-branch:99/95",
        "orphan-budget-exceeded:orphan-issue-lane:42/25",
        "orphan-budget-exceeded:orphan-pr:30/20",
        "orphan-budget-exceeded:orphan-directive:120/115",
    ]
    blocking, advisory = o.venue_classify(exceeded, "attestation")
    assert blocking == exceeded, "the serial post-merge attestation enforces every kind for real"
    assert advisory == []


def test_venue_classify_treats_any_non_attestation_string_as_lane():
    exceeded = ["orphan-budget-exceeded:orphan-worktree:2/1"]
    blocking, advisory = o.venue_classify(exceeded, "")
    assert advisory == exceeded and blocking == [], "unset/unknown AO_GATE_VENUE defaults to lane behaviour"


def test_venue_classify_orphan_directive_stays_blocking_in_the_lane_venue():
    exceeded = ["orphan-budget-exceeded:orphan-directive:120/115"]
    blocking, advisory = o.venue_classify(exceeded, "lane")
    assert blocking == exceeded and advisory == [], "orphan-directive is a per-artifact fact, never advisory"


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


# --- "is this IN USE?", the second question (#1440) --------------------------


def test_judge_use_prefers_a_positive_signal_over_a_blind_one():
    use = o.judge_use([
        o.Signal("git-worktree-lock", True, hit="git holds this worktree's lock (reason: claude agent)"),
        o.Signal("holder-process", False, note="no readable /proc on this platform"),
    ])
    assert use.verdict == o.IN_USE and use.in_use and use.known
    assert "claude agent" in use.evidence


def test_judge_use_is_not_in_use_only_when_every_signal_was_read_and_negative():
    use = o.judge_use([
        o.Signal("git-worktree-lock", True, note="git reports no worktree lock"),
        o.Signal("holder-process", True, note="412 process(es) checked, none inside"),
    ])
    assert use.verdict == o.NOT_IN_USE and not use.in_use and use.known
    assert "412" in use.evidence, "a clear verdict still reports the coverage it measured"


def test_judge_use_never_reads_an_unreadable_signal_as_clear():
    use = o.judge_use([o.Signal("holder-process", False, note="no readable /proc on this platform")])
    assert use.verdict == o.LIVENESS_CANNOT_ASSESS and not use.known and not use.in_use
    assert "no readable /proc" in use.evidence


def test_judge_use_with_no_signal_at_all_is_cannot_assess():
    use = o.judge_use([])
    assert use.verdict == o.LIVENESS_CANNOT_ASSESS and "no liveness signal" in use.evidence


def test_a_content_landed_worktree_that_is_in_use_is_refused_by_name_and_never_reclaimed():
    """The measured case (#1440): the work IS landed, and the tree is still in use."""
    ops = FakeOps()
    tree = "/repo/.claude/worktrees/agent-live"
    ops.trees.append(o.Worktree(tree, "issue-1265", "5" * 40))
    ops.landed = {"5" * 40}
    ops.use = {tree: o.Use(o.IN_USE, "holder-process: live process with a working directory inside it: 4242 (claude)")}

    dry = o.walk(ops, apply=False, budget=BIG)
    found = next(x for x in dry.by_kind(o.ORPHAN_WORKTREE) if x.name == tree)
    assert found.reclaimable is False and found.outcome == o.REPORTED
    assert "IN USE" in found.detail and "4242 (claude)" in found.detail, "the refusal names WHAT and WHY"
    assert found.use.startswith(o.IN_USE), "and names it machine-readably too"
    assert "left alone" in found.remedy and dry.assessable, "an in-use tree is a finding, not an unmeasured walk"

    applied = o.walk(ops, apply=True, budget=BIG)
    assert applied.by_kind(o.ORPHAN_WORKTREE)[0].outcome == o.REPORTED
    assert ops.calls == [], "an in-use tree is never touched, not even to record a reap"


def test_a_worktree_whose_liveness_could_not_be_measured_refuses_and_reds_the_walk():
    ops = FakeOps()
    tree = "/repo/.claude/worktrees/agent-blind"
    ops.trees.append(o.Worktree(tree, "issue-1265", "5" * 40))
    ops.landed = {"5" * 40}
    ops.use = {tree: o.Use(o.LIVENESS_CANNOT_ASSESS, "holder-process: no readable /proc on this platform")}

    report = o.walk(ops, apply=True, budget=BIG)
    found = report.by_kind(o.ORPHAN_WORKTREE)[0]
    assert found.reclaimable is False and found.outcome == o.REPORTED
    assert "could NOT be measured" in found.detail
    assert not report.assessable and not report.ok, "an unmeasured liveness read is CANNOT-ASSESS"
    assert tree in report.unmeasured[o.LIVENESS_UNMEASURED]
    assert o.LIVENESS_UNMEASURED not in o.KINDS, "the unmeasured key must not shadow a kind's budget"
    assert ops.calls == [], "nothing unmeasured is ever reclaimed"


def test_a_genuinely_dead_worktree_is_still_reclaimed():
    """The negative that keeps the guard honest: a liveness check that refuses
    everything is not a control (#1440)."""
    ops = FakeOps()
    tree = "/repo/.claude/worktrees/agent-dead"
    ops.trees.append(o.Worktree(tree, "issue-1265", "5" * 40))
    ops.landed = {"5" * 40}
    assert ops.default_use.verdict == o.NOT_IN_USE and ops.use == {}

    dry = o.walk(ops, apply=False, budget=BIG)
    assert next(x for x in dry.by_kind(o.ORPHAN_WORKTREE) if x.name == tree).outcome == o.WOULD_RECLAIM
    applied = o.walk(ops, apply=True, budget=BIG)
    assert next(x for x in applied.by_kind(o.ORPHAN_WORKTREE) if x.name == tree).outcome == o.RECLAIMED
    assert ("remove_worktree", tree) in ops.calls and ("record_reaped", "5" * 40) in ops.calls


def test_a_failed_removal_records_no_reap():
    """#1440 acceptance: ``record_reaped`` and the removal cannot disagree — a
    removal that did not happen is not recorded as a reap."""

    class Refusing(FakeOps):
        def remove_worktree(self, path):
            self.calls.append(("remove_worktree", path))
            raise RuntimeError("fatal: cannot remove a locked working tree")

    ops = Refusing()
    tree = "/repo/.claude/worktrees/agent-landed"
    ops.trees.append(o.Worktree(tree, "issue-1265", "5" * 40))
    ops.landed = {"5" * 40}

    report = o.walk(ops, apply=True, budget=BIG)
    found = report.by_kind(o.ORPHAN_WORKTREE)[0]
    assert found.outcome == o.FAILED and "reclaim failed" in found.detail
    assert [call[0] for call in ops.calls] == ["remove_worktree"], (
        "the removal was attempted and NO reap was recorded — the ledger cannot claim a reap that did not happen"
    )


def test_the_lock_reason_is_read_from_gits_own_porcelain():
    """The measured shape, git 2.53.0 (module docstring, #1440)."""
    porcelain = (
        "worktree /tmp/exp1/main\nHEAD 350c18ed1fe6750c55e694559659fba1a455b7bf\nbranch refs/heads/master\n\n"
        "worktree /tmp/exp1/locked-tree\nHEAD 350c18ed1fe6750c55e694559659fba1a455b7bf\n"
        "branch refs/heads/issue-900\nlocked claude agent fixture holder\n\n"
        "worktree /tmp/exp1/bare-lock\nHEAD 350c18ed1fe6750c55e694559659fba1a455b7bf\ndetached\nlocked\n"
    )
    assert o._lock_reasons(porcelain) == {
        "/tmp/exp1/locked-tree": "claude agent fixture holder",
        "/tmp/exp1/bare-lock": "",
    }


def test_a_venue_record_is_read_in_both_measured_shapes():
    assert o._declared_paths("/home/akushnir/ao-worktrees/ao-master-1789820219\n") == [
        "/home/akushnir/ao-worktrees/ao-master-1789820219"
    ]
    assert o._declared_paths('{"path": "/w/ao-1", "by": "lane"}', expect_json=True) == ["/w/ao-1"]
    assert o._declared_paths('{"path": "/w/ao-1"}') == [], "a JSON blob is not read as a path line"
    with pytest.raises(ValueError):
        o._declared_paths("{not json", expect_json=True)


def test_a_declared_venue_matches_through_a_trailing_slash_and_a_symlink(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    assert o._same_path(str(target) + "/", str(link))
    assert not o._same_path(str(target), str(tmp_path / "other"))


def test_a_venue_spool_that_does_not_exist_is_a_measured_zero_not_an_unreadable_signal(tmp_path):
    ops = o.RepoOrphanOps(tmp_path, venue_roots=[tmp_path / "no-such-spool"])
    naming, blind = ops._venue_declarations("/w/ao-1")
    assert naming == [] and blind == [], "a store that does not exist is zero, never unreadable"


def test_a_venue_record_that_cannot_be_parsed_is_unreadable_and_the_walk_cannot_assess(tmp_path):
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / "master-venue.json").write_text("{not json", encoding="utf-8")
    ops = o.RepoOrphanOps(tmp_path, venue_roots=[spool])
    naming, blind = ops._venue_declarations("/w/ao-1")
    assert naming == [] and blind and "could not be parsed" in blind[0]
    signals = [
        o.Signal("git-worktree-lock", True, note="git reports no worktree lock"),
        o.Signal("venue-record", False, note="; ".join(blind)),
    ]
    assert o.judge_use(signals).verdict == o.LIVENESS_CANNOT_ASSESS, "never 'not in use'"

