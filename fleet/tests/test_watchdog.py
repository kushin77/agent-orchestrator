"""The fleet watchdog and its crontab manager — the cron-owned keeper."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cron  # noqa: E402
import watchdog  # noqa: E402


def _beat(commit="abc1234", age=10):
    return {"pid": 111, "state": "idle", "commit": commit, "ts": "2026-09-13T00:00:00Z"}


# --- the decision -------------------------------------------------------------


def test_decide_classifies_all_four_states(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat: 10)
    assert watchdog.decide(None, None, "head") == ("missing", "no loop process")
    assert watchdog.decide(111, None, "head") == ("stale", "no heartbeat from a live loop")
    assert watchdog.decide(111, _beat(commit="old0000"), "head1111") == ("drifted", "running old0000, HEAD head1111")
    assert watchdog.decide(111, _beat(commit="head1111"), "head1111") == ("healthy", "")


def test_decide_flags_a_stale_beat(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat: 999)
    state, _reason = watchdog.decide(111, _beat(), "head")
    assert state == "stale"


def test_a_missing_loop_is_respawned(monkeypatch):
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: None)
    calls = []
    monkeypatch.setattr(watchdog, "respawn", lambda pattern, script: calls.append(script) or True)
    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head")
    assert "missing" in line and calls == ["fleet/terminal.sh"]


def test_a_drifted_sister_with_a_run_in_flight_is_left_alone(monkeypatch):
    """The watchdog's one rule: never restart a run just to update code."""
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="old0000"))
    monkeypatch.setattr(watchdog, "run_in_flight", lambda: True)
    monkeypatch.setattr(watchdog, "respawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not respawn")))
    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head1111")
    assert "left alone" in line


def test_a_drifted_idle_sister_is_respawned(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(commit="old0000"))
    monkeypatch.setattr(watchdog, "run_in_flight", lambda: False)
    calls = []
    monkeypatch.setattr(watchdog, "respawn", lambda pattern, script: calls.append(script) or True)
    watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head1111")
    assert calls == ["fleet/terminal.sh"]


# --- the crontab manager ------------------------------------------------------


def test_the_cron_line_is_identifiable_and_self_contained():
    ln = cron.line(2)
    assert ln.endswith("# ao-fleet-watchdog")
    assert "fleet/watchdog.py run" in ln
    assert "*/2 * * * *" in ln


def test_install_replaces_an_existing_line_and_keeps_others(monkeypatch):
    state = {"written": None}
    monkeypatch.setattr(cron, "read_crontab", lambda: [
        "0 2 * * * other-job # other",
        "*/5 * * * * old fleet line # ao-fleet-watchdog",
    ])
    monkeypatch.setattr(cron, "write_crontab", lambda lines: state.__setitem__("written", lines))
    assert cron.cmd_install(type("Args", (), {"interval": 2})()) == 0
    written = state["written"]
    assert len(written) == 2
    assert written[0] == "0 2 * * * other-job # other"
    assert written[1].startswith("*/2 * * * *") and written[1].endswith("# ao-fleet-watchdog")


def test_uninstall_removes_only_the_fleet_line(monkeypatch):
    state = {"written": None}
    monkeypatch.setattr(cron, "read_crontab", lambda: [
        "0 2 * * * other-job # other",
        "*/2 * * * * fleet # ao-fleet-watchdog",
    ])
    monkeypatch.setattr(cron, "write_crontab", lambda lines: state.__setitem__("written", lines))
    assert cron.cmd_uninstall(type("Args", (), {})()) == 0
    assert state["written"] == ["0 2 * * * other-job # other"]


def test_disable_comments_out_and_enable_restores(monkeypatch):
    state = {"lines": ["*/2 * * * * fleet # ao-fleet-watchdog"]}
    monkeypatch.setattr(cron, "read_crontab", lambda: state["lines"])
    monkeypatch.setattr(cron, "write_crontab", lambda lines: state.__setitem__("lines", lines))
    cron.cmd_disable(type("Args", (), {})())
    assert state["lines"][0].lstrip().startswith("#")
    cron.cmd_enable(type("Args", (), {})())
    assert not state["lines"][0].lstrip().startswith("#")
    assert "# ao-fleet-watchdog" in state["lines"][0]


def test_status_reports_installed(monkeypatch, capsys):
    monkeypatch.setattr(cron, "read_crontab", lambda: ["*/2 * * * * fleet # ao-fleet-watchdog"])
    monkeypatch.setattr(cron, "LOG", Path("/nonexistent-watchdog-log"))
    assert cron.cmd_status(type("Args", (), {})()) == 0
    assert "installed" in capsys.readouterr().out
