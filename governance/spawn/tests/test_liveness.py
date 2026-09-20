"""Is a run in flight? The marker's own evidence (issue #793, criterion 4).

The measured box state is the first test: a marker whose ``pid`` (the LOOP's) is
alive, whose ``child_pid`` is null, and whose beat is 5.6 hours old, must NOT
hold the drift lock — that state is what pinned a rung on pre-#723 code with no
attempt budget and regenerated the gate storms #724 was filed for.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from governance.spawn import liveness


def stamp(seconds_ago: float, *, anchor: datetime | None = None) -> str:
    """An ISO beat exactly the form `fleet/terminal.py::_now` writes.

    `anchor` pins the instant the beat is taken from (#1572). `strftime` truncates
    microseconds, so a beat built from the live clock renders the intended integer
    only while the reader happens to land inside the same wall-clock second — a
    sub-second window. A test that asserts a RENDERED integer must therefore pass the
    same anchor to the reader's `now=`, which removes the clock read entirely.
    """
    base = datetime.now(timezone.utc) if anchor is None else anchor
    return (base - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def marker(directory: Path, name: str, **fields) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def dead_pid() -> int:
    """A pid certainly not alive: a child this process has already reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_the_measured_box_state_is_not_a_run_in_flight(tmp_path: Path) -> None:
    """The loop's pid alive, no child, a beat nothing advances — four of these."""
    runs = tmp_path / "runs"
    marker(runs, "run-a", issue=234, pid=os.getpid(), child_pid=None, ts=stamp(16200))
    marker(runs, "run-b", issue=170, pid=os.getpid(), child_pid=None, ts=stamp(16200))

    held, note = liveness.runs_in_flight(runs)

    assert held is False
    assert "run-a" in note and "run-b" in note
    assert "crashed run" in note
    assert "child_pid None" in note


def test_a_live_child_is_a_run_in_flight_without_needing_a_clock(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    marker(runs, "live", pid=os.getpid(), child_pid=os.getpid(), ts=stamp(99999))

    held, note = liveness.runs_in_flight(runs)

    assert held is True
    assert f"live child pid {os.getpid()}" in note


def test_a_just_started_run_with_no_child_yet_is_in_flight(tmp_path: Path) -> None:
    """`mark_run` writes `child_pid: null` BEFORE the subagent exists."""
    runs = tmp_path / "runs"
    marker(runs, "starting", pid=os.getpid(), child_pid=None, ts=stamp(2))

    held, note = liveness.runs_in_flight(runs)

    assert held is True
    assert "fresh beat" in note


def test_a_dead_child_with_a_stale_beat_is_not_in_flight(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    marker(runs, "dead", pid=os.getpid(), child_pid=dead_pid(), ts=stamp(600))

    assert liveness.runs_in_flight(runs)[0] is False


def test_an_unreadable_beat_is_not_in_flight_and_names_the_marker(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    marker(runs, "torn", pid=os.getpid(), child_pid=None, ts="not-a-timestamp")

    held, note = liveness.runs_in_flight(runs)

    assert held is False
    assert "torn" in note and "unreadable" in note


def test_a_missing_beat_is_the_same_verdict_as_an_unreadable_one(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    marker(runs, "absent-stamp", pid=os.getpid(), child_pid=None)

    assert liveness.runs_in_flight(runs)[0] is False


def test_the_held_line_names_the_marker_that_decided_it(tmp_path: Path) -> None:
    """A leftover marker must never hold the lock invisibly."""
    runs = tmp_path / "runs"
    marker(runs, "crashed", pid=os.getpid(), child_pid=None, ts=stamp(900))
    marker(runs, "working", pid=os.getpid(), child_pid=os.getpid(), ts=stamp(5))

    held, note = liveness.runs_in_flight(runs)

    assert held is True
    assert note.startswith("held by working")
    assert "crashed" not in note


def test_no_markers_at_all_is_no_run_in_flight(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()

    held, note = liveness.runs_in_flight(runs)

    assert held is False
    assert note == "no run markers"


def test_a_marker_that_is_not_json_is_refused_rather_than_believed(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "torn.json").write_text("{not json", encoding="utf-8")

    held, note = liveness.runs_in_flight(runs)

    assert held is False
    assert "unreadable record" in note


def test_the_heartbeat_contradiction_is_reported_not_resolved(tmp_path: Path) -> None:
    """`state: idle` beside a live child is two artifacts disagreeing."""
    runs = tmp_path / "runs"
    marker(runs, "working", pid=os.getpid(), child_pid=os.getpid(), ts=stamp(1))

    report = liveness.contradiction({"pid": os.getpid(), "state": "idle", "runs": 0}, runs)

    assert report is not None
    assert "CONTRADICTION" in report
    assert "idle" in report
    assert "keeps the lock" in report


def test_a_heartbeat_that_agrees_is_not_a_contradiction(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    marker(runs, "working", pid=os.getpid(), child_pid=os.getpid(), ts=stamp(1))

    assert liveness.contradiction({"state": "working"}, runs) is None


def test_an_idle_heartbeat_with_no_run_in_flight_is_not_a_contradiction(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    marker(runs, "crashed", pid=os.getpid(), child_pid=None, ts=stamp(9999))

    assert liveness.contradiction({"state": "idle"}, runs) is None


def test_an_unreadable_heartbeat_beside_a_live_child_is_reported(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    marker(runs, "working", pid=os.getpid(), child_pid=os.getpid(), ts=stamp(1))

    report = liveness.contradiction(None, runs)

    assert report is not None and "unreadable" in report


def test_the_beat_window_is_configurable_and_a_bad_value_falls_back() -> None:
    assert liveness.stale_seconds({"AO_RUN_STALE_SECONDS": "5"}) == 5
    assert liveness.stale_seconds({"AO_RUN_STALE_SECONDS": "zero"}) == liveness.DEFAULT_STALE_SECONDS
    assert liveness.stale_seconds({"AO_RUN_STALE_SECONDS": "0"}) == liveness.DEFAULT_STALE_SECONDS


def test_a_non_pid_is_never_alive() -> None:
    for value in (None, "1234", 0, -1, True, 1.5):
        assert liveness.process_alive(value) is False


def test_the_verdict_line_carries_its_evidence(tmp_path: Path) -> None:
    # #1572: the anchor is PINNED and handed back through the reader's `now=` seam, so
    # the rendered integer is exact by construction. It used to be built from the live
    # clock, where `5000s old` held only inside the write's own wall-clock second.
    anchor = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    verdict = liveness.marker_verdict(
        "run-x",
        {"pid": os.getpid(), "child_pid": None, "ts": stamp(5000, anchor=anchor)},
        now=anchor.timestamp(),
        window=120,
    )

    assert verdict.in_flight is False
    assert "run-x" in verdict.line()
    assert "5000s old" in verdict.line()
