"""Verify API tri-state tests (issue #31).

The ledger reports an honest OK / NOT-OK / CANNOT-ASSESS verdict; a tamper is
NOT-OK and never a silent pass; an unavailable key is CANNOT-ASSESS; and exit
codes follow the issue #28 wire contract (OK=0, NOT-OK=1, CANNOT-ASSESS=2).
"""

from __future__ import annotations

import os

from conftest import rewrite_ledger_file
from ledger import LedgerVerdict, verify_all


def _seed(store, tenant="acme", n=3):
    for i in range(1, n + 1):
        store.append(tenant, actor="user:alice", action=f"a{i}",
                     payload={"i": i} if i == 1 else None)


def _ledger_path(store, tenant="acme"):
    return os.path.join(store.directory, tenant + ".jsonl")


def test_ok_verdict_exit_code(file_store):
    _seed(file_store, n=3)
    verdict = file_store.verify("acme")
    assert verdict.status == "OK"
    assert verdict.exit_code == 0
    assert verdict.as_dict()["status"] == "OK"
    assert verdict.as_dict()["seq"] == 3


def test_not_ok_verdict_exit_code_and_broken_at(file_store):
    _seed(file_store, n=3)
    path = _ledger_path(file_store)

    def mutate(seq, record):
        if seq == 2:
            record["resource"] = "altered"
        return record

    rewrite_ledger_file(path, mutate)
    verdict = file_store.verify("acme")
    assert verdict.status == "NOT-OK"
    assert verdict.exit_code == 1
    assert verdict.broken_at == 2
    assert verdict.is_fail and not verdict.is_pass and not verdict.is_unknown


def test_cannot_assess_exit_code_and_never_pass(file_store):
    _seed(file_store, n=2)
    path = _ledger_path(file_store)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    verdict = file_store.verify("acme")
    assert verdict.status == "CANNOT-ASSESS"
    assert verdict.exit_code == 2
    assert not verdict.is_pass  # CANNOT-ASSESS is never a pass
    assert verdict.is_unknown


def test_verify_all_covers_every_tenant(file_store):
    _seed(file_store, tenant="acme", n=2)
    _seed(file_store, tenant="other", n=1)
    verdicts = verify_all(file_store)
    assert set(verdicts) == {"acme", "other"}
    assert all(v.status == "OK" for v in verdicts.values())


def test_expected_tail_mismatch_is_not_ok(file_store):
    _seed(file_store, n=3)
    verdict = file_store.verify("acme", expected=(3, "0" * 64))
    assert verdict.status == "NOT-OK"
    assert verdict.is_fail


def test_missing_key_payload_read_is_cannot_assess(tmp_path):
    # A store with a payload but no key for the tenant when reading.
    from ledger import DictKeystore, KeyMaterial, open_ledger
    from conftest import make_key

    keyed = open_ledger(
        str(tmp_path / "audit"),
        keystore=DictKeystore({"acme": KeyMaterial(key=make_key("acme"),
                                                   key_id="k")}),
    )
    keyed.append("acme", actor="user:alice", action="a", payload={"secret": 1})

    keyless = open_ledger(str(tmp_path / "audit"))  # reopened with no keystore
    from ledger.verify import read_payload

    status, payload, detail = read_payload(keyless, "acme", 1)
    assert status == "CANNOT-ASSESS"
    assert payload is None
    assert "keystore" in detail
    # Chain integrity itself does not need the key: verify is still OK.
    assert keyless.verify("acme").status == "OK"


def test_payload_read_out_of_range_is_not_ok(file_store):
    _seed(file_store, n=2)
    from ledger.verify import read_payload

    status, _, _ = read_payload(file_store, "acme", 99)
    assert status == "NOT-OK"


def test_verdict_exit_code_mapping():
    from ledger.verify import verdict_exit_code

    assert verdict_exit_code(LedgerVerdict.ok("t", (0, "0" * 64))) == 0
    assert verdict_exit_code(
        LedgerVerdict.not_ok("t", (0, "0" * 64), "x")
    ) == 1
    assert verdict_exit_code(
        LedgerVerdict.cannot_assess("t", "x")
    ) == 2
