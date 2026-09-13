"""Per-run telemetry wired into fleet/terminal.py's run path (issue #233)."""

from __future__ import annotations

import json
import types

import terminal
import telemetry


def test_record_run_appends_a_valid_record(tmp_path, monkeypatch):
    log = tmp_path / "runs.jsonl"
    monkeypatch.setattr(telemetry, "RUNS_LOG", log)

    terminal.record_run("d-1", 233, "subagent-abc", "started", "2026-09-13T00:00:00Z")

    records = telemetry.read_records(log)
    assert len(records) == 1
    assert records[0]["run_id"] == "d-1"
    assert records[0]["issue"] == "233"
    assert records[0]["agent"] == "subagent-abc"
    assert records[0]["status"] == "started"
    assert records[0]["finished_at"] is None


def test_record_run_writes_done_with_started_and_finished(tmp_path, monkeypatch):
    log = tmp_path / "runs.jsonl"
    monkeypatch.setattr(telemetry, "RUNS_LOG", log)

    terminal.record_run(
        "d-2", 233, "subagent-abc", "done",
        "2026-09-13T00:00:00Z", "2026-09-13T00:05:00Z", "worktree ok",
    )

    records = telemetry.read_records(log)
    assert records[0]["status"] == "done"
    assert records[0]["started_at"] == "2026-09-13T00:00:00Z"
    assert records[0]["finished_at"] == "2026-09-13T00:05:00Z"
    assert records[0]["detail"] == "worktree ok"


def test_record_run_swallows_a_bad_status_instead_of_crashing_the_loop(tmp_path, monkeypatch, capsys):
    log = tmp_path / "runs.jsonl"
    monkeypatch.setattr(telemetry, "RUNS_LOG", log)

    terminal.record_run("d-3", 233, "subagent-abc", "bogus-status", "2026-09-13T00:00:00Z")

    assert telemetry.read_records(log) == []
    assert "rejected" in capsys.readouterr().err


class _NullBeater:
    """A beater stand-in: the loop only ever calls ``stop()`` on it."""

    def stop(self) -> None:
        return None


class _FakeSubprocess:
    """A subprocess stand-in: this test spawns no process at all."""

    def __init__(self, respond):
        self._respond = respond

    def run(self, argv, **kwargs):
        return self._respond(argv)


def _completed(rc: int, stdout: str = ""):
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr="")


def test_loop_records_started_and_a_terminal_record_for_the_run_path(monkeypatch):
    """Asserted by EFFECT — the records the loop writes, not its source text.

    The old test grepped ``inspect.getsource(terminal.loop)``, so commenting the
    emission out while keeping its text left it green (issue #286). This drives
    ``loop`` once against stubs and reads the telemetry back.
    """
    directive = {
        "id": "d286aaaa",
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "task": {"issue": 286, "lane": "harness"},
    }

    def respond(argv):
        if argv[:1] == ["git"]:
            return _completed(0, "deadbee\n")
        if "watch" in argv:
            return _completed(0, json.dumps(directive) + "\n")
        if "held" in argv:
            # rc != 0 means "not held" — the loop then executes the directive.
            return _completed(1, "")
        return _completed(0, "")

    monkeypatch.setattr(terminal, "subprocess", _FakeSubprocess(respond))
    monkeypatch.setattr(terminal.singleton, "guard", lambda *args, **kwargs: True)
    monkeypatch.setattr(terminal, "provision_worktree", lambda *args, **kwargs: None)
    monkeypatch.setattr(terminal, "run_once", lambda *args, **kwargs: (0, "the work landed"))
    monkeypatch.setattr(terminal, "closeout_issue", lambda issue: "OK")
    monkeypatch.setattr(terminal, "start_beating", lambda *args, **kwargs: _NullBeater())

    args = terminal.build_parser().parse_args(["run", "--once"])

    assert terminal.loop(args) == 0

    records = [r for r in telemetry.read_records(telemetry.RUNS_LOG) if r["run_id"] == directive["id"]]
    assert {record["status"] for record in records} == {"started", "done"}, (
        "the run path did not emit both a started and a terminal telemetry record"
    )
    started = next(record for record in records if record["status"] == "started")
    assert started["issue"] == "286"
    assert started["finished_at"] is None
