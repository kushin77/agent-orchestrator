"""The lane's close-out: terminal only by evidence, blocked by name otherwise (#1301).

Every step is driven against an injected port, so the ORDER (evidence before any
destructive step; tips recorded before deletion) and every ``closeout-blocked``
refusal are proven offline, and a dry run is proven to write nothing.
"""

from __future__ import annotations

from governance.lifecycle import lane_closeout as lc

RECORD = {
    "session_id": "abc123def456",
    "lane_id": "abc123def456",
    "issue": 1301,
    "agent_id": "gate",
    "lane": "governance",
    "branch": "issue-1301",
    "worktree": "/lanes/ao-1301-abc123de",
    "runtime": "claude-subagent",
    "opened_at": "2026-09-18T00:00:00Z",
}


class FakeOps:
    def __init__(self, **overrides):
        self.pr = lc.PullRequest(1400, "merged", "f" * 40)
        self.trailer = None
        self.issue = "closed"
        self.local = "a" * 40
        self.remote = "a" * 40
        self.landed = {self.local: True, self.remote: True}
        self.present = True
        self.foreign = []
        self.machine = [".board/focus.json"]
        self.tails = {"verify": "verify: PASS"}
        self.calls: list[tuple] = []
        self.__dict__.update(overrides)

    def pull_request_for(self, branch):
        return self.pr

    def trailer_finding(self, sha):
        return self.trailer

    def issue_state(self, issue):
        return self.issue

    def local_tip(self, branch):
        return self.local

    def remote_tip(self, branch):
        return self.remote

    def content_landed(self, ref):
        return self.landed.get(ref, False)

    def record_reaped(self, **kw):
        self.calls.append(("record_reaped", kw["head_sha"]))

    def delete_local_branch(self, branch):
        self.calls.append(("delete_local", branch))
        return f"deleted local {branch}"

    def delete_remote_branch(self, branch):
        self.calls.append(("delete_remote", branch))
        return f"deleted origin/{branch}"

    def worktree_present(self, path):
        return self.present

    def foreign_dirt(self, path):
        return self.foreign

    def machine_dirt(self, path):
        return self.machine

    def remove_worktree(self, path):
        self.calls.append(("remove_worktree", path))
        return f"removed {path}"

    def gate_tails(self, issue, lane):
        return self.tails

    def archive(self, lane_id, bundle):
        self.calls.append(("archive", lane_id, bundle))
        return f".fleet/lifecycle/lanes/{lane_id}.json"

    def forget_lane(self, lane_id):
        self.calls.append(("forget_lane", lane_id))

    def clear_session(self, lane_id):
        self.calls.append(("clear_session", lane_id))


def outcomes(result):
    return {step.name: step.outcome for step in result.steps}


def test_a_fully_evidenced_lane_reaches_terminal_with_its_bundle():
    ops = FakeOps()
    result = lc.closeout_lane(RECORD, ops, apply=True, now="2026-09-18T12:00:00Z")
    assert result.ok and result.blocked == []
    assert outcomes(result) == {
        "pr-merged": "skipped", "issue-closed": "skipped", "branch-reaped": "performed",
        "worktree-removed": "performed", "lane-archived": "performed",
    }
    names = [call[0] for call in ops.calls]
    # Tips are recorded BEFORE anything is deleted; the archive is written before the record goes.
    assert names.index("record_reaped") < names.index("delete_local")
    assert names.index("archive") < names.index("forget_lane") < names.index("clear_session")
    bundle = next(call[2] for call in ops.calls if call[0] == "archive")
    assert bundle["evidence"]["pr"] == 1400 and bundle["evidence"]["merge_commit"] == "f" * 40
    assert bundle["evidence"]["reaped"] == {"local": "a" * 40, "remote": "a" * 40}
    assert bundle["evidence"]["gate_tails"] == {"verify": "verify: PASS"}
    assert bundle["closed_at"] == "2026-09-18T12:00:00Z" and bundle["lane"] == RECORD
    worktree_step = next(step for step in result.steps if step.name == "worktree-removed")
    assert "ignored machine-managed state: .board/focus.json" in worktree_step.detail


def test_a_dry_run_plans_and_writes_nothing():
    ops = FakeOps()
    result = lc.closeout_lane(RECORD, ops, apply=False)
    assert result.ok
    assert outcomes(result)["branch-reaped"] == "planned"
    assert outcomes(result)["worktree-removed"] == "planned"
    assert outcomes(result)["lane-archived"] == "planned"
    assert ops.calls == []


def test_no_pull_request_blocks_and_withholds_everything_after():
    ops = FakeOps(pr=None)
    result = lc.closeout_lane(RECORD, ops, apply=True)
    assert result.blocked == ["closeout-blocked:pr-merged"]
    assert outcomes(result)["pr-merged"] == "blocked"
    assert all(outcomes(result)[name] == "withheld" for name in ("issue-closed", "branch-reaped", "worktree-removed", "lane-archived"))
    assert ops.calls == []


def test_an_open_pull_request_blocks():
    ops = FakeOps(pr=lc.PullRequest(1400, "open", ""))
    result = lc.closeout_lane(RECORD, ops, apply=True)
    assert result.blocked == ["closeout-blocked:pr-merged"]
    assert ops.calls == []


def test_a_landing_that_fails_the_trailer_predicate_blocks():
    ops = FakeOps(trailer="commit-missing-ticket-trailer:ffffffffffff")
    result = lc.closeout_lane(RECORD, ops, apply=True)
    assert result.blocked == ["closeout-blocked:pr-merged"]
    assert "commit-missing-ticket-trailer" in result.steps[0].detail


def test_an_open_issue_blocks_before_any_destructive_step():
    ops = FakeOps(issue="open")
    result = lc.closeout_lane(RECORD, ops, apply=True)
    assert result.blocked == ["closeout-blocked:issue-closed"]
    assert "Closes #1301" in result.steps[1].detail
    assert ops.calls == []


def test_an_unlanded_branch_tip_is_never_deleted():
    """The load-bearing negative: a remote tip that is not content-landed blocks
    branch-reaped, and neither tip is deleted nor the worktree removed."""
    ops = FakeOps(remote="b" * 40)
    ops.landed = {"a" * 40: True, "b" * 40: False}
    result = lc.closeout_lane(RECORD, ops, apply=True)
    assert result.blocked == ["closeout-blocked:branch-reaped"]
    assert "not content-landed" in result.steps[2].detail and "never deleted" in result.steps[2].detail
    assert [call for call in ops.calls if call[0] in ("delete_local", "delete_remote", "remove_worktree", "archive")] == []
    assert outcomes(result)["worktree-removed"] == "withheld"


def test_a_worktree_with_the_lanes_own_dirt_blocks_and_is_not_archived():
    ops = FakeOps(foreign=["governance/x.py"])
    result = lc.closeout_lane(RECORD, ops, apply=True)
    assert result.blocked == ["closeout-blocked:worktree-removed"]
    assert "governance/x.py" in result.steps[3].detail
    assert [call for call in ops.calls if call[0] in ("remove_worktree", "archive", "forget_lane")] == []


def test_a_branch_already_gone_and_no_worktree_still_archives():
    ops = FakeOps(local="", remote="", present=False)
    result = lc.closeout_lane(RECORD, ops, apply=True)
    assert result.ok
    assert outcomes(result)["branch-reaped"] == "skipped"
    assert outcomes(result)["worktree-removed"] == "skipped"
    assert [call[0] for call in ops.calls] == ["archive", "forget_lane", "clear_session"]


def test_a_record_without_its_binding_fields_is_blocked_by_name():
    result = lc.closeout_lane({"session_id": "x"}, FakeOps(), apply=True)
    assert result.blocked == ["closeout-blocked:pr-merged"]


def test_a_legacy_record_without_lane_id_closes_by_its_session_id():
    legacy = {k: v for k, v in RECORD.items() if k not in ("lane_id", "runtime", "opened_at")}
    result = lc.closeout_lane(legacy, FakeOps(), apply=True)
    assert result.ok and result.lane_id == "abc123def456"


def test_the_archive_lives_in_a_subdirectory_the_issue_journal_reader_cannot_mistake(tmp_path):
    """`reconcile/audit.py::landed_issues` reads `.fleet/lifecycle/*.json` as ISSUE
    journals and refuses a non-numeric stem; a lane archive beside them would
    turn the whole disk audit CANNOT-ASSESS."""
    path = lc.write_archive(tmp_path, "abc123def456", {"lane_id": "abc123def456"})
    assert path == tmp_path / ".fleet" / "lifecycle" / "lanes" / "abc123def456.json"
    assert lc.archived_lanes(tmp_path) == {"abc123def456"}
    assert list((tmp_path / ".fleet" / "lifecycle").glob("*.json")) == []
