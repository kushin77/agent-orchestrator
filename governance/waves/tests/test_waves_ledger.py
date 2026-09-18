"""Wave ledger maths, append/query round-trip, and SLO assertions (issue #181)."""

from __future__ import annotations

import json

import ledger
import pytest
from model import WaveRecord


# --- duration + verify-rate maths ------------------------------------------


def test_duration_s_is_whole_seconds_between_timestamps():
    assert ledger.compute_duration_s("2026-09-13T10:00:00Z", "2026-09-13T10:00:45Z") == 45


def test_duration_s_treats_empty_side_as_zero():
    assert ledger.compute_duration_s("", "2026-09-13T10:00:00Z") == 0
    assert ledger.compute_duration_s("2026-09-13T10:00:00Z", "") == 0


def test_duration_s_refuses_a_finish_before_the_start():
    with pytest.raises(ValueError):
        ledger.compute_duration_s("2026-09-13T10:00:00Z", "2026-09-13T09:00:00Z")


def test_verify_rate_is_one_minus_failures_over_issues():
    assert ledger.compute_verify_rate(2, 10) == 0.8
    assert ledger.compute_verify_rate(0, 10) == 1.0


def test_verify_rate_is_vacuous_one_when_there_are_no_issues():
    assert ledger.compute_verify_rate(0, 0) == 1.0


def test_verify_rate_clamps_to_the_unit_interval():
    assert ledger.compute_verify_rate(15, 10) == 0.0
    assert ledger.compute_verify_rate(-1, 10) == 1.0


def test_build_record_computes_the_derived_fields():
    record = ledger.build_record(
        wave_id="w1",
        started_at="2026-09-13T10:00:00Z",
        finished_at="2026-09-13T10:30:00Z",
        issues=10,
        cost_usd=1.5,
        escalations=0,
        verify_failures=2,
        module_pins={"deepseek": "abc"},
        direction_issues=["kushin77/code-indexing#200"],
    )
    assert record.duration_s == 1800
    assert record.verify_rate == 0.8


# --- append / query round-trip ----------------------------------------------


def test_append_and_read_round_trip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_record(ledger.build_record(wave_id="w1", issues=5, verify_failures=1), path)
    records = ledger.read_ledger(path)
    assert [r.wave_id for r in records] == ["w1"]
    assert records[0].issues == 5
    assert records[0].verify_rate == 0.8


def test_query_filters_by_wave_id_most_recent_first(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_record(
        ledger.build_record(wave_id="w1", finished_at="2026-09-13T10:00:00Z"), path
    )
    ledger.append_record(
        ledger.build_record(wave_id="w2", finished_at="2026-09-13T11:00:00Z"), path
    )
    records = ledger.read_ledger(path)
    assert [r.wave_id for r in ledger.query(records)] == ["w2", "w1"]
    assert [r.wave_id for r in ledger.query(records, "w1")] == ["w1"]


def test_malformed_line_raises_with_its_line_number(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_record(ledger.build_record(wave_id="w1"), path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    with pytest.raises(ValueError) as excinfo:
        ledger.read_ledger(path)
    assert "ledger.jsonl:2" in str(excinfo.value)


def test_closed_vocabulary_rejects_an_unknown_field(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps({"wave_id": "w1", "mystery": 1}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        ledger.read_ledger(path)
    assert "mystery" in str(excinfo.value)


# --- SLO assertions: recorded, never silently dropped -----------------------


def test_a_wave_that_escalated_is_recorded_not_dropped(tmp_path):
    path = tmp_path / "ledger.jsonl"
    record = ledger.build_record(wave_id="w1", issues=4, escalations=2)
    ledger.append_record(record, path)
    assert [r.escalations for r in ledger.read_ledger(path)] == [2]
    assert any("escalations" in v for v in ledger.slo_violations(record))


def test_a_wave_within_slo_has_no_violations():
    record = ledger.build_record(wave_id="w1", issues=10, verify_failures=0, escalations=0)
    assert ledger.slo_violations(record) == []


def test_vacuous_verify_rate_is_flagged_as_an_slo_breach():
    record = ledger.build_record(wave_id="w1", issues=0)
    assert any("vacuous" in v for v in ledger.slo_violations(record))


def test_ledger_stats_aggregate_the_loop(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_record(ledger.build_record(wave_id="w1", issues=2, cost_usd=1.0), path)
    ledger.append_record(ledger.build_record(wave_id="w2", issues=3, cost_usd=2.0), path)
    stats = ledger.ledger_stats(ledger.read_ledger(path))
    assert stats["waves"] == 2
    assert stats["total_issues"] == 5
    assert stats["total_cost_usd"] == 3.0
