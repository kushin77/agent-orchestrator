"""The lane's session is stamped at open, cleared at close, judged by the audit (#917).

Measured 2026-09-16: ``.fleet/sessions/`` empty while 78 lane records existed, so
``reconcile status`` reported 0 orphans over a fleet full of them. These tests
pin the write that closes that gap and the rule that names its absence.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from governance.isolation import session
from governance.isolation.identity import SessionIdentity, mint
from governance.isolation.worktree import write_record, read_record
from governance.reconcile import heartbeat


def _identity(tmp_path: Path, *, opened: bool = True) -> SessionIdentity:
    identity = mint(917, "gate", "sessions", worktree_root=tmp_path / "lanes")
    return dataclasses.replace(identity, opened_at="2026-09-18T00:00:00Z") if opened else identity


def test_open_stamps_a_beat_the_sweeper_can_read(tmp_path: Path):
    identity = _identity(tmp_path)
    beat = session.stamp_for(identity, tmp_path, pid=4242, at=1000.0)
    on_disk = heartbeat.read(identity.session_id, tmp_path)
    assert on_disk is not None and on_disk == beat
    assert on_disk.issue == 917 and on_disk.branch == "issue-917" and on_disk.pid == 4242
    assert on_disk.worktree == str(identity.worktree)
    assert [s.session_id for s in heartbeat.list_sessions(tmp_path)] == [identity.session_id]


def test_close_clears_the_beat(tmp_path: Path):
    identity = _identity(tmp_path)
    session.stamp_for(identity, tmp_path, pid=4242)
    assert session.clear_for(identity, tmp_path) is True
    assert heartbeat.read(identity.session_id, tmp_path) is None
    assert session.clear_for(identity, tmp_path) is False


def test_a_fresh_beat_is_not_gone(tmp_path: Path):
    identity = _identity(tmp_path)
    session.stamp_for(identity, tmp_path, pid=4242, at=1000.0)
    assert session.session_gone(identity, tmp_path, at=1000.0 + 60, alive=False) == []


def test_a_stale_beat_behind_a_live_process_is_not_gone(tmp_path: Path):
    """A runtime that does not refresh its beat is the sweeper's `suspect`, not a
    gone session — refusing it would red every live lane after one TTL."""
    identity = _identity(tmp_path)
    session.stamp_for(identity, tmp_path, pid=4242, at=1000.0)
    assert session.session_gone(identity, tmp_path, at=1000.0 + 16 * 60, alive=True) == []


def test_a_stale_beat_and_a_dead_process_is_gone_by_name(tmp_path: Path):
    identity = _identity(tmp_path)
    session.stamp_for(identity, tmp_path, pid=4242, at=1000.0)
    found = session.session_gone(identity, tmp_path, ttl_minutes=15, at=1000.0 + 16 * 60, alive=False)
    assert [v.code for v in found] == ["lane-session-gone"]
    assert "close --lane" in found[0].detail


def test_a_session_minted_lane_with_no_beat_at_all_is_gone(tmp_path: Path):
    identity = _identity(tmp_path)
    found = session.session_gone(identity, tmp_path, alive=False)
    assert [v.code for v in found] == ["lane-session-gone"]
    assert "no beat exists" in found[0].detail


def test_a_legacy_record_owes_no_beat(tmp_path: Path):
    """Vacuity in the safe direction: 33 records predate the plane (measured
    2026-09-18); they are the sweep's `orphan-issue-lane`, not this rule's red."""
    identity = _identity(tmp_path, opened=False)
    assert identity.opened_at == ""
    assert session.session_gone(identity, tmp_path, alive=False) == []


def test_opened_at_round_trips_and_a_legacy_record_still_reads(tmp_path: Path):
    identity = _identity(tmp_path)
    path = write_record(identity, tmp_path)
    assert json.loads(path.read_text())["opened_at"] == "2026-09-18T00:00:00Z"
    assert read_record(identity.session_id, tmp_path) == identity
    legacy = json.loads(path.read_text())
    del legacy["opened_at"]
    path.write_text(json.dumps(legacy))
    restored = read_record(identity.session_id, tmp_path)
    assert restored is not None and restored.opened_at == ""
    assert "opened_at" not in restored.to_json()
