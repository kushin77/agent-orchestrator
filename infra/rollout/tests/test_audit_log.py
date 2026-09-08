"""Promotion audit-log tests (issue #45): append-only hash chain.

Every promotion and rollback is audit-logged; the log is append-only and
hash-chained so any tamper, deletion or reordering of a past record is
detected by ``verify``.
"""

from __future__ import annotations

from infra.rollout.engine import AuditLog


def test_append_and_verify(tmp_path) -> None:
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    log.append(action="promote", flag="services.registry", from_stage="off", to_stage="canary")
    log.append(action="promote", flag="services.gateway", from_stage="off", to_stage="canary")
    assert log.verify()
    assert [r["seq"] for r in log.records()] == [1, 2]


def test_reopen_and_verify(tmp_path) -> None:
    path = str(tmp_path / "audit.jsonl")
    AuditLog(path).append(action="promote", flag="services.registry", from_stage="off", to_stage="canary")
    AuditLog(path).append(action="rollback", flag="services.registry", from_stage="canary", to_stage="off")
    reopened = AuditLog(path)
    assert reopened.verify()
    assert len(reopened.records()) == 2


def test_tampered_record_breaks_verify(tmp_path) -> None:
    path = str(tmp_path / "audit.jsonl")
    AuditLog(path).append(action="promote", flag="services.registry", from_stage="off", to_stage="canary")
    AuditLog(path).append(action="promote", flag="services.gateway", from_stage="off", to_stage="canary")
    # Rewrite the first record with an edited action (same line length story).
    lines = open(path, encoding="utf-8").read().splitlines()
    import json

    record = json.loads(lines[0])
    record["to_stage"] = "full"
    lines[0] = json.dumps(record, sort_keys=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log = AuditLog(path)
    assert not log.verify()


def test_reordered_records_break_verify(tmp_path) -> None:
    path = str(tmp_path / "audit.jsonl")
    AuditLog(path).append(action="promote", flag="services.registry", from_stage="off", to_stage="canary")
    AuditLog(path).append(action="promote", flag="services.gateway", from_stage="off", to_stage="canary")
    # Swap the two records: the second no longer chains to the first.
    lines = open(path, encoding="utf-8").read().splitlines()
    lines.reverse()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log = AuditLog(path)
    assert not log.verify()


def test_first_record_chains_to_genesis(tmp_path) -> None:
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    record = log.append(action="promote", flag="services.registry", from_stage="off", to_stage="canary")
    assert record["prev_hash"] == "0" * 64
    assert record["hash"] == log._hash(record)
