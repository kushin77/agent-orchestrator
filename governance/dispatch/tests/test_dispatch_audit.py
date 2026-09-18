"""The append-only arbitration audit trail (issue #885)."""

from __future__ import annotations

from datetime import datetime, timezone

import audit
import claims
import schema as dispatch_schema
from model import Issue, Snapshot


def _board(now):
    return Snapshot(
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="test",
        issues={1: Issue(1, "open", milestone="M"), 2: Issue(2, "closed", state="closed")},
    )


def test_refusal_produces_exactly_one_valid_record(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    board = _board(now)
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"

    try:
        claims.arbitrate(2, "agent-a", "lane-a", board, ledger=ledger, lock_dir=locks, now=now)
    except claims.ClaimRefused:
        pass

    trail = ledger.parent / "dispatch-audit.jsonl"
    records = audit.read(trail)
    assert len(records) == 1
    record = records[0]
    assert record["kind"] == "refusal"
    assert record["reason"] == "issue-closed"
    assert dispatch_schema.problems(record, dispatch_schema.SHAPE_AUDIT_RECORD) == ()


def test_grant_produces_exactly_one_valid_record(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    board = _board(now)
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"

    claims.arbitrate(1, "agent-a", "lane-a", board, ledger=ledger, lock_dir=locks, now=now)

    trail = ledger.parent / "dispatch-audit.jsonl"
    records = audit.read(trail)
    assert len(records) == 1
    assert records[0]["kind"] == "grant"
    assert dispatch_schema.problems(records[0], dispatch_schema.SHAPE_AUDIT_RECORD) == ()


def test_audit_ledger_is_adjacent_not_a_replacement(tmp_path):
    """The claims ledger keeps recording exactly as before; audit is additive."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    board = _board(now)
    ledger = tmp_path / "claims"
    locks = tmp_path / "locks"

    claims.arbitrate(1, "agent-a", "lane-a", board, ledger=ledger, lock_dir=locks, now=now)
    claim_event = claims.claim(1, "agent-a", "lane-a", board, ledger=ledger, lock_dir=locks, now=now)

    assert claims.read_ledger(ledger) == [claim_event]
    assert len(audit.read(ledger.parent / "dispatch-audit.jsonl")) == 2  # arbitrate() grant + claim()'s own arbitrate() grant


def test_append_grows_the_trail_monotonically(tmp_path):
    """Two sequential appends leave the first record an exact prefix of the file."""
    path = tmp_path / "trail.jsonl"
    audit.append(audit.record_refusal(issue=1, agent="a", at="t1", reason="r1", detail="d1"), path=path)
    first = path.read_bytes()
    audit.append(audit.record_refusal(issue=2, agent="b", at="t2", reason="r2", detail="d2"), path=path)
    second = path.read_bytes()
    assert second.startswith(first)
    assert len(audit.read(path)) == 2


def test_read_refuses_a_record_carrying_the_wrong_schema_tag(tmp_path):
    path = tmp_path / "trail.jsonl"
    path.write_text('{"schema": "not-the-dispatch-schema", "kind": "grant"}\n', encoding="utf-8")
    import pytest

    with pytest.raises(audit.AuditUnavailable):
        audit.read(path)
