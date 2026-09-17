"""The live disk-vs-heartbeat projection (issue #885)."""

from __future__ import annotations

from pathlib import Path

from governance.reconcile import live


def test_matched_when_worktree_exists(root: Path, beaten):
    worktree = root / "wt"
    worktree.mkdir()
    beaten(worktree=str(worktree))
    rows = live.project(root)
    assert len(rows) == 1
    assert rows[0].match == live.MATCHED
    assert rows[0].worktree_present is True


def test_drift_when_worktree_is_gone(root: Path, beaten):
    missing = root / "nowhere"
    beaten(worktree=str(missing))
    rows = live.project(root)
    assert len(rows) == 1
    assert rows[0].match == live.DRIFT
    assert not rows[0].worktree_present
    assert str(missing) in rows[0].detail


def test_no_worktree_recorded_is_matched(root: Path, beaten):
    beaten(worktree="")
    rows = live.project(root)
    assert rows[0].match == live.MATCHED


def test_rows_validate_against_the_frozen_schema(root: Path, beaten):
    worktree = root / "wt"
    worktree.mkdir()
    beaten(worktree=str(worktree))
    rows = live.project(root)
    live.validate(rows)  # must not raise


def test_describe_names_the_drifted_session(root: Path, beaten):
    missing = root / "nowhere"
    beaten(worktree=str(missing))
    rows = live.project(root)
    text = live.describe(rows)
    assert "DRIFT" in text
