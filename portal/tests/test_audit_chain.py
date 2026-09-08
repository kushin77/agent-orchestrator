"""Audit verify-chain tests (issue #39 audit view).

The audit ledger consumes the telemetry/ledger (issue #31) record vocabulary
(seq/ts/tenantId/actor/action/prevHash/hash) and the Audit view exposes a
verify chain. These tests prove the chain is append-only and tamper-evident:
any edit, deletion or reorder is detected as NOT-OK.
"""

from __future__ import annotations

from portal.server.auditlog import AuditLedger
from portal.server.state import seed_state


def test_ledger_appends_chained_records():
    ledger = AuditLedger("acme")
    first = ledger.append("user:alice", "control.toggle", resource="control:demo")
    second = ledger.append("system:policy", "policy.decision", resource="model.call")
    assert first.seq == 1
    assert second.seq == 2
    assert first.prev_hash == "0" * 64
    assert second.prev_hash == first.hash
    assert ledger.verify()["status"] == "OK"


def test_seed_chains_verify_ok():
    state = seed_state()
    for tenant_id in state.tenant_ids():
        verdict = state.audit[tenant_id].verify()
        assert verdict["status"] == "OK", (tenant_id, verdict)


def test_tamper_is_detected():
    state = seed_state()
    ledger = state.audit["acme"]
    assert ledger.verify()["status"] == "OK"
    ledger.tamper(1, field="action", value="policy.decision")
    verdict = ledger.verify()
    assert verdict["status"] == "NOT-OK"
    assert verdict["seq"] == 1


def test_deleting_an_interior_record_breaks_the_link():
    ledger = AuditLedger("acme")
    ledger.append("user:alice", "console.login")
    ledger.append("user:alice", "agent.pause")
    ledger.append("user:alice", "control.toggle", resource="control:demo")
    # Simulate a deletion of the middle record (records are immutable in the
    # append-only contract; this mutates the in-memory list to prove the
    # verification detects the gap).
    ledger._records.pop(1)
    verdict = ledger.verify()
    assert verdict["status"] == "NOT-OK"
    assert verdict["seq"] == 3  # record 3 no longer links to record 1's hash


def test_api_audit_view_and_verify(app):
    from conftest import login_as

    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/audit")
    assert status == 200
    records = payload["data"]["records"]
    assert records
    # Records carry the ledger vocabulary fields.
    record = records[-1]
    for field in ("seq", "ts", "tenantId", "actor", "action", "prevHash", "hash"):
        assert field in record
    # The view's verify endpoint reports OK on the live chain.
    status, payload = api.post("/api/tenants/acme/audit/verify")
    assert status == 200
    assert payload["data"]["verify"]["status"] == "OK"


def test_mutations_append_to_the_chain(app):
    from conftest import login_as

    api = login_as(app, "alice@acme.example.com", "acme")
    before = len(api.get("/api/tenants/acme/audit")[1]["data"]["records"])
    status, payload = api.post(
        "/api/tenants/acme/agents/researcher-1/pause", {}
    )
    assert status == 200
    after = len(api.get("/api/tenants/acme/audit")[1]["data"]["records"])
    assert after == before + 1
    assert payload["data"]["status"] == "paused"
