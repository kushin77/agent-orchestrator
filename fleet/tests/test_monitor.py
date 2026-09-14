"""The monitor's liveness beat: JSON, in the brain/sister shape (issue #330).

The monitor used to write `.fleet/open-eye.heartbeat` as a plain `alive pid=N`
line, which `fleet/console.py` read as JSON — so the dashboard printed
`monitor no-heartbeat` while the monitor was alive. These tests pin the fix:
the monitor publishes a JSON beat the console can read, and the mutation
(plain-text instead of JSON) reproduces the original `no-heartbeat` symptom.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import console  # noqa: E402
import monitor  # noqa: E402


def test_monitor_writes_a_json_beat_in_the_brain_sister_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "HEARTBEAT", tmp_path / "monitor.heartbeat.json")
    monitor.write_heartbeat(started_at="2026-09-13T21:00:00Z", commit="8d9c219")
    beat = json.loads(monitor.HEARTBEAT.read_text(encoding="utf-8"))
    assert set(beat) == {"pid", "state", "started_at", "commit", "ts"}
    assert beat["state"] == "healthy"
    assert beat["commit"] == "8d9c219"
    assert beat["started_at"] == "2026-09-13T21:00:00Z"
    assert isinstance(beat["pid"], int)


def test_monitor_beat_is_rewritten_atomically_each_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "HEARTBEAT", tmp_path / "monitor.heartbeat.json")
    monitor.write_heartbeat(started_at="2026-09-13T21:00:00Z", commit="a")
    first = monitor.HEARTBEAT.read_text(encoding="utf-8")
    monitor.write_heartbeat(started_at="2026-09-13T21:00:00Z", commit="b")
    second = monitor.HEARTBEAT.read_text(encoding="utf-8")
    assert first != second
    assert json.loads(second)["commit"] == "b"
    # no .tmp leftover after the rename
    assert not monitor.HEARTBEAT.with_suffix(".tmp").exists()


def test_console_reads_the_monitor_beat_as_a_healthy_rung(tmp_path, monkeypatch):
    """The console's monitor row must show pid + beat age, not `no-heartbeat`."""
    monkeypatch.setattr(console, "loop_pid", lambda pattern: 4242)
    monkeypatch.setattr(console, "monitor_heartbeat", lambda: tmp_path / "monitor.heartbeat.json")
    beat = {"pid": 4242, "state": "healthy", "commit": "8d9c219", "ts": "2020-01-01T00:00:00Z"}
    (tmp_path / "monitor.heartbeat.json").write_text(json.dumps(beat), encoding="utf-8")
    info = console.rungs_snapshot()["monitor"]
    assert info["state"] == "healthy"
    assert info["pid"] == 4242
    assert info["beat_age"] is not None


def test_mutation_plain_text_beat_reproduces_no_heartbeat(tmp_path, monkeypatch):
    """The negative control: the OLD plain-text beat must read as no-heartbeat,
    not as healthy — that is the exact bug this PR removes."""
    monkeypatch.setattr(console, "loop_pid", lambda pattern: 4242)
    monkeypatch.setattr(console, "monitor_heartbeat", lambda: tmp_path / "monitor.heartbeat.json")
    (tmp_path / "monitor.heartbeat.json").write_text(
        "2026-09-13T21:49:10Z alive pid=4242\n", encoding="utf-8"
    )
    info = console.rungs_snapshot()["monitor"]
    assert info["state"] == "no-heartbeat"
    assert info["pid"] == 4242
