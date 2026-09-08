"""Per-call HMAC signing + tamper-evident audit tests.

Every outbound call record is HMAC-SHA256 signed over a canonical JSON form
bound to tenant/agent/call fields. A tampered field, a missing signature, a
different key, or a malformed log line must all fail verification (fail
closed). The signer must refuse to run without a key.
"""

from __future__ import annotations

import json

import pytest

from dlp.hmac_audit import HmacAuditLog, HmacKeyError, HmacSigner

KEY = b"ao-dlp-test-hmac-key-0001"
OTHER_KEY = b"ao-dlp-test-hmac-key-0002"


def _record(**overrides) -> dict:
    rec = {
        "record_type": "egress_call",
        "record_id": "call-1",
        "call_id": "call-1",
        "ts": "2026-09-08T00:00:00Z",
        "tenant_id": "acme",
        "agent_id": "agent-1",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "payload_sha256": "0" * 64,
        "nonce": "abc123",
        "decision": "sent",
    }
    rec.update(overrides)
    return rec


def _signer() -> HmacSigner:
    return HmacSigner(key=KEY)


# -- sign / verify ------------------------------------------------------------


def test_signature_is_deterministic():
    signer = _signer()
    rec = _record()
    assert signer.sign(rec) == signer.sign(rec)


def test_verify_round_trip():
    signer = _signer()
    rec = _record()
    sig = signer.sign(rec)
    assert signer.verify(rec, sig)


def test_wrong_signature_fails():
    signer = _signer()
    rec = _record()
    assert not signer.verify(rec, "0" * 64)


def test_verify_requires_a_signature():
    signer = _signer()
    assert not signer.verify(_record(), "")


def test_different_key_cannot_verify():
    rec = _record()
    sig = _signer().sign(rec)
    assert not HmacSigner(key=OTHER_KEY).verify(rec, sig)


def test_tampering_any_field_invalidates_signature():
    signer = _signer()
    sig = signer.sign(_record())
    for field, value in (
        ("tenant_id", "globex"),
        ("agent_id", "agent-2"),
        ("call_id", "call-2"),
        ("provider", "anthropic"),
        ("endpoint", "https://api.anthropic.com/v1/messages"),
        ("payload_sha256", "f" * 64),
        ("ts", "2026-09-09T00:00:00Z"),
        ("decision", "blocked"),
        ("nonce", "tampered"),
    ):
        tampered = _record(**{field: value})
        assert not signer.verify(tampered, sig), f"tampered {field} still verified"


def test_signer_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("AO_DLP_HMAC_KEY", raising=False)
    with pytest.raises(HmacKeyError):
        HmacSigner()
    with pytest.raises(HmacKeyError):
        HmacSigner(key=b"")


def test_signer_reads_key_from_environment(monkeypatch):
    monkeypatch.setenv("AO_DLP_HMAC_KEY", "env-supplied-test-key")
    signer = HmacSigner()
    rec = _record()
    assert signer.verify(rec, signer.sign(rec))


# -- audit log ----------------------------------------------------------------


def test_append_signs_record_and_exposes_hmac():
    audit = HmacAuditLog(_signer())
    signed = audit.append(_record())
    assert "hmac" in signed
    assert audit.verify_record(signed)
    assert len(audit) == 1


def test_append_rejects_record_that_already_has_hmac():
    audit = HmacAuditLog(_signer())
    audit.append(_record())
    with pytest.raises(ValueError):
        audit.append({"hmac": "x", "call_id": "c2"})


def test_audit_log_file_replay_verifies_clean(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    audit = HmacAuditLog(_signer(), path=str(log_path))
    audit.append(_record(record_id="a", call_id="a"))
    audit.append(_record(record_id="b", call_id="b"))
    integrity = audit.verify()
    assert integrity.ok
    assert integrity.total == 2
    assert integrity.valid == 2
    assert integrity.tampered == []


def test_audit_log_detects_tampered_file_line(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    audit = HmacAuditLog(_signer(), path=str(log_path))
    audit.append(_record(record_id="a", call_id="a"))
    audit.append(_record(record_id="b", call_id="b"))

    # Tamper with the second line's tenant on disk.
    lines = log_path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["tenant_id"] = "evil"
    lines[1] = json.dumps(second, sort_keys=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Fresh log instance replays the file and flags the tampered record.
    replay = HmacAuditLog(_signer(), path=str(log_path))
    integrity = replay.verify()
    assert not integrity.ok
    assert integrity.tampered == ["b"]
    assert integrity.valid == 1


def test_audit_log_flags_malformed_lines(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    audit = HmacAuditLog(_signer(), path=str(log_path))
    audit.append(_record(record_id="a", call_id="a"))
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    replay = HmacAuditLog(_signer(), path=str(log_path))
    integrity = replay.verify()
    assert not integrity.ok
    assert integrity.malformed == 1
    assert integrity.valid == 1


def test_unsigned_record_is_not_verifiable():
    audit = HmacAuditLog(_signer())
    assert not audit.verify_record(_record())
