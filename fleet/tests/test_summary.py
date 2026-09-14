"""The per-run telemetry summary verb (issue #234).

These tests prove the summary computes its aggregates from the records, not
from any hardcoded or source-scanned text: every assertion is over a value the
code derived, and several tests mutate the input to show the output follows it.
A stub that returned a fixed string, or a grep for a keyword, would fail these
tests.
"""

from __future__ import annotations

import json

import pytest

import summary


def _record(issue, tier, rc, duration, agent="subagent-a", runner="deepseek",
            directive_id=None):
    return {
        "issue": issue,
        "agent": agent,
        "lane": "fleet",
        "tier": tier,
        "thinking": "low",
        "runner": runner,
        "rc": rc,
        "duration": duration,
        "worktree": "/tmp/ao-1",
        "directive_id": directive_id or f"d-{issue}",
    }


def test_summarize_counts_runs_tier_mix_outcome_mix_and_mean_duration():
    records = [
        _record("301", "pro", 0, 30.0),
        _record("302", "flash", 0, 60.0),
        _record("306", "flash", 1, 15.0),
    ]
    stats = summary.summarize(records)
    assert stats["runs"] == 3
    assert stats["tier_mix"] == {"pro": 1, "flash": 2}
    assert stats["outcome_mix"] == {"0": 2, "1": 1}
    assert stats["mean_duration"] == pytest.approx(35.0)


def test_mean_duration_is_sensitive_to_the_records():
    first = summary.summarize([
        _record("1", "flash", 0, 10.0),
        _record("2", "flash", 0, 20.0),
    ])
    second = summary.summarize([
        _record("1", "flash", 0, 100.0),
        _record("2", "flash", 0, 200.0),
    ])
    assert first["mean_duration"] == pytest.approx(15.0)
    assert second["mean_duration"] == pytest.approx(150.0)
    assert first["mean_duration"] != second["mean_duration"]


def test_tier_mix_follows_a_mutated_record():
    records = [
        _record("301", "pro", 0, 30.0),
        _record("302", "flash", 0, 60.0),
    ]
    before = summary.summarize(records)
    records[1]["tier"] = "pro"
    after = summary.summarize(records)
    assert before["tier_mix"] == {"pro": 1, "flash": 1}
    assert after["tier_mix"] == {"pro": 2}


def test_empty_log_reports_zero_runs():
    stats = summary.summarize([])
    assert stats["runs"] == 0
    assert stats["tier_mix"] == {}
    assert stats["outcome_mix"] == {}
    assert stats["mean_duration"] is None


def test_missing_fields_are_bucketed_not_crashed():
    stats = summary.summarize([
        {"issue": "1", "agent": "a"},  # no tier, no rc, no duration
    ])
    assert stats["runs"] == 1
    assert stats["tier_mix"] == {"unknown": 1}
    assert stats["outcome_mix"] == {"unknown": 1}
    assert stats["mean_duration"] is None


def test_render_report_contains_the_computed_aggregates():
    records = [
        _record("301", "pro", 0, 30.0),
        _record("302", "flash", 0, 60.0),
        _record("306", "flash", 1, 15.0),
    ]
    text = summary.render_report(records)
    assert "runs: 3" in text
    assert "pro=1" in text and "flash=2" in text
    assert "35.00s" in text


def test_main_reads_a_log_and_prints_the_report(tmp_path, capsys):
    log = tmp_path / "telemetry.jsonl"
    record = _record("234", "flash", 0, 12.0, agent="mech", directive_id="d-234")
    log.write_text(json.dumps(record) + "\n", encoding="utf-8")

    rc = summary.main(["--log", str(log)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "runs: 1" in captured.out
    assert "flash=1" in captured.out
    assert "12.00s" in captured.out
