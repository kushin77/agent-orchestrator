"""Per-run telemetry wired into fleet/terminal.py's run path (issue #233)."""

from __future__ import annotations

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


def test_record_run_survives_an_unwritable_log(tmp_path, monkeypatch, capsys):
    """#285: an OSError (disk full / bad permission) must not kill the loop.

    `append_record` mkdirs the parent then opens the file, so pointing the log
    under a *file* raises FileExistsError (an OSError) — deterministic, no chmod.
    """
    blocker = tmp_path / "afile"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(telemetry, "RUNS_LOG", blocker / "runs.jsonl")

    terminal.record_run("d-285", 285, "subagent-abc", "done", "2026-09-13T00:00:00Z", "2026-09-13T00:05:00Z")

    assert "rejected" in capsys.readouterr().err


def test_loop_wires_record_run_into_the_run_path():
    """The run path must call record_run for both the start and the outcome."""
    import inspect

    source = inspect.getsource(terminal.loop)
    assert 'record_run(directive_id, issue, agent_id, "started"' in source
    assert "record_run(directive_id, issue, agent_id, run_status" in source
