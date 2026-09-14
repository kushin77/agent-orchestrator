"""Tests for the unified fleet-state projection (issue #323).

Two properties carry the design and are asserted here by effect rather than by
reading the source:

* the projection **derives** its answer from the stores, so changing a store
  changes the projection with no other edit (a cached copy would not);
* a **disagreement between two stores is surfaced**, never silently resolved by
  picking one of them.

The four session states are produced from the real heartbeat rule
(``governance.reconcile.heartbeat.judge``), not a stand-in, so a change to the
TTL/pid semantics shows up here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import state


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dead_pid() -> int:
    """A pid that is definitely gone: spawn a process and reap it."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _write_fleet(root: Path) -> None:
    for sub in ("lanes", "sessions", "lifecycle", "sent", "done"):
        (root / ".fleet" / sub).mkdir(parents=True, exist_ok=True)
    (root / ".board" / "claims").mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _lane(root: Path, issue: int, *, session_id: str, agent: str, lane: str,
          branch: str, worktree: str) -> None:
    _write_json(
        root / ".fleet" / "lanes" / f"{session_id}.json",
        {
            "session_id": session_id,
            "issue": issue,
            "agent_id": agent,
            "lane": lane,
            "branch": branch,
            "worktree": worktree,
        },
    )


def _session(root: Path, issue: int, *, session_id: str, agent: str, lane: str,
             branch: str, worktree: str, pid: int | None, age_seconds: float = 0.0,
             state_name: str = "running") -> None:
    _write_json(
        root / ".fleet" / "sessions" / f"{session_id}.json",
        {
            "session_id": session_id,
            "issue": issue,
            "agent": agent,
            "lane": lane,
            "worktree": worktree,
            "branch": branch,
            "pid": pid,
            "at": state.time.time() - age_seconds,
            "state": state_name,
        },
    )


def _claim(root: Path, issue: int, *, agent: str, lane: str = "", directive_id: str = "") -> None:
    payload = {
        "event": "claim",
        "issue": issue,
        "agent": agent,
        "at": _iso_now(),
        "lane": lane,
        "ttl_hours": 24,
        "directive_id": directive_id,
    }
    path = root / ".board" / "claims" / f"{issue:020d}-{issue:05d}-{agent}-claim.json"
    _write_json(path, payload)


def _directive(root: Path, ident: str, *, issue: int = 0, consumed: bool = False) -> None:
    payload: dict = {"from": "brain", "to": "sister", "type": "directive", "id": ident, "ts": _iso_now()}
    if issue:
        payload["task"] = {"kind": "work", "issue": issue, "lane": "fleet"}
    _write_json(root / ".fleet" / "sent" / f"{ident}.json", payload)
    if consumed:
        _write_json(root / ".fleet" / "done" / f"{ident}.json", payload)


def _journal(root: Path, issue: int, *, closing_evidence: bool = True) -> None:
    _write_json(
        root / ".fleet" / "lifecycle" / f"{issue}.json",
        {"closing_evidence": closing_evidence, "evidence": "merged", "verify": {"commit": "0" * 40, "ok": closing_evidence}},
    )


def _project(root: Path, **kwargs):
    return state.project(
        root=root,
        fleet_dir=root / ".fleet",
        board_dir=root / ".board",
        **kwargs,
    )


def _item(projection, issue: int):
    return next(item for item in projection.items if item.issue == issue)


# --------------------------------------------------------------------------
# the four session states, derived from the real heartbeat rule
# --------------------------------------------------------------------------


def test_each_session_state_is_derived(tmp_path):
    _write_fleet(tmp_path)
    # live: fresh beat, process alive (the pytest process itself)
    _lane(tmp_path, 1, session_id="s1", agent="a", lane="l", branch="issue-1", worktree="/w1")
    _session(tmp_path, 1, session_id="s1", agent="a", lane="l", branch="issue-1", worktree="/w1", pid=os.getpid())
    # suspect: fresh beat, process gone
    _lane(tmp_path, 2, session_id="s2", agent="a", lane="l", branch="issue-2", worktree="/w2")
    _session(tmp_path, 2, session_id="s2", agent="a", lane="l", branch="issue-2", worktree="/w2", pid=_dead_pid())
    # orphan: beat past the TTL
    _lane(tmp_path, 3, session_id="s3", agent="a", lane="l", branch="issue-3", worktree="/w3")
    _session(tmp_path, 3, session_id="s3", agent="a", lane="l", branch="issue-3", worktree="/w3",
             pid=os.getpid(), age_seconds=60 * 60)
    # shelved: declared by the reconciliation worker
    _lane(tmp_path, 4, session_id="s4", agent="a", lane="l", branch="issue-4", worktree="/w4")
    _session(tmp_path, 4, session_id="s4", agent="a", lane="l", branch="issue-4", worktree="/w4",
             pid=os.getpid(), state_name="shelved")
    _claim(tmp_path, 4, agent="a")

    projection = _project(tmp_path)
    assert _item(projection, 1).status == state.LIVE
    assert _item(projection, 2).status == state.SUSPECT
    assert _item(projection, 3).status == state.ORPHAN
    assert _item(projection, 4).status == state.SHELVED


def test_only_orphan_shelved_and_wedged_block(tmp_path):
    _write_fleet(tmp_path)
    _lane(tmp_path, 1, session_id="s1", agent="a", lane="l", branch="issue-1", worktree="/w1")
    _session(tmp_path, 1, session_id="s1", agent="a", lane="l", branch="issue-1", worktree="/w1", pid=os.getpid())
    _lane(tmp_path, 2, session_id="s2", agent="a", lane="l", branch="issue-2", worktree="/w2")
    _session(tmp_path, 2, session_id="s2", agent="a", lane="l", branch="issue-2", worktree="/w2", pid=_dead_pid())

    projection = _project(tmp_path)
    assert _item(projection, 1).blocking is False
    assert _item(projection, 2).blocking is False  # suspect is reported, not blocking
    assert projection.clean is True
    assert [i.issue for i in projection.blocking] == []


# --------------------------------------------------------------------------
# exit codes: the command is a cron/health input, not only a human view
# --------------------------------------------------------------------------


def test_clean_projection_exits_zero(tmp_path, capsys):
    _write_fleet(tmp_path)
    _lane(tmp_path, 7, session_id="s7", agent="a", lane="l", branch="issue-7", worktree="/w7")
    _session(tmp_path, 7, session_id="s7", agent="a", lane="l", branch="issue-7", worktree="/w7", pid=os.getpid())
    rc = state.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == state.EXIT_OK
    assert "fleet-state: OK" in out


def test_orphan_exits_non_zero_and_names_the_item(tmp_path, capsys):
    _write_fleet(tmp_path)
    _lane(tmp_path, 9, session_id="s9", agent="a", lane="l", branch="issue-9", worktree="/w9")
    _session(tmp_path, 9, session_id="s9", agent="a", lane="l", branch="issue-9", worktree="/w9",
             pid=os.getpid(), age_seconds=60 * 60)
    rc = state.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == state.EXIT_NOT_OK
    assert "#9" in out
    assert "orphan" in out
    assert "NOT-OK" in out


def test_shelved_exits_non_zero_and_names_the_item(tmp_path, capsys):
    _write_fleet(tmp_path)
    _lane(tmp_path, 11, session_id="s11", agent="a", lane="l", branch="issue-11", worktree="/w11")
    _session(tmp_path, 11, session_id="s11", agent="a", lane="l", branch="issue-11", worktree="/w11",
             pid=os.getpid(), state_name="shelved")
    _claim(tmp_path, 11, agent="a")
    rc = state.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == state.EXIT_NOT_OK
    assert "#11" in out and "shelved" in out


def test_cannot_assess_when_no_store_exists(tmp_path, capsys):
    rc = state.main(["--root", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == state.EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in err


# --------------------------------------------------------------------------
# derived, never a copy
# --------------------------------------------------------------------------


def test_changing_a_store_changes_the_projection(tmp_path):
    """A cached projection would not move when a store moves; this one does."""
    _write_fleet(tmp_path)
    _lane(tmp_path, 5, session_id="s5", agent="a", lane="l", branch="issue-5", worktree="/w5")
    _session(tmp_path, 5, session_id="s5", agent="a", lane="l", branch="issue-5", worktree="/w5", pid=os.getpid())

    before = _project(tmp_path)
    assert _item(before, 5).status == state.LIVE

    # The lane retreats past the TTL; no other file is touched.
    _session(tmp_path, 5, session_id="s5", agent="a", lane="l", branch="issue-5", worktree="/w5",
             pid=os.getpid(), age_seconds=60 * 60)
    after = _project(tmp_path)
    assert _item(after, 5).status == state.ORPHAN
    assert after.clean is False


def test_a_disagreeing_store_is_shown_not_resolved(tmp_path):
    """The load-bearing property: two stores differing is reported, not hidden."""
    _write_fleet(tmp_path)
    _lane(tmp_path, 21, session_id="s21", agent="a", lane="l", branch="issue-21-wrong", worktree="/w21")
    _session(tmp_path, 21, session_id="s21", agent="a", lane="l", branch="issue-21", worktree="/w21", pid=os.getpid())

    item = _item(_project(tmp_path), 21)
    codes = {finding.code for finding in item.findings}
    assert state.F_LANE_SESSION_MISMATCH in codes
    detail = next(f.detail for f in item.findings if f.code == state.F_LANE_SESSION_MISMATCH)
    # Both values are named, so the operator sees WHICH stores disagree.
    assert "issue-21-wrong" in detail and "issue-21" in detail
    assert item.blocking is True


def test_agreeing_stores_raise_no_mismatch(tmp_path):
    """Negative control for the mismatch check: agreement must be silent."""
    _write_fleet(tmp_path)
    _lane(tmp_path, 22, session_id="s22", agent="a", lane="l", branch="issue-22", worktree="/w22")
    _session(tmp_path, 22, session_id="s22", agent="a", lane="l", branch="issue-22", worktree="/w22", pid=os.getpid())
    item = _item(_project(tmp_path), 22)
    assert state.F_LANE_SESSION_MISMATCH not in {f.code for f in item.findings}
    assert item.findings == []
    assert item.blocking is False


# --------------------------------------------------------------------------
# wedged artifacts
# --------------------------------------------------------------------------


def test_claim_without_lane_is_wedged(tmp_path):
    _write_fleet(tmp_path)
    _claim(tmp_path, 31, agent="ghost")
    item = _item(_project(tmp_path), 31)
    assert state.F_CLAIM_WITHOUT_LANE in {f.code for f in item.findings}
    assert item.blocking is True


def test_lane_without_heartbeat_is_wedged(tmp_path):
    _write_fleet(tmp_path)
    _lane(tmp_path, 32, session_id="s32", agent="a", lane="l", branch="issue-32", worktree="/w32")
    item = _item(_project(tmp_path), 32)
    assert state.F_LANE_WITHOUT_HEARTBEAT in {f.code for f in item.findings}


def test_consumed_directive_with_claim_held_is_wedged(tmp_path):
    _write_fleet(tmp_path)
    _lane(tmp_path, 33, session_id="s33", agent="a", lane="l", branch="issue-33", worktree="/w33")
    _session(tmp_path, 33, session_id="s33", agent="a", lane="l", branch="issue-33", worktree="/w33", pid=os.getpid())
    _claim(tmp_path, 33, agent="a", lane="l", directive_id="brain-directive-abc")
    _directive(tmp_path, "brain-directive-abc", issue=33, consumed=True)

    item = _item(_project(tmp_path), 33)
    assert state.F_DIRECTIVE_CONSUMED_CLAIM_HELD in {f.code for f in item.findings}
    assert item.directive is not None and item.directive.state == "done"


def test_claim_citing_a_directive_no_mailbox_holds_is_wedged(tmp_path):
    _write_fleet(tmp_path)
    _claim(tmp_path, 34, agent="a", directive_id="brain-directive-gone")
    item = _item(_project(tmp_path), 34)
    assert state.F_DIRECTIVE_MISSING in {f.code for f in item.findings}


def test_closing_journal_with_claim_held_is_wedged(tmp_path):
    _write_fleet(tmp_path)
    _claim(tmp_path, 35, agent="a")
    _journal(tmp_path, 35, closing_evidence=True)
    item = _item(_project(tmp_path), 35)
    assert state.F_JOURNAL_CLOSED_CLAIM_HELD in {f.code for f in item.findings}


def test_shelved_without_a_claim_is_wedged(tmp_path):
    _write_fleet(tmp_path)
    _session(tmp_path, 36, session_id="s36", agent="a", lane="l", branch="issue-36", worktree="/w36",
             pid=os.getpid(), state_name="shelved")
    item = _item(_project(tmp_path), 36)
    assert state.F_SHELVED_WITHOUT_CLAIM in {f.code for f in item.findings}


# --------------------------------------------------------------------------
# reporting surface
# --------------------------------------------------------------------------


def test_json_projection_carries_every_named_field(tmp_path):
    _write_fleet(tmp_path)
    _lane(tmp_path, 41, session_id="s41", agent="a", lane="lane-41", branch="issue-41", worktree="/w41")
    _session(tmp_path, 41, session_id="s41", agent="a", lane="lane-41", branch="issue-41", worktree="/w41", pid=os.getpid())
    _claim(tmp_path, 41, agent="a", lane="lane-41", directive_id="d41")
    _directive(tmp_path, "d41", issue=41)
    _journal(tmp_path, 41, closing_evidence=False)

    payload = _project(tmp_path).to_json()
    item = next(entry for entry in payload["items"] if entry["issue"] == 41)
    assert item["session_status"] == state.LIVE
    assert item["claim"]["agent"] == "a"
    assert item["directive"]["state"] == "sent"
    assert item["journal"]["closing_evidence"] is False
    assert item["findings"] == []
    assert payload["blocking"] == []
    assert payload["counts"]["live"] >= 1


def test_unreadable_record_is_reported_and_blocks(tmp_path, capsys):
    _write_fleet(tmp_path)
    (tmp_path / ".fleet" / "lanes" / "broken.json").write_text("{not json", encoding="utf-8")
    _lane(tmp_path, 51, session_id="s51", agent="a", lane="l", branch="issue-51", worktree="/w51")
    _session(tmp_path, 51, session_id="s51", agent="a", lane="l", branch="issue-51", worktree="/w51", pid=os.getpid())

    projection = _project(tmp_path)
    assert _item(projection, 51).status == state.LIVE
    assert any("broken.json" in path for path in projection.unreadable)
    # Not fatal — the projection still answers — but it cannot claim an all-clear.
    assert projection.clean is False

    rc = state.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == state.EXIT_NOT_OK
    assert "unreadable" in out


def test_finding_vocabulary_is_closed():
    with pytest.raises(ValueError):
        state.Finding("invented-code", "detail")
