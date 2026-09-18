"""Tests for governance/knowledge/ledger.py (issue #887)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ledger


def test_append_and_read_roundtrip(tmp_path: Path):
    path = tmp_path / "sync-ledger.jsonl"
    ledger.append_event(
        ledger.KIND_PIN_DRIFT_CHECK,
        ledger.STATUS_OK,
        "no drift",
        ledger_path=path,
        timestamp="2026-09-17T00:00:00Z",
    )
    ledger.append_event(
        ledger.KIND_VENDOR_COMPLIANCE_GAP,
        ledger.STATUS_WARN,
        "shared-services #132: 4 finding(s) open",
        ledger_path=path,
        timestamp="2026-09-17T00:00:01Z",
    )
    events = ledger.read_ledger(path)
    assert len(events) == 2
    assert events[0].kind == ledger.KIND_PIN_DRIFT_CHECK
    assert events[0].status == ledger.STATUS_OK
    assert events[1].status == ledger.STATUS_WARN


def test_read_missing_ledger_returns_empty(tmp_path: Path):
    assert ledger.read_ledger(tmp_path / "nope.jsonl") == []


def test_append_writes_valid_json_lines(tmp_path: Path):
    path = tmp_path / "sync-ledger.jsonl"
    ledger.append_event(ledger.KIND_PIN_DRIFT_CHECK, ledger.STATUS_OK, "x", ledger_path=path)
    line = path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert set(parsed) == set(ledger.EVENT_FIELDS)


def test_unknown_kind_refused_by_name(tmp_path: Path):
    """Negative control: an unregistered event kind is refused, not appended."""
    path = tmp_path / "sync-ledger.jsonl"
    with pytest.raises(ledger.LedgerError):
        ledger.append_event("not-a-real-kind", ledger.STATUS_OK, "x", ledger_path=path)
    assert not path.exists()


def test_unknown_status_refused_by_name(tmp_path: Path):
    path = tmp_path / "sync-ledger.jsonl"
    with pytest.raises(ledger.LedgerError):
        ledger.append_event(ledger.KIND_PIN_DRIFT_CHECK, "not-a-real-status", "x", ledger_path=path)
    assert not path.exists()


def test_read_malformed_line_raises(tmp_path: Path):
    path = tmp_path / "sync-ledger.jsonl"
    path.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(ledger.LedgerError):
        ledger.read_ledger(path)


def test_read_line_missing_field_raises(tmp_path: Path):
    path = tmp_path / "sync-ledger.jsonl"
    path.write_text(json.dumps({"timestamp": "2026-09-17T00:00:00Z", "kind": ledger.KIND_PIN_DRIFT_CHECK}) + "\n", encoding="utf-8")
    with pytest.raises(ledger.LedgerError):
        ledger.read_ledger(path)
