"""Per-tenant isolation tests (issue #31 / AO-GR-15 structural isolation).

The ledger is per-tenant: physically separate chain files, every record bound
to its chain's tenant, and no cross-tenant chain access through the API.
Negatives: tenant A's records never appear in B's export; a foreign-tenant
record smuggled into a chain is DETECTED on verify; A's key cannot read B's
payloads; appending to one tenant never writes to another.
"""

from __future__ import annotations

import json
import os

import pytest

from ledger import LedgerIntegrityError, open_ledger
from conftest import make_keystore, rewrite_ledger_file


def _seed(store, tenant="acme", n=2, action="model.call"):
    for i in range(1, n + 1):
        store.append(tenant, actor="user:alice", action=action,
                     resource=f"r/{tenant}/{i}", payload={"t": tenant, "i": i})


def _ledger_path(store, tenant):
    return os.path.join(store.directory, tenant + ".jsonl")


def test_separate_physical_files(file_store):
    _seed(file_store, tenant="acme", n=2)
    _seed(file_store, tenant="other", n=3)
    assert set(file_store.tenant_ids()) == {"acme", "other"}
    assert os.path.exists(_ledger_path(file_store, "acme"))
    assert os.path.exists(_ledger_path(file_store, "other"))
    assert _ledger_path(file_store, "acme") != _ledger_path(file_store, "other")


def test_export_is_scoped_to_requested_tenant(file_store):
    _seed(file_store, tenant="acme", n=2)
    _seed(file_store, tenant="other", n=3)
    acme_records = file_store.export("acme")
    other_records = file_store.export("other")
    assert len(acme_records) == 2
    assert all(r["tenantId"] == "acme" for r in acme_records)
    assert len(other_records) == 3
    assert all(r["tenantId"] == "other" for r in other_records)
    acme_ids = {json.dumps(r, sort_keys=True) for r in acme_records}
    other_ids = {json.dumps(r, sort_keys=True) for r in other_records}
    assert not (acme_ids & other_ids)  # no record appears in both chains


def test_append_stamps_target_tenant_only(file_store):
    record = file_store.append("acme", actor="user:alice", action="a",
                               payload={"x": 1})
    assert record["tenantId"] == "acme"
    # Only acme's file was created.
    assert os.path.exists(_ledger_path(file_store, "acme"))
    assert not os.path.exists(_ledger_path(file_store, "other"))


def test_verify_acme_unaffected_by_other(file_store):
    _seed(file_store, tenant="acme", n=2)
    _seed(file_store, tenant="other", n=3)
    assert file_store.verify("acme").status == "OK"
    assert file_store.verify("other").status == "OK"
    # Corrupting other's chain does not change acme's verdict.
    path = _ledger_path(file_store, "other")

    def mutate(seq, record):
        record["action"] = "tampered"
        return record

    rewrite_ledger_file(path, mutate)
    assert file_store.verify("other").status == "NOT-OK"
    assert file_store.verify("acme").status == "OK"


def test_smuggled_foreign_record_detected(file_store):
    _seed(file_store, tenant="acme", n=2)
    _seed(file_store, tenant="other", n=1)
    # Forge acme's third record from tenant other's content (cross-tenant
    # forgery): give it a valid next seq (3) so the SEQUENCE check passes and
    # the tenant-binding check is what trips the chain.
    acme_path = _ledger_path(file_store, "acme")
    other_path = _ledger_path(file_store, "other")
    with open(other_path, "r", encoding="utf-8") as handle:
        foreign = [json.loads(line) for line in handle.read().splitlines()
                   if line.strip() and not line.strip().startswith("#")]
    forged = dict(foreign[0])
    forged["seq"] = 3
    with open(acme_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(forged, sort_keys=True,
                                separators=(",", ":")) + "\n")

    verdict = file_store.verify("acme")
    assert verdict.status == "NOT-OK"
    assert verdict.broken_at == 3
    assert "does not match chain tenant" in verdict.detail
    # Fail closed: reads refuse the poisoned chain.
    with pytest.raises(LedgerIntegrityError):
        file_store.records("acme")


def test_cross_tenant_append_via_api_is_impossible(file_store):
    # The API has no way to write a record with a tenant different from the
    # target chain: the target is the single tenant argument.
    _seed(file_store, tenant="acme", n=1)
    rec = file_store.append("acme", actor="user:alice", action="b",
                            payload={"for": "other"})
    assert rec["tenantId"] == "acme"
    assert all(r["tenantId"] == "acme" for r in file_store.records("acme"))


def test_keys_are_tenant_scoped(file_store):
    _seed(file_store, tenant="acme", n=1, action="a")
    _seed(file_store, tenant="other", n=1, action="b")
    from ledger.verify import read_payload

    # Swap the keys: acme's store keyed with other's key cannot read acme.
    swapped = open_ledger(
        file_store.directory,
        keystore=make_keystore("acme", "other"),
    )
    # Correct keys decrypt each tenant's own payload...
    status, payload, _ = read_payload(swapped, "acme", 1)
    assert status == "OK" and payload == {"t": "acme", "i": 1}
    # ...and a foreign key cannot (CANNOT-ASSESS, never a wrong-tenant leak).
    bad = open_ledger(
        file_store.directory,
        keystore=make_keystore("other"),
    )
    status, payload, detail = read_payload(bad, "acme", 1)
    assert status == "CANNOT-ASSESS"
    assert payload is None


def test_in_memory_store_isolation(mem_store):
    _seed(mem_store, tenant="acme", n=2)
    _seed(mem_store, tenant="other", n=1)
    assert len(mem_store.export("acme")) == 2
    assert len(mem_store.export("other")) == 1
    assert mem_store.verify("acme").status == "OK"
    assert mem_store.verify("other").status == "OK"
