"""Schema / record-contract tests for telemetry/ledger (issue #31)."""

from __future__ import annotations

import pytest

from ledger.schema import (
    GENESIS_HASH,
    SCHEMA_VERSION,
    build_record,
    canonical_bytes,
    encode_actor,
    now_utc,
    parse_actor,
    record_hash,
    validate_record,
)
from ledger.errors import LedgerValidationError


def _base(**overrides):
    record = build_record(
        tenant_id="acme",
        seq=1,
        ts=now_utc(),
        prev_hash=GENESIS_HASH,
        actor="user:alice",
        action="model.call",
    )
    record.update(overrides)
    record["hash"] = record_hash(record)  # keep the hash honest after edits
    return record


def test_build_record_minimal_contract():
    record = _base()
    assert record["schemaVersion"] == SCHEMA_VERSION
    assert record["tenantId"] == "acme"
    assert record["prevHash"] == GENESIS_HASH
    assert len(record["hash"]) == 64
    validate_record(record)  # round-trips through the validator


def test_build_record_with_all_optional_fields():
    record = build_record(
        tenant_id="acme",
        seq=1,
        ts="2026-09-08T12:00:00Z",
        prev_hash=GENESIS_HASH,
        actor="agent:worker-1",
        impersonated_by="user:alice",
        action="policy.decision",
        resource="guardrails/dlp",
        evidence="sha256:deadbeef",
        model_used="claude-3-5-sonnet",
        cost_usd="0.0021",
        payload_enc={"v": 1, "alg": "AES-256-GCM", "keyId": "acme:k1"},
    )
    validate_record(record, tenant_id="acme")
    assert record["impersonatedBy"] == "user:alice"
    assert record["modelUsed"] == "claude-3-5-sonnet"


def test_hash_is_deterministic_and_covers_content():
    a = _base()
    b = _base()
    assert a["hash"] == b["hash"]
    # Changing any content field changes the hash (canonical_bytes excludes
    # only the hash field itself).
    tampered = _base(action="model.callX")
    assert tampered["hash"] != a["hash"]
    assert canonical_bytes(_base()) == canonical_bytes(_base())


def test_hash_covers_prev_hash_and_encrypted_payload():
    first = _base()
    second = _base(seq=2, prev_hash=first["hash"])
    # Two identical records except seq/prevHash must hash differently.
    assert first["hash"] != second["hash"]


def test_actor_canonical_form():
    assert encode_actor("agent", "worker-1") == "agent:worker-1"
    assert parse_actor("user:alice") == ("user", "alice")
    with pytest.raises(LedgerValidationError):
        encode_actor("robot", "r1")  # kind outside the closed set
    with pytest.raises(LedgerValidationError):
        parse_actor("alice")  # no kind:id separator


def test_validate_rejects_bad_records():
    with pytest.raises(LedgerValidationError):
        validate_record({"not": "a record"})
    bad_variants = [
        dict(seq=0),  # seq must be >= 1
        dict(ts="2026-09-08 12:00:00"),  # not RFC 3339 Z
        dict(tenantId=""),  # empty tenant
        dict(actor="alice"),  # not canonical kind:id
        dict(action=""),
        dict(schemaVersion=2),
    ]
    for variant in bad_variants:
        with pytest.raises(LedgerValidationError):
            validate_record(_base(**variant))
    # A bad hash is only rejected when it is not recomputed over it.
    bad_hash = _base()
    bad_hash["hash"] = "zzz"
    with pytest.raises(LedgerValidationError):
        validate_record(bad_hash)


def test_validate_rejects_cross_tenant_binding():
    with pytest.raises(LedgerValidationError):
        validate_record(_base(), tenant_id="other")


def test_validate_rejects_foreign_fields_in_sequence():
    # Record declares seq=1 but validator is told seq=2 -> seq mismatch.
    with pytest.raises(LedgerValidationError):
        validate_record(_base(), seq=2)


def test_now_utc_is_rfc3339():
    from ledger.schema import _TS_RE

    assert _TS_RE.match(now_utc())
