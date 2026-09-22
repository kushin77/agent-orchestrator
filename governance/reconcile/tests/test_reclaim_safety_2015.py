"""Regression for #2015: a mid-edit lane must never be RECLAIMED as "landed".

Sibling to #2001 (fixed via #2016's live-verify-process exemption, which does
not cover this case: no verify process running, just an editing agent). The
predicate this guards is already implemented by #1902/#1893 — this test names
the exact measured incident (#2015/#1919: HEAD unchanged since cut, an
ancestor of origin/master, dirty worktree, heartbeat 940s old past a 15m TTL)
so the regression is traceable to its own issue rather than relying on #1897's
tests to be recognized as covering it.
"""

from __future__ import annotations

from pathlib import Path

from governance.reconcile.sweep import RECLAIMED, SHELVED_OUTCOME, RepoOps, sweep

from governance.reconcile.tests.test_reclaim_safety_1897 import NOW, beat, lane, scratch  # noqa: F401


def test_2015_mid_edit_lane_with_ancestor_head_is_shelved_not_reclaimed(scratch: Path, tmp_path: Path):
    """HEAD is an ancestor of origin/master (never committed) + a dirty tree.

    The ancestry test alone says "landed"; the dirty tree says otherwise. The
    dirty tree must win: shelved, worktree and edit both survive.
    """
    path = lane(scratch, tmp_path, "mid-edit-2015")
    (path / "in-progress.txt").write_text("uncommitted edit\n", encoding="utf-8")
    beat(scratch, "mid-edit-1", worktree=str(path), branch="mid-edit-2015", issue=2015)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    action = report.actions[0]
    assert action.outcome == SHELVED_OUTCOME, action.steps
    assert path.exists(), "the worktree holding the only copy of the work was destroyed"
    assert (path / "in-progress.txt").exists()


def test_2015_negative_control_landed_and_clean_is_still_reclaimed(scratch: Path, tmp_path: Path):
    """Same ancestor-HEAD, stale-heartbeat setup, but a clean tree: still reclaimed."""
    path = lane(scratch, tmp_path, "clean-2015")
    beat(scratch, "clean-2015-1", worktree=str(path), branch="clean-2015", issue=2015)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    action = report.actions[0]
    assert action.outcome == RECLAIMED, action.steps
    assert not path.exists()
