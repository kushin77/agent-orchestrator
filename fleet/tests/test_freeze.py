"""fleet/freeze.py — the D7 cutover drain/freeze/thaw gate (issue #715).

These tests never touch the live ``.fleet/``: every test monkeypatches
``freeze.FLEET_DIR``/``freeze.FLAG``/``freeze.PARITY_EVIDENCE`` onto
``tmp_path`` explicitly (the shared autouse isolation fixture in
``conftest.py`` does not cover this module, since no other lane declared it),
and ``subprocess.run`` calls into ``fleet/cron.py`` are stubbed so no test
touches a real crontab.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import freeze


@pytest.fixture(autouse=True)
def isolate_freeze(tmp_path, monkeypatch):
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    monkeypatch.setattr(freeze, "FLEET_DIR", fleet_dir)
    monkeypatch.setattr(freeze, "FLAG", fleet_dir / "freeze.flag")
    monkeypatch.setattr(freeze, "PARITY_EVIDENCE", fleet_dir / "parity.json")
    return fleet_dir


def _write_heartbeat(fleet_dir, rung: str, pid: int) -> None:
    (fleet_dir / f"{rung}.heartbeat.json").write_text(
        json.dumps({"pid": pid}), encoding="utf-8"
    )


def _write_parity(fleet_dir, *, one_writer=True, diffs=None) -> None:
    freeze.PARITY_EVIDENCE.write_text(
        json.dumps({"ticks": 4, "one_writer": one_writer, "diffs": diffs if diffs is not None else []}),
        encoding="utf-8",
    )


# --- live_rungs / is_drained --------------------------------------------------


def test_no_heartbeats_means_drained(isolate_freeze):
    assert freeze.live_rungs() == {}
    assert freeze.is_drained() is True


def test_live_pid_is_reported_and_blocks_drain(isolate_freeze):
    """A real, currently-running pid (our own) is reported as live."""
    import os

    _write_heartbeat(isolate_freeze, "brain", os.getpid())
    live = freeze.live_rungs()
    assert live == {"brain": os.getpid()}
    assert freeze.is_drained() is False


def test_dead_pid_is_not_reported_as_live(isolate_freeze):
    """A pid number that cannot exist (negative) is dead; drain is not blocked."""
    _write_heartbeat(isolate_freeze, "sister", -12345)
    assert freeze.live_rungs() == {}
    assert freeze.is_drained() is True


def test_unknown_rung_is_never_reported(isolate_freeze):
    """Only fleet/control.py's LIVE_RUNGS are considered — no stray heartbeat file counts."""
    import os

    _write_heartbeat(isolate_freeze, "not-a-rung", os.getpid())
    assert freeze.live_rungs() == {}


# --- parity evidence -----------------------------------------------------------


def test_parity_absent_is_not_green(isolate_freeze):
    assert freeze.parity_evidence_present() is False


def test_parity_green_matches_issue_714_shape(isolate_freeze):
    _write_parity(isolate_freeze, one_writer=True, diffs=[])
    assert freeze.parity_evidence_present() is True


def test_parity_with_diffs_is_not_green(isolate_freeze):
    _write_parity(isolate_freeze, one_writer=True, diffs=["watchdog-state-mismatch"])
    assert freeze.parity_evidence_present() is False


def test_parity_without_one_writer_is_not_green(isolate_freeze):
    _write_parity(isolate_freeze, one_writer=False, diffs=[])
    assert freeze.parity_evidence_present() is False


def test_parity_malformed_json_is_not_green(isolate_freeze):
    freeze.PARITY_EVIDENCE.write_text("{not json", encoding="utf-8")
    assert freeze.parity_evidence_present() is False


# --- refuse_if_frozen ----------------------------------------------------------


def test_refuse_if_frozen_is_none_when_flag_absent(isolate_freeze):
    assert freeze.refuse_if_frozen("watchdog") is None


def test_refuse_if_frozen_names_the_rung_and_the_flag(isolate_freeze):
    freeze.FLAG.write_text("{}", encoding="utf-8")
    refusal = freeze.refuse_if_frozen("watchdog")
    assert refusal is not None
    assert "watchdog" in refusal
    assert str(freeze.FLAG) in refusal
    assert "#715" in refusal


# --- cmd_drain -------------------------------------------------------------


def test_drain_sets_the_flag(isolate_freeze):
    args = SimpleNamespace()
    rc = freeze.cmd_drain(args)
    assert rc == 0
    assert freeze.FLAG.exists()
    payload = json.loads(freeze.FLAG.read_text(encoding="utf-8"))
    assert "drained_at" in payload


def test_drain_reports_live_rungs_without_erroring(isolate_freeze, capsys):
    import os

    _write_heartbeat(isolate_freeze, "monitor", os.getpid())
    rc = freeze.cmd_drain(SimpleNamespace())
    assert rc == 0
    assert freeze.FLAG.exists()
    out = capsys.readouterr().out
    assert "monitor" in out


# --- cmd_freeze --------------------------------------------------------------


def test_freeze_refuses_when_not_drained(isolate_freeze, capsys):
    rc = freeze.cmd_freeze(SimpleNamespace())
    assert rc == 1
    assert not freeze.FLAG.exists()


def test_freeze_refuses_when_live_rungs_remain_even_if_drained(isolate_freeze, monkeypatch):
    """The negative control: draining does not itself stop anything — freeze
    must independently verify no live rung survived before it will disable cron.
    """
    import os

    freeze.cmd_drain(SimpleNamespace())
    _write_heartbeat(isolate_freeze, "brain", os.getpid())
    calls = []
    monkeypatch.setattr(
        freeze.subprocess,
        "run",
        lambda *a, **k: calls.append((a, k)) or SimpleNamespace(returncode=0),
    )
    rc = freeze.cmd_freeze(SimpleNamespace())
    assert rc == 1
    assert calls == []  # cron.py disable was never invoked


def test_freeze_refuses_without_parity_evidence(isolate_freeze, monkeypatch):
    freeze.cmd_drain(SimpleNamespace())
    calls = []
    monkeypatch.setattr(
        freeze.subprocess,
        "run",
        lambda *a, **k: calls.append((a, k)) or SimpleNamespace(returncode=0),
    )
    rc = freeze.cmd_freeze(SimpleNamespace())
    assert rc == 1
    assert calls == []


def test_freeze_proceeds_when_drained_and_parity_green(isolate_freeze, monkeypatch):
    freeze.cmd_drain(SimpleNamespace())
    _write_parity(isolate_freeze, one_writer=True, diffs=[])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(freeze.subprocess, "run", fake_run)
    rc = freeze.cmd_freeze(SimpleNamespace())
    assert rc == 0
    assert calls, "expected `fleet/cron.py disable` to be shelled out to"
    assert calls[0][-1] == "disable"


def test_freeze_propagates_cron_disable_failure(isolate_freeze, monkeypatch):
    freeze.cmd_drain(SimpleNamespace())
    _write_parity(isolate_freeze, one_writer=True, diffs=[])
    monkeypatch.setattr(freeze.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=7))
    rc = freeze.cmd_freeze(SimpleNamespace())
    assert rc == 7


# --- cmd_thaw (the rollback) ------------------------------------------------


def test_thaw_removes_flag_and_calls_cron_enable(isolate_freeze, monkeypatch):
    freeze.FLAG.write_text("{}", encoding="utf-8")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(freeze.subprocess, "run", fake_run)
    rc = freeze.cmd_thaw(SimpleNamespace())
    assert rc == 0
    assert not freeze.FLAG.exists()
    assert calls[0][-1] == "enable"


def test_thaw_leaves_flag_when_cron_enable_fails(isolate_freeze, monkeypatch):
    freeze.FLAG.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(freeze.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=3))
    rc = freeze.cmd_thaw(SimpleNamespace())
    assert rc == 3
    assert freeze.FLAG.exists()


def test_thaw_is_safe_when_flag_already_absent(isolate_freeze, monkeypatch):
    monkeypatch.setattr(freeze.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    rc = freeze.cmd_thaw(SimpleNamespace())
    assert rc == 0


# --- cmd_status (smoke) -------------------------------------------------------


def test_status_runs_without_error(isolate_freeze, monkeypatch, capsys):
    monkeypatch.setattr(
        freeze.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="cron: NOT installed", stderr="", returncode=0),
    )
    rc = freeze.cmd_status(SimpleNamespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "freeze:" in out


# --- CLI wiring ----------------------------------------------------------------


def test_main_dispatches_status(monkeypatch, isolate_freeze):
    monkeypatch.setattr(freeze, "cmd_status", lambda args: 0)
    assert freeze.main(["status"]) == 0


def test_main_requires_a_command():
    with pytest.raises(SystemExit):
        freeze.main([])
