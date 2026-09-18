"""The append-only reconciliation ledger (issue #885)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.reconcile import ledger


def test_append_writes_one_valid_line(root: Path):
    ledger.append(
        root,
        {
            "kind": ledger.SWEEP_DECISION,
            "at": 1000.0,
            "at_iso": "2026-09-17T00:00:00Z",
            "session_id": "s1",
            "issue": 1,
            "agent": "a",
            "outcome": "reclaimed",
            "code": "reconcile.reclaimed",
            "reason": "landed",
        },
    )
    records = ledger.read(root)
    assert len(records) == 1
    assert records[0]["outcome"] == "reclaimed"


def test_append_is_append_only(root: Path):
    for i in range(3):
        ledger.record_sweep_decision(
            root, session_id=f"s{i}", issue=i, agent="a", outcome="reported",
            code="reconcile.reported", reason="active", at=1000.0 + i,
        )
    assert len(ledger.read(root)) == 3


def test_invalid_record_is_refused_not_written(root: Path):
    with pytest.raises(ledger.LedgerUnavailable):
        ledger.append(root, {"kind": "not-a-real-kind", "at": 1.0, "at_iso": "x"})
    assert ledger.read(root) == []


def test_refusal_produces_exactly_one_record(root: Path):
    """One refusal -> one record (the #885 brief's audit mutation test)."""
    ledger.record_sweep_decision(
        root, session_id="refused-1", issue=9, agent="a", outcome="refused",
        code="reconcile.batch-limit-exceeded", reason="batch limit reached", at=1000.0,
    )
    records = [r for r in ledger.read(root) if r["outcome"] == "refused"]
    assert len(records) == 1
    assert records[0]["code"] == "reconcile.batch-limit-exceeded"


def test_verify_ok_on_a_clean_ledger(root: Path):
    ledger.record_sweep_decision(
        root, session_id="s1", issue=1, agent="a", outcome="reclaimed",
        code="reconcile.reclaimed", reason="landed", at=1000.0,
    )
    ok, description = ledger.verify(root)
    assert ok, description


def test_verify_detects_a_corrupted_record(root: Path):
    ledger.record_sweep_decision(
        root, session_id="s1", issue=1, agent="a", outcome="reclaimed",
        code="reconcile.reclaimed", reason="landed", at=1000.0,
    )
    path = ledger.ledger_path(root)
    path.write_text('{"schema": "ao.reconcile/ledger-record-v1", "kind": "not-real"}\n', encoding="utf-8")
    ok, description = ledger.verify(root)
    assert not ok
    assert "line 1" in description


def test_verify_ok_with_no_ledger_yet(root: Path):
    ok, description = ledger.verify(root)
    assert ok, description


def test_real_tree_verdict_record_shape(root: Path):
    class FakeVerdict:
        assessable = True
        ok = True
        new_violations = ()
        stale_entries = ()
        young = ()

    ledger.record_real_tree_verdict(root, FakeVerdict(), at=1234.0)
    records = ledger.read(root)
    assert records[-1]["kind"] == ledger.REAL_TREE_VERDICT
    assert records[-1]["ok"] is True
