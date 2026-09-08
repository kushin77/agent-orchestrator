"""Audit-log tests (issue #26 acceptance #2: every block audit-logged)."""

from __future__ import annotations

from policy import (
    AuditRecord,
    DecisionLevel,
    InMemoryAuditLog,
    JsonlAuditLog,
)


def _record(**overrides) -> AuditRecord:
    base = dict(
        sequence=1,
        timestamp="2026-09-08T00:00:00+00:00",
        decision="block",
        action="model.call",
        outcome="blocked",
        subject="agent-1",
        tenant="acme",
        policy_ids=("model-call-budget",),
        rule_ids=("over-budget-block",),
        reason="over budget",
        error=None,
        evidence={"decision": "block"},
    )
    base.update(overrides)
    return AuditRecord(**base)


def test_in_memory_log_appends_and_reads():
    log = InMemoryAuditLog()
    log.append(_record(sequence=1))
    log.append(_record(sequence=2, decision="warn", outcome="allowed"))
    assert len(log) == 2
    assert [record.sequence for record in log.records()] == [1, 2]


def test_audit_record_round_trips_through_dict():
    record = _record()
    rebuilt = AuditRecord.from_dict(record.to_dict())
    assert rebuilt == record
    assert rebuilt.policy_ids == ("model-call-budget",)
    assert rebuilt.evidence["decision"] == "block"


def test_jsonl_audit_log_is_append_only(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = JsonlAuditLog(path)
    log.append(_record(sequence=1))
    log.append(_record(sequence=2, decision="log", outcome="allowed"))
    assert len(log.records()) == 2
    # File content is one JSON object per line.
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    # Reading an empty/nonexistent log yields no records.
    assert JsonlAuditLog(tmp_path / "missing.jsonl").records() == ()


def test_jsonl_log_persists_across_instances(tmp_path):
    path = tmp_path / "audit.jsonl"
    JsonlAuditLog(path).append(_record(sequence=1))
    second = JsonlAuditLog(path)
    second.append(_record(sequence=2))
    assert len(second.records()) == 2  # reopened, previous line still there
