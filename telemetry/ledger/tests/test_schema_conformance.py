"""JSON-Schema conformance tests (issue #31).

``audit_event.schema.json`` is the human/machine-readable mirror of the record
contract; every record the store emits must conform to it, and a record that
violates it must fail. Uses ``jsonschema`` when available (skipped otherwise;
the authoritative validator is ``ledger.schema.validate_record``).
"""

from __future__ import annotations

import json
import os

import pytest

jsonschema = pytest.importorskip("jsonschema")

_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "audit_event.schema.json",
)


def _schema():
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _validator():
    return jsonschema.Draft7Validator(_schema())


def test_minimal_stored_record_conforms(file_store):
    record = file_store.append("acme", actor="agent:worker-1", action="model.call")
    errors = [e.message for e in _validator().iter_errors(record)]
    assert not errors


def test_full_stored_record_conforms(file_store):
    record = file_store.append(
        "acme",
        actor="agent:worker-1",
        action="model.call",
        impersonated_by="user:alice",
        resource="gateway/proxy",
        evidence="registry-event:abcd",
        model_used="claude-3-5-sonnet",
        cost_usd="0.0021",
        payload={"prompt": "hi", "note": "sensitive"},
    )
    errors = [e.message for e in _validator().iter_errors(record)]
    assert not errors


def test_schema_rejects_invalid_record():
    record = {
        "schemaVersion": 1,
        "seq": 1,
        "ts": "2026-09-08T12:00:00Z",
        "tenantId": "acme",
        "actor": "user:alice",
        "action": "a",
        "prevHash": "0" * 64,
        "hash": "0" * 64,
        "unknownField": True,  # additionalProperties: false
    }
    assert _validator().is_valid(record) is False


def test_schema_rejects_plaintext_payload_field():
    # A clear 'payload' (plaintext) field is not part of the stored contract.
    record = {
        "schemaVersion": 1,
        "seq": 1,
        "ts": "2026-09-08T12:00:00Z",
        "tenantId": "acme",
        "actor": "user:alice",
        "action": "a",
        "payload": {"pii": "plaintext-not-allowed"},  # not a stored field
        "prevHash": "0" * 64,
        "hash": "0" * 64,
    }
    assert _validator().is_valid(record) is False
