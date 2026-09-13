"""Per-run telemetry record schema + JSONL append helper (issue #232)."""

from __future__ import annotations

import pytest

import telemetry


def test_build_record_round_trips_required_fields():
    record = telemetry.build_record(
        run_id="r1", issue="232", agent="subagent-x", status="done",
        started_at="2026-09-13T00:00:00Z", finished_at="2026-09-13T00:01:00Z",
    )
    assert record["run_id"] == "r1"
    assert record["status"] == "done"
    assert record["detail"] == ""


def test_build_record_rejects_bad_status():
    with pytest.raises(telemetry.TelemetryError):
        telemetry.build_record(
            run_id="r1", issue="232", agent="subagent-x", status="bogus",
            started_at="2026-09-13T00:00:00Z",
        )


def test_validate_record_rejects_missing_fields():
    with pytest.raises(telemetry.TelemetryError):
        telemetry.validate_record({"run_id": "r1"})


def test_append_record_writes_one_json_line(tmp_path):
    path = tmp_path / "runs.jsonl"
    record = telemetry.build_record(
        run_id="r1", issue="232", agent="subagent-x", status="started",
        started_at="2026-09-13T00:00:00Z", finished_at=None,
    )
    telemetry.append_record(path, record)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record2 = telemetry.build_record(
        run_id="r2", issue="232", agent="subagent-x", status="done",
        started_at="2026-09-13T00:00:00Z", finished_at="2026-09-13T00:01:00Z",
    )
    telemetry.append_record(path, record2)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_append_record_rejects_invalid_record(tmp_path):
    path = tmp_path / "runs.jsonl"
    with pytest.raises(telemetry.TelemetryError):
        telemetry.append_record(path, {"run_id": "r1"})
    assert not path.exists()


def test_read_records_returns_empty_list_for_missing_file(tmp_path):
    assert telemetry.read_records(tmp_path / "missing.jsonl") == []


def test_read_records_reads_back_appended_records(tmp_path):
    path = tmp_path / "runs.jsonl"
    record = telemetry.build_record(
        run_id="r1", issue="232", agent="subagent-x", status="done",
        started_at="2026-09-13T00:00:00Z", finished_at="2026-09-13T00:01:00Z",
    )
    telemetry.append_record(path, record)
    records = telemetry.read_records(path)
    assert records == [record]
