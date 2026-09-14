"""Audit seam: append-only, one record per toggle, observable by a second reader."""

from __future__ import annotations

from controls.audit import (
    ControlAuditRecord,
    InMemoryControlAuditLog,
    JsonlControlAuditLog,
    build_toggle_record,
)


def test_in_memory_is_append_only_and_sequences():
    log = InMemoryControlAuditLog()
    assert log.next_sequence() == 1
    record = build_toggle_record(
        sequence=log.next_sequence(),
        control_id="demo",
        actor="tester",
        before=False,
        after=True,
        status_before=246,
        status_after=446,
    )
    log.append(record)
    assert len(log) == 1
    assert log.next_sequence() == 2
    assert log.records()[0].control_id == "demo"


def test_jsonl_second_reader_sees_every_record(tmp_path):
    path = tmp_path / "audit.jsonl"
    writer = JsonlControlAuditLog(path)
    writer.append(
        build_toggle_record(
            sequence=writer.next_sequence(),
            control_id="demo",
            actor="writer",
            before=False,
            after=True,
            status_before=246,
            status_after=446,
            audit_ref="guardrails/policy/controls.yaml#demo",
            reason="proof",
        )
    )
    reader = JsonlControlAuditLog(path)
    assert len(reader) == 1
    record = reader.records()[0]
    assert record.control_id == "demo"
    assert record.after is True
    assert record.status_after == 446
    assert record.audit_ref.endswith("#demo")
    assert record.reason == "proof"
    assert record.timestamp


def test_jsonl_continues_sequence_across_readers(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = JsonlControlAuditLog(path)
    first.append(
        build_toggle_record(
            sequence=first.next_sequence(),
            control_id="demo",
            actor="a",
            before=False,
            after=True,
            status_before=246,
            status_after=446,
        )
    )
    second = JsonlControlAuditLog(path)
    assert second.next_sequence() == 2
    second.append(
        build_toggle_record(
            sequence=second.next_sequence(),
            control_id="demo",
            actor="a",
            before=True,
            after=False,
            status_before=446,
            status_after=246,
        )
    )
    assert len(JsonlControlAuditLog(path).records()) == 2


def test_record_round_trips_through_mapping():
    record = build_toggle_record(
        sequence=7,
        control_id="demo",
        actor="tester",
        before=False,
        after=True,
        status_before=246,
        status_after=446,
        audit_ref="ref",
        reason="why",
        timestamp="2026-09-14T00:00:00+00:00",
    )
    restored = ControlAuditRecord.from_dict(record.to_dict())
    assert restored == record
