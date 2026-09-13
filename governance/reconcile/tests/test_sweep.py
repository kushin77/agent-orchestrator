"""The sweep: what it reclaims, what it parks, and what it refuses to destroy.

The centre of gravity is the third case. A lane whose work exists nowhere but its
own worktree must survive the sweep — that is the whole reason the teardown is
three-way instead of a single ``git worktree remove --force``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.reconcile.heartbeat import ORPHAN, SHELVED, SUSPECT, stamp
from governance.reconcile.sweep import (
    FAILED_OUTCOME,
    PARKED,
    RECLAIMED,
    REPORTED,
    SHELVED_OUTCOME,
    RepoOps,
    describe,
    sweep,
)

from conftest import AGENT, BRANCH, SESSION, WORKTREE, FakeOps  # noqa: E402

OLD = 1_000_000.0
NOW = OLD + 20 * 60  # past a 15-minute TTL
FRESH = OLD + 10  # inside it


def beat(root: Path, *, at: float = OLD, **overrides) -> None:
    stamp(
        overrides.pop("session_id", SESSION),
        issue=overrides.pop("issue", 304),
        agent=overrides.pop("agent", AGENT),
        root=root,
        lane=overrides.pop("lane", "governance-reconcile"),
        worktree=overrides.pop("worktree", WORKTREE),
        branch=overrides.pop("branch", BRANCH),
        at=at,
        **overrides,
    )


def test_an_empty_board_sweeps_clean(root: Path):
    report = sweep(root, at=NOW, ops=FakeOps())
    assert report.actions == []
    assert report.to_json()["counts"][RECLAIMED] == 0


def test_a_live_session_is_left_alone(root: Path):
    beat(root, at=NOW)
    ops = FakeOps()
    report = sweep(root, at=NOW, alive={SESSION: True}, ops=ops)
    assert [action.outcome for action in report.actions] == [REPORTED]
    assert ops.calls == []


def test_a_suspect_session_is_reported_and_not_touched(root: Path):
    """A fresh beat behind a dead pid: flagged, never reclaimed on that alone."""
    beat(root, at=FRESH)
    ops = FakeOps()
    report = sweep(root, at=NOW - 19 * 60, alive={SESSION: False}, ops=ops)
    assert report.actions[0].status == SUSPECT
    assert report.actions[0].outcome == REPORTED
    assert ops.calls == []


def test_a_stale_session_whose_worktree_is_gone_is_reconciled(root: Path):
    """The common crash: the worktree is gone, but the lane record and claim stay."""
    beat(root, at=OLD)
    ops = FakeOps(present=False)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == RECLAIMED
    assert ops.calls == ["forget-lane", "release-claim", "clear-heartbeat"]


def test_a_landed_lane_is_fully_reclaimed(root: Path):
    """Requirement 3, verification 3: the orphaned directory is pruned."""
    beat(root, at=OLD)
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == RECLAIMED
    assert ops.calls == [
        "remove-worktree",
        "delete-local-branch",
        "delete-remote-branch",
        "forget-lane",
        "release-claim",
        "clear-heartbeat",
    ]


def test_a_lane_preserved_only_remotely_is_parked_not_deleted(root: Path):
    """The remote branch is the only copy of the work: reclaim the disk, keep the branch."""
    beat(root, at=OLD)
    ops = FakeOps(remotely=True)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    action = report.actions[0]
    assert action.outcome == PARKED
    assert "delete-remote-branch" not in ops.calls
    assert "remove-worktree" in ops.calls
    assert any(step.action == "keep-remote-branch" for step in action.steps)
    assert "release-claim" in ops.calls  # the issue is unlocked; the branch is the work


def test_unmerged_work_is_shelved_and_the_issue_stays_locked(root: Path):
    """The asymmetry, asserted: work is never traded for an unlocked issue."""
    beat(root, at=OLD)
    ops = FakeOps(present=True, on_main=False, remotely=False)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    action = report.actions[0]
    assert action.outcome == SHELVED_OUTCOME
    assert ops.calls == ["mark-shelved"]
    assert "release-claim" not in ops.calls
    assert "remove-worktree" not in ops.calls
    assert "unmerged" in ops.shelved_reason or "TTL" in ops.shelved_reason


def test_a_dry_run_decides_without_acting(root: Path):
    beat(root, at=OLD)
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=False, ops=ops)
    assert report.applied is False
    assert report.actions[0].outcome == RECLAIMED  # the decision is still reported
    assert ops.calls == []
    assert any(step.action == "plan" for step in report.actions[0].steps)


def test_a_dry_run_reports_which_case_a_lane_is_in(root: Path):
    beat(root, at=OLD)
    assert sweep(root, at=NOW, ops=FakeOps(present=False)).actions[0].outcome == RECLAIMED
    assert sweep(root, at=NOW, ops=FakeOps(remotely=True)).actions[0].outcome == PARKED
    assert sweep(root, at=NOW, ops=FakeOps()).actions[0].outcome == SHELVED_OUTCOME


def test_a_failing_step_is_reported_and_the_rest_still_run(root: Path):
    beat(root, at=OLD)
    ops = FakeOps(on_main=True, fail=("remove-worktree",))
    report = sweep(root, at=NOW, apply=True, ops=ops)
    action = report.actions[0]
    assert action.outcome == FAILED_OUTCOME
    assert any(step.outcome == "failed" for step in action.steps)
    assert "release-claim" in ops.calls  # the remaining work was not abandoned


def test_a_shelved_lane_self_heals_once_its_work_lands(root: Path):
    """A shelved session is re-evaluated every pass, not written off."""
    beat(root, at=OLD, state=SHELVED, note="unmerged work")
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == RECLAIMED
    assert "remove-worktree" in ops.calls


def test_a_shelved_lane_with_work_still_at_risk_stays_shelved(root: Path):
    beat(root, at=OLD, state=SHELVED, note="unmerged work")
    ops = FakeOps(present=True, on_main=False, remotely=False)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == SHELVED_OUTCOME
    assert ops.calls == ["mark-shelved"]


def test_killing_a_process_makes_the_sweep_flag_that_session(root: Path):
    """Requirement 2's verification, without killing anything: the pid is the seam."""
    beat(root, at=FRESH)
    report = sweep(root, at=FRESH + 1, alive={SESSION: False}, ops=FakeOps())
    assert report.actions[0].status == SUSPECT
    assert SESSION in str(report.actions[0])


def test_several_sessions_are_each_dispositioned_on_their_own_facts(root: Path):
    beat(root, at=NOW, session_id="live-one", issue=1)
    beat(root, at=OLD, session_id="dead-one", issue=2, worktree="/lanes/gone")
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=True, alive={"live-one": True, "dead-one": True}, ops=ops)
    outcomes = {action.session_id: action.outcome for action in report.actions}
    assert outcomes == {"live-one": REPORTED, "dead-one": RECLAIMED}
    assert len(report.shelved) == 0


def test_the_report_is_machine_readable(root: Path):
    beat(root, at=OLD)
    payload = sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True)).to_json()
    assert payload["counts"][RECLAIMED] == 1
    assert payload["actions"][0]["issue"] == 304
    assert payload["actions"][0]["steps"]


def test_the_description_shows_the_case_and_the_steps(root: Path):
    beat(root, at=OLD)
    text = describe(sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True)))
    assert "reconcile (apply)" in text
    assert RECLAIMED in text
    assert "remove-worktree" in text


def test_sweep_refuses_to_run_without_an_operations_port(root: Path):
    with pytest.raises(ValueError):
        sweep(root, at=NOW)


def test_the_real_port_refuses_an_unknown_session_id(tmp_path: Path):
    """The real port is only constructed here; its git effects are exercised by the gate."""
    ops = RepoOps(tmp_path)
    assert ops.worktree_present("") is False
    assert ops.worktree_present(str(tmp_path)) is True
