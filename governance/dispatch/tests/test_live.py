"""The live dispatch projection (issue #885): a real read of the ledger + snapshot."""

from __future__ import annotations

from datetime import datetime, timezone

import claims
import live
from model import Issue, Snapshot


def _board(now):
    return Snapshot(
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="test",
        issues={
            1: Issue(1, "frontier", milestone="M"),
            2: Issue(2, "second", milestone="M"),
        },
    )


def test_project_reflects_a_live_claim(tmp_path):
    now = datetime.now(timezone.utc)
    board = _board(now)
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"
    claims.claim(1, "agent-a", "lane-a", board, ledger=ledger, lock_dir=locks, now=now)

    projection = live.project(board, ledger=ledger, queue_path=tmp_path / "no-queue.yaml")
    assert len(projection["live_claims"]) == 1
    record = projection["live_claims"][0]
    assert record["issue"] == 1
    assert record["agent"] == "agent-a"
    assert record["lane"] == "lane-a"
    assert projection["active_milestone"] == "M"


def test_project_is_deterministic_against_the_same_store(tmp_path):
    """Two independent reads of the same real store must agree byte-for-byte —
    this is the drift the gate provocation exercises: a caller pinning a
    serialised projection instead of re-reading the store."""
    now = datetime.now(timezone.utc)
    board = _board(now)
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"
    claims.claim(1, "agent-a", "lane-a", board, ledger=ledger, lock_dir=locks, now=now)

    first = live.project(board, ledger=ledger, queue_path=tmp_path / "no-queue.yaml")
    second = live.project(board, ledger=ledger, queue_path=tmp_path / "no-queue.yaml")
    assert first == second


def test_render_is_human_readable(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    board = _board(now)
    projection = live.project(board, ledger=tmp_path / "claims", queue_path=tmp_path / "no-queue.yaml")
    text = live.render(projection)
    assert "live: milestone=M" in text
