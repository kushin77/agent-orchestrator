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


# The claim "the loop wires record_run into the run path — for both the start and
# the outcome" is asserted by EFFECT in
# ``test_isolation_guard.py::test_loop_wires_record_run_into_the_run_path``: it
# drives ``terminal.loop`` through one real dispatch with every out-of-process
# seam stubbed and reads the records the run path wrote back from the JSONL, so
# deleting either emission fails it. It is deliberately NOT asserted here by
# grepping the source: the previous version of that test did exactly that and
# stayed green with the call commented out (#286, GR-12). A source-text
# assertion cannot be the load-bearing check for a call — it is green while the
# characters are present and the call is dead, and red when the call is merely
# reformatted.
