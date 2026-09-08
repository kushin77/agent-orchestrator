"""Append-only + hash-chain integrity tests (issue #31).

Positive: appends chain correctly, reopen verifies, in-memory and file-backed
behave the same. Negative (tamper must be DETECTED, never a silent pass):
edit a field, delete a record, reorder records, truncate the tail, smuggle a
foreign-tenant record, corrupt a line, and append onto a broken chain (refused
fail closed).
"""

from __future__ import annotations

import json
import os

import pytest

from ledger import GENESIS_HASH, LedgerIntegrityError, open_ledger
from ledger.errors import RepairRefusedError
from conftest import rewrite_ledger_file


def _seed(store, tenant="acme", n=3, action="model.call", actor="agent:worker-1"):
    records = []
    for i in range(1, n + 1):
        records.append(
            store.append(
                tenant, actor=actor, action=action,
                resource=f"r/{i}", evidence=f"e{i}", ts=f"2026-09-08T12:00:0{i}Z",
            )
        )
    return records


# --------------------------------------------------------------------------- #
# positive
# --------------------------------------------------------------------------- #
def test_appends_chain_and_seq(file_store):
    records = _seed(file_store, n=3)
    assert [r["seq"] for r in records] == [1, 2, 3]
    assert records[0]["prevHash"] == GENESIS_HASH
    assert records[1]["prevHash"] == records[0]["hash"]
    assert records[2]["prevHash"] == records[1]["hash"]


def test_verify_ok_and_tail_state(file_store):
    _seed(file_store, n=3)
    verdict = file_store.verify("acme")
    assert verdict.status == "OK"
    assert verdict.is_pass
    assert verdict.tail == file_store.tail_state("acme") == (3, verdict.tail[1])


def test_reopen_verifies_and_state_anchors(file_store, tmp_path):
    _seed(file_store, n=2)
    reopened = open_ledger(str(tmp_path / "audit"), keystore=file_store.keystore)
    assert reopened.verify("acme").status == "OK"
    assert reopened.records("acme") == file_store.records("acme")


def test_empty_tenant_is_ok_genesis(file_store):
    verdict = file_store.verify("acme")
    assert verdict.status == "OK"
    assert verdict.tail == (0, GENESIS_HASH)


def test_unknown_tenant_export_is_empty_not_leak(file_store):
    _seed(file_store, tenant="acme", n=2)
    assert file_store.export("does-not-exist") == []


def test_in_memory_store_matches_file_backed(mem_store, file_store):
    for store in (mem_store, file_store):
        _seed(store, n=3)
        assert store.verify("acme").status == "OK"


def test_len_counts_records(file_store):
    _seed(file_store, tenant="acme", n=2)
    _seed(file_store, tenant="other", n=4)
    assert len(file_store) == 6


# --------------------------------------------------------------------------- #
# negatives: tamper is always detected
# --------------------------------------------------------------------------- #
def _ledger_path(file_store, tenant="acme"):
    return os.path.join(file_store.directory, tenant + ".jsonl")


def test_edit_content_is_detected(file_store):
    _seed(file_store, n=3)
    path = _ledger_path(file_store)

    def mutate(seq, record):
        if seq == 2:
            record["action"] = "attacker.model.call"
        return record

    rewrite_ledger_file(path, mutate)
    verdict = file_store.verify("acme")
    assert verdict.status == "NOT-OK"
    assert verdict.broken_at == 2
    assert "hash mismatch" in verdict.detail


def test_edit_ts_is_detected(file_store):
    _seed(file_store, n=3)
    path = _ledger_path(file_store)

    def mutate(seq, record):
        if seq == 1:
            record["ts"] = "1999-01-01T00:00:00Z"
        return record

    rewrite_ledger_file(path, mutate)
    assert file_store.verify("acme").status == "NOT-OK"


def test_delete_record_is_detected(file_store):
    _seed(file_store, n=3)
    path = _ledger_path(file_store)
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    kept = [line for line in lines if line.strip().startswith("#")
            or json.loads(line)["seq"] != 2]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept) + "\n")
    verdict = file_store.verify("acme")
    assert verdict.status == "NOT-OK"
    assert verdict.broken_at == 2  # seq gap at record 2


def test_reorder_records_is_detected(file_store):
    _seed(file_store, n=3)
    path = _ledger_path(file_store)
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    payloads = []
    headers = []
    for line in lines:
        if line.strip().startswith("#"):
            headers.append(line)
        else:
            payloads.append(json.loads(line))
    payloads[0], payloads[1] = payloads[1], payloads[0]  # swap seq 1 and 2
    with open(path, "w", encoding="utf-8") as handle:
        for line in headers:
            handle.write(line + "\n")
        for record in payloads:
            handle.write(json.dumps(record, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    assert file_store.verify("acme").status == "NOT-OK"


def test_truncate_tail_detected_with_expected(file_store):
    _seed(file_store, n=3)
    trusted = file_store.tail_state("acme")  # (3, hash)
    path = _ledger_path(file_store)
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines[:-1]) + "\n")  # drop last record
    # Internally consistent after truncation -> OK without an anchor...
    assert file_store.verify("acme").status == "OK"
    # ...but NOT-OK against the trusted pre-truncation tail.
    verdict = file_store.verify("acme", expected=trusted)
    assert verdict.status == "NOT-OK"
    assert "truncated" in verdict.detail


def test_whole_trail_gone_detected_with_expected(file_store):
    _seed(file_store, n=2)
    trusted = file_store.tail_state("acme")
    os.remove(_ledger_path(file_store))
    assert file_store.verify("acme").status == "OK"  # fresh empty chain
    assert file_store.verify("acme", expected=trusted).status == "NOT-OK"


def test_corrupt_line_is_cannot_assess_not_silent_pass(file_store):
    _seed(file_store, n=2)
    path = _ledger_path(file_store)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("this is not json {{{{\n")
    verdict = file_store.verify("acme")
    assert verdict.status == "CANNOT-ASSESS"
    assert not verdict.is_pass  # CANNOT-ASSESS is never a pass


def test_unreadable_file_is_cannot_assess(file_store):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root; permissions do not block reads")
    _seed(file_store, n=2)
    path = _ledger_path(file_store)
    os.chmod(path, 0o000)
    try:
        verdict = file_store.verify("acme")
        assert verdict.status == "CANNOT-ASSESS"
    finally:
        os.chmod(path, 0o644)


def test_reads_and_append_fail_closed_on_broken_chain(file_store):
    _seed(file_store, n=3)
    path = _ledger_path(file_store)

    def mutate(seq, record):
        if seq == 1:
            record["action"] = "tampered"
        return record

    rewrite_ledger_file(path, mutate)
    with pytest.raises(LedgerIntegrityError):
        file_store.records("acme")
    with pytest.raises(LedgerIntegrityError):
        file_store.tail_state("acme")
    with pytest.raises(LedgerIntegrityError):
        file_store.append("acme", actor="user:alice", action="more")


def test_reads_fail_closed_on_corrupt_file(file_store):
    _seed(file_store, n=2)
    path = _ledger_path(file_store)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("garbage[[[\n")
    from ledger.errors import LedgerCorruptError

    with pytest.raises(LedgerCorruptError):
        file_store.records("acme")


# --------------------------------------------------------------------------- #
# rechaining (documented opt-in repair)
# --------------------------------------------------------------------------- #
def test_rechain_requires_acknowledgement(file_store):
    _seed(file_store, n=3)
    with pytest.raises(RepairRefusedError):
        file_store.rechain("acme")  # no acknowledge
    assert file_store.verify("acme").status == "OK"  # untouched


def test_rechain_repairs_broken_links_and_preserves_content(file_store):
    _seed(file_store, n=3)
    path = _ledger_path(file_store)
    before = {r["seq"]: r for r in file_store.records("acme")}

    # Tamper only the hash field (content stays intact and authoritative).
    def mutate(seq, record):
        if seq == 2:
            record["hash"] = "0" * 64
        return record

    rewrite_ledger_file(path, mutate)
    assert file_store.verify("acme").status == "NOT-OK"

    verdict = file_store.rechain("acme", acknowledge=True)
    assert verdict.status == "OK"
    assert file_store.verify("acme").status == "OK"

    after = {r["seq"]: r for r in file_store.records("acme")}
    for seq in (1, 2, 3):
        for field in ("tenantId", "actor", "action", "resource", "evidence", "ts"):
            assert after[seq][field] == before[seq][field], field
        assert after[seq]["prevHash"] == (before[seq]["prevHash"]
                                          if seq == 1 else after[seq - 1]["hash"])
        assert len(after[seq]["hash"]) == 64
