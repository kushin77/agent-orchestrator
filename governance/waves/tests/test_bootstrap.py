"""Wave-bootstrap report — offline determinism and pin plumbing (issue #181)."""

from __future__ import annotations

import json

import bootstrap
import pytest


PINS = {
    "schema": "wave-pins/1",
    "note": "test seam",
    "as_of": "2026-09-13T00:00:00Z",
    "modules": {
        "deepseek": {"sha": "d111", "state": "pinned"},
        "codeidx-context-pack": {"sha": "c222", "state": "pinned"},
    },
    "board_deltas": [
        {"repo": "kushin77/code-indexing", "number": 128, "title": "shared-temporal"},
    ],
}


def test_offline_report_is_deterministic():
    first = bootstrap.offline_report("2026-09-12T00:00:00Z", PINS)
    second = bootstrap.offline_report("2026-09-12T00:00:00Z", PINS)
    assert first == second


def test_offline_report_uses_pins_as_of_not_wall_clock():
    text = bootstrap.offline_report("sha-123", PINS)
    assert "generated_at: `2026-09-13T00:00:00Z`" in text
    assert "since: `sha-123`" in text


def test_offline_report_names_the_pinned_shas():
    text = bootstrap.offline_report("since", PINS)
    assert "`d111`" in text
    assert "`c222`" in text


def test_offline_report_is_marked_as_a_test_seam():
    text = bootstrap.offline_report("since", PINS)
    assert "test seam" in text


def test_offline_report_lists_board_deltas():
    text = bootstrap.offline_report("since", PINS)
    assert "shared-temporal" in text
    assert "kushin77/code-indexing" in text


def test_empty_baseline_report_is_still_complete():
    baseline = {
        "schema": "wave-pins/1",
        "note": "test seam",
        "as_of": "2026-09-13T00:00:00Z",
        "modules": {"deepseek": {}, "codeidx-context-pack": {}},
        "board_deltas": [],
    }
    text = bootstrap.offline_report("since", baseline)
    assert "none recorded (empty baseline)" in text


def test_build_report_offline_writes_a_file_twice_identically(tmp_path):
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json.dumps(PINS) + "\n", encoding="utf-8")
    out_a = tmp_path / "a.md"
    out_b = tmp_path / "b.md"
    bootstrap.build_report("since", out_a, pins_path=pins_path, online=False)
    bootstrap.build_report("since", out_b, pins_path=pins_path, online=False)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_load_pins_rejects_an_unknown_schema(tmp_path):
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json.dumps({"schema": "other"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bootstrap.load_pins(pins_path)


def test_load_pins_accepts_the_committed_baseline():
    pins = bootstrap.load_pins(bootstrap.DEFAULT_PINS_PATH)
    assert pins["schema"] == "wave-pins/1"
    assert pins["board_deltas"] == []
