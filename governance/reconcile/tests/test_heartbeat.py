"""Heartbeats: they advance while a session runs, and they say what died.

The two behaviours that matter operationally are here: a running session's beat
**advances** (otherwise a live lane goes stale and gets reclaimed under its own
agent), and a session whose process is gone is **flagged** — the moment the sweep
has to notice.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from governance.reconcile.heartbeat import (
    LIVE,
    ORPHAN,
    SHELVED,
    SUSPECT,
    Beater,
    Session,
    clear,
    judge,
    list_sessions,
    path_for,
    pid_alive,
    read,
    stamp,
)

from conftest import SESSION, dead_pid, make_session  # noqa: E402  (suite-local helper)


def test_a_stamp_writes_a_readable_heartbeat(root: Path):
    stamp(SESSION, issue=304, agent="copilot-brain", root=root, worktree="/lanes/x", branch="issue-304")
    session = read(SESSION, root)
    assert session is not None
    assert session.session_id == SESSION
    assert session.issue == 304
    assert session.branch == "issue-304"
    assert session.pid  # defaults to the writing process


def test_the_heartbeat_carries_both_a_human_and_a_machine_timestamp(root: Path):
    stamp(SESSION, issue=304, agent="a", root=root)
    payload = json.loads(path_for(SESSION, root).read_text(encoding="utf-8"))
    assert payload["at"] > 0
    assert payload["at_iso"].endswith("Z")


def test_a_second_stamp_advances_the_timestamp(root: Path):
    """Requirement 1's verification: the agent keeps its timestamp file current."""
    first = stamp(SESSION, issue=304, agent="a", root=root)
    time.sleep(0.01)
    second = stamp(SESSION, issue=304, agent="a", root=root)
    assert second.at > first.at
    assert read(SESSION, root).at == second.at


def test_stamping_leaves_no_partial_file(root: Path):
    """A sweep must never read a half-written record and guess about it."""
    stamp(SESSION, issue=304, agent="a", root=root)
    strays = [p.name for p in (root / ".fleet" / "sessions").iterdir() if p.suffix == ".tmp"]
    assert strays == []


def test_clear_removes_the_heartbeat(root: Path):
    stamp(SESSION, issue=304, agent="a", root=root)
    assert clear(SESSION, root) is True
    assert read(SESSION, root) is None
    assert clear(SESSION, root) is False


def test_an_unsafe_session_id_is_refused(root: Path):
    for bad in ("../escape", "a/b", ".hidden", ""):
        with pytest.raises(ValueError):
            stamp(bad, issue=1, agent="a", root=root)


def test_list_sessions_returns_every_beat(root: Path):
    stamp("s-one", issue=1, agent="a", root=root)
    stamp("s-two", issue=2, agent="b", root=root)
    assert {session.session_id for session in list_sessions(root)} == {"s-one", "s-two"}


def test_a_live_session_is_not_a_candidate():
    verdict = judge(make_session(at=1_000_000.0), 15, at=1_000_010.0, alive=True)
    assert verdict.status == LIVE
    assert verdict.reclaimable is False


def test_a_stale_beat_is_an_orphan_even_while_the_process_lives():
    """Catches a session that is alive but wedged — a pid check cannot see this."""
    verdict = judge(make_session(at=1_000_000.0), 15, at=1_000_000.0 + 16 * 60, alive=True)
    assert verdict.status == ORPHAN
    assert verdict.reclaimable is True
    assert "past the 15m TTL" in verdict.reason


def test_a_fresh_beat_behind_a_dead_process_is_suspect_not_reclaimable():
    """Absence alone is weak evidence: handovers and re-execs look exactly like this."""
    verdict = judge(make_session(at=1_000_000.0), 15, at=1_000_010.0, alive=False)
    assert verdict.status == SUSPECT
    assert verdict.reclaimable is False
    assert "not reclaimed" in verdict.reason


def test_a_stale_beat_behind_a_dead_process_is_an_orphan():
    verdict = judge(make_session(at=1_000_000.0), 15, at=1_000_000.0 + 20 * 60, alive=False)
    assert verdict.status == ORPHAN


def test_an_undated_record_fails_towards_the_audit():
    """at=0 must read as maximally stale, never as "just started"."""
    undated = Session.from_json({"session_id": "s", "issue": 1, "agent": "a"})
    assert undated.at == 0.0
    assert judge(undated, 15, at=1_000_000.0, alive=True).status == ORPHAN


def test_pid_liveness_is_real():
    assert pid_alive(__import__("os").getpid()) is True
    assert pid_alive(dead_pid()) is False
    assert pid_alive(None) is False


def test_the_beater_keeps_the_beat_alive_and_clears_it_on_stop(root: Path):
    """Requirement 1 end to end: beats advance while running, gone when stopped."""
    beat = Beater(SESSION, 304, "a", root, worktree="/lanes/x", branch="issue-304", interval=0.05).start()
    first = read(SESSION, root)
    assert first is not None
    time.sleep(0.2)
    later = read(SESSION, root)
    assert later is not None and later.at > first.at
    beat.stop()
    assert read(SESSION, root) is None


def test_a_shelved_session_keeps_its_record(root: Path):
    stamp(SESSION, issue=304, agent="a", root=root, state=SHELVED, note="unmerged")
    session = read(SESSION, root)
    assert session.state == SHELVED
    assert session.note == "unmerged"
