"""Intake-adapter tests: registry/events-shaped records onto the audit ledger
(issue #31). The adapter consumes the registry event shape read-only and maps
it to a tenant-scoped, hash-chained, payload-encrypted audit record.
"""

from __future__ import annotations

import pytest

from ledger.adapter import ingest_registry_event


def _registry_event(seq=1, tenant="acme", event="register", agent_id="worker-1",
                    detail=None, ts="2026-09-08T12:00:00Z"):
    # Shape mirrors registry/events records (see registry/events/README.md).
    return {
        "seq": seq,
        "ts": ts,
        "event": event,
        "status": "registered",
        "tenantId": tenant,
        "agentId": agent_id,
        "actor": "admin",
        "detail": detail,
        "prevHash": "0" * 64,
        "hash": "a" * 64,
    }


def test_ingest_maps_registry_event_to_audit_record(file_store):
    event = _registry_event()
    record = ingest_registry_event(file_store, event)
    assert record["tenantId"] == "acme"
    assert record["action"] == "registry.register"
    assert record["resource"] == "registry/agents/worker-1"
    assert record["evidence"] == "registry-event:" + "a" * 64
    assert record["actor"] == "system:admin"  # bare registry actor -> system
    assert record["payloadEnc"] is None  # no detail -> nothing encrypted


def test_ingest_encrypts_detail_and_round_trips(file_store):
    event = _registry_event(detail={"taskType": "route", "note": "sensitive"})
    record = ingest_registry_event(file_store, event)
    assert record["payloadEnc"] is not None
    from ledger.verify import read_payload

    status, payload, _ = read_payload(file_store, "acme", 1)
    assert status == "OK"
    assert payload == {"taskType": "route", "note": "sensitive"}


def test_ingest_chains_multiple_events(file_store):
    for seq in (1, 2):
        ingest_registry_event(
            file_store, _registry_event(seq=seq, event="activate", agent_id="w-1")
        )
    records = file_store.records("acme")
    assert [r["seq"] for r in records] == [1, 2]
    assert records[1]["prevHash"] == records[0]["hash"]
    assert file_store.verify("acme").status == "OK"


def test_ingest_does_not_mutate_input(file_store):
    event = _registry_event(detail={"x": 1})
    snapshot = dict(event)
    ingest_registry_event(file_store, event)
    assert event == snapshot  # read-only consumption


def test_ingest_with_detail_false_leaves_payload_null(file_store):
    record = ingest_registry_event(
        file_store, _registry_event(detail={"x": 1}), with_detail=False
    )
    assert record["payloadEnc"] is None


def test_ingest_keeps_tenant_isolation(file_store):
    ingest_registry_event(file_store, _registry_event(tenant="acme"))
    ingest_registry_event(file_store, _registry_event(tenant="other"))
    assert {r["tenantId"] for r in file_store.records("acme")} == {"acme"}
    assert {r["tenantId"] for r in file_store.records("other")} == {"other"}


def test_ingest_refuses_detail_without_tenant_key(tmp_path):
    from ledger import open_ledger

    # No keystore: appending detail (a sensitive payload) must fail closed.
    store = open_ledger(str(tmp_path / "audit"))
    with pytest.raises(Exception):
        ingest_registry_event(store, _registry_event(detail={"x": 1}))
    assert store.tenant_ids() == []


def test_ingest_rejects_malformed_input(file_store):
    with pytest.raises((TypeError, ValueError)):
        ingest_registry_event(file_store, {"no": "tenant"})
    with pytest.raises((TypeError, ValueError)):
        ingest_registry_event(file_store, "not-a-dict")
