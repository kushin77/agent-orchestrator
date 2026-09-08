"""Encryption-at-rest tests (issue #31).

Positive: payloads are stored as AES-256-GCM envelopes and round-trip. Negative:
the plaintext is NEVER in the store (file bytes, record JSON, canonical hash
bytes), a missing key or cipher FAILS CLOSED (append refused, nothing written),
and a wrong key or tampered ciphertext cannot decrypt.
"""

from __future__ import annotations

import base64
import os

import pytest

from ledger import (
    CipherUnavailableError,
    DecryptionError,
    DictKeystore,
    KeyMaterial,
    KeyUnavailableError,
    LedgerCryptoError,
    open_ledger,
)
from ledger import crypto
from ledger.crypto import decrypt_payload, encrypt_payload
from conftest import make_key

MARKER = "s3cr3t-TOKEN-plaintext-must-not-persist"


def _payload():
    return {"prompt": "hello", "secret": MARKER, "nested": {"k": "v"}}


def _ledger_path(store, tenant="acme"):
    return os.path.join(store.directory, tenant + ".jsonl")


def test_envelope_shape(file_store):
    record = file_store.append("acme", actor="user:alice", action="a",
                               payload=_payload())
    env = record["payloadEnc"]
    assert env["alg"] == "AES-256-GCM"
    assert env["v"] == 1
    assert env["keyId"] == "test:acme:v1"
    assert base64.b64decode(env["nonce"])
    assert base64.b64decode(env["ct"])
    assert env != {}  # a real envelope, not a stub


def test_no_payload_means_null_envelope(file_store):
    record = file_store.append("acme", actor="user:alice", action="a")
    assert record["payloadEnc"] is None


def test_plaintext_never_in_stored_bytes(file_store):
    _seed_with_payload(file_store, n=2)
    path = _ledger_path(file_store)
    on_disk = open(path, "r", encoding="utf-8").read()
    assert MARKER not in on_disk
    # Also absent from every stored record's canonical form and JSON.
    for record in file_store.records("acme"):
        assert MARKER not in json_dumps(record)
        assert MARKER not in str(record)


def test_ciphertext_bytes_differ_between_records(file_store):
    a = file_store.append("acme", actor="user:alice", action="a", payload={"v": 1})
    b = file_store.append("acme", actor="user:alice", action="b", payload={"v": 2})
    # Distinct payloads + fresh nonces => distinct ciphertexts (no ECB-style
    # equality between identical-prefix plaintexts).
    assert a["payloadEnc"]["ct"] != b["payloadEnc"]["ct"]


def test_decrypt_round_trip_returns_original(file_store):
    file_store.append("acme", actor="agent:worker-1", action="model.call",
                      resource="gateway/proxy", payload=_payload())
    from ledger.verify import read_payload

    status, payload, _ = read_payload(file_store, "acme", 1)
    assert status == "OK"
    assert payload == _payload()


def test_wrong_key_cannot_decrypt(file_store):
    file_store.append("acme", actor="user:alice", action="a", payload=_payload())
    from ledger.verify import read_payload

    env = file_store.records("acme")[0]["payloadEnc"]
    with pytest.raises(DecryptionError):
        decrypt_payload(env, make_key("attacker"))
    wrong_store = open_ledger(
        file_store.directory,
        keystore=DictKeystore({"acme": KeyMaterial(key=make_key("attacker"),
                                                   key_id="evil")}),
    )
    status, _, detail = read_payload(wrong_store, "acme", 1)
    assert status == "CANNOT-ASSESS"
    assert "decrypt" in detail


def test_tampered_ciphertext_cannot_decrypt(file_store):
    file_store.append("acme", actor="user:alice", action="a", payload=_payload())
    env = dict(file_store.records("acme")[0]["payloadEnc"])
    raw = bytearray(base64.b64decode(env["ct"]))
    raw[0] ^= 0xFF  # flip a ciphertext byte
    env["ct"] = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(DecryptionError):
        decrypt_payload(env, make_key("acme"))


def test_cross_tenant_key_cannot_decrypt(keystore, file_store):
    file_store.append("acme", actor="user:alice", action="a", payload=_payload())
    from ledger.verify import read_payload

    # 'other' tenant's key is present in the shared keystore but wrong for acme.
    other_store = open_ledger(
        file_store.directory,
        keystore=DictKeystore({"acme": KeyMaterial(key=make_key("other"),
                                                   key_id="other")}),
    )
    status, _, _ = read_payload(other_store, "acme", 1)
    assert status == "CANNOT-ASSESS"


def test_append_payload_fails_closed_without_keystore(tmp_path):
    store = open_ledger(str(tmp_path / "audit"))  # no keystore at all
    with pytest.raises(KeyUnavailableError):
        store.append("acme", actor="user:alice", action="a", payload=_payload())
    # Nothing was written - fail closed.
    assert store.tenant_ids() == []
    assert store.verify("acme").status == "OK"


def test_append_payload_fails_closed_without_tenant_key(keystore, file_store):
    # keystore has acme/other; a tenant without a key must be refused.
    with pytest.raises(KeyUnavailableError):
        file_store.append("no-key-tenant", actor="user:alice", action="a",
                          payload=_payload())
    assert "no-key-tenant" not in file_store.tenant_ids()


def test_append_payload_fails_closed_without_cipher(file_store, monkeypatch):
    monkeypatch.setattr(crypto, "_CIPHER_AVAILABLE", False)
    with pytest.raises(CipherUnavailableError):
        file_store.append("acme", actor="user:alice", action="a", payload=_payload())
    assert file_store.tenant_ids() == []
    # Non-payload appends still work without the cipher (nothing to encrypt).
    monkeypatch.setattr(crypto, "_CIPHER_AVAILABLE", True)
    record = file_store.append("acme", actor="user:alice", action="no-payload")
    assert record["payloadEnc"] is None


def test_encrypt_payload_rejects_weak_key():
    with pytest.raises(LedgerCryptoError):
        encrypt_payload(b"x", b"short", "k")  # not 32 bytes -> LedgerCryptoError


def test_env_keystore_resolution(monkeypatch):
    from ledger.keystore import EnvKeystore, KeyUnavailableError

    monkeypatch.setenv("AO_AUDIT_LEDGER_KEY_ACME", make_key("acme").hex())
    monkeypatch.setenv("AO_AUDIT_LEDGER_KEY_ID_ACME", "env:acme:rotated")
    material = EnvKeystore().get("acme")
    assert material.key == make_key("acme")
    assert material.key_id == "env:acme:rotated"
    with pytest.raises(KeyUnavailableError):
        EnvKeystore().get("unset-tenant")


def _seed_with_payload(store, n=2, tenant="acme"):
    for i in range(1, n + 1):
        store.append(tenant, actor="user:alice", action=f"a{i}",
                     payload={"i": i, "secret": MARKER})


def json_dumps(record):
    import json

    return json.dumps(record, sort_keys=True)
