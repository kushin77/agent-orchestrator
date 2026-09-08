"""Attestation tests for the agent-pack registry (issue #40).

Covers the consumer-trust primitive: signed attestation per pack, signature
verification, tamper detection, and fail-closed behavior when the cryptography
library is unavailable.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import packhelpers  # noqa: E402

from packs import attestation  # noqa: E402
from packs.attestation import PackAttestationError  # noqa: E402


def test_sign_and_verify_roundtrip():
    private_pem, public_pem = packhelpers.keypair()
    doc = packhelpers.signed_doc(private_pem)
    assert doc["attestation"]["alg"] == "PS256"
    assert doc["attestation"]["signature"]
    assert attestation.verify_pack(doc, public_pem) is True


def test_verify_rejects_tampered_pack():
    private_pem, public_pem = packhelpers.keypair()
    doc = packhelpers.signed_doc(private_pem)
    tampered = dict(doc)
    tampered["version"] = "9.9.9"
    assert attestation.verify_pack(tampered, public_pem) is False


def test_verify_rejects_tampered_attestation_header():
    private_pem, public_pem = packhelpers.keypair()
    doc = packhelpers.signed_doc(private_pem)
    tampered = dict(doc)
    att = dict(doc["attestation"])
    att["alg"] = "RS256"
    tampered["attestation"] = att
    assert attestation.verify_pack(tampered, public_pem) is False


def test_verify_missing_signature_is_false():
    _, public_pem = packhelpers.keypair()
    doc = packhelpers.pack_doc(with_attestation=False)
    assert attestation.verify_pack(doc, public_pem) is False


def test_verify_bad_signature_is_false():
    private_pem, public_pem = packhelpers.keypair()
    doc = packhelpers.signed_doc(private_pem)
    att = dict(doc["attestation"])
    att["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAA="
    doc["attestation"] = att
    assert attestation.verify_pack(doc, public_pem) is False


def test_verify_with_wrong_key_is_false():
    private_pem, _ = packhelpers.keypair()
    _, other_public = packhelpers.keypair()
    doc = packhelpers.signed_doc(private_pem)
    assert attestation.verify_pack(doc, other_public) is False


def test_verify_fails_closed_without_cryptography(monkeypatch):
    _, public_pem = packhelpers.keypair()
    doc = packhelpers.signed_doc(packhelpers.keypair()[0])
    monkeypatch.setattr(attestation, "HAS_CRYPTO", False)
    try:
        attestation.verify_pack(doc, public_pem)
        raise AssertionError("verify must fail closed without cryptography")
    except PackAttestationError as exc:
        assert "cryptography" in str(exc)


def test_sign_fails_closed_without_cryptography(monkeypatch):
    doc = packhelpers.pack_doc()
    monkeypatch.setattr(attestation, "HAS_CRYPTO", False)
    try:
        attestation.sign_pack(doc, b"not-a-key")
        raise AssertionError("sign must fail closed without cryptography")
    except PackAttestationError as exc:
        assert "cryptography" in str(exc)


def test_canonical_bytes_deterministic():
    a = {"b": [1, 2], "a": {"z": "y", "x": 1}}
    b = {"a": {"x": 1, "z": "y"}, "b": [1, 2]}
    assert attestation.canonical_bytes(a) == attestation.canonical_bytes(b)


def test_signed_payload_excludes_signature_only():
    doc = packhelpers.pack_doc()
    payload = attestation.signed_payload(doc)
    assert "signature" not in payload["attestation"]
    # every OTHER field survives
    assert payload["id"] == doc["id"]
