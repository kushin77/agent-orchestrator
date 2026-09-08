#!/usr/bin/env python3
"""Attestation for the agent-pack registry (issue #40).

Signs and verifies AgentPack documents with RSASSA-PSS / SHA-256 (PS256).
A publisher signs the pack's canonical JSON (every field EXCEPT
``attestation.signature``); consumers verify it at install time with the
publisher's public key (consumer trust). ``validate.py`` verifies the shipped
release snapshots against the committed public key (``publisher-key.pem``).

Fail-closed doctrine: if ``cryptography`` is not importable every signing and
verification call RAISES ``PackAttestationError`` — a pack is never trusted
just because the verifier library went missing (no-false-green). The signing
key is never committed (GR-6); only the public key lives in the repo.

Canonicalization: keys are recursively sorted and the document is serialized
as compact JSON (``sort_keys``, no spaces), so signing is deterministic across
YAML/JSON authoring and across Python runs.

Usage (importable; the pytest suite drives it directly):

    import sys; sys.path.insert(0, "registry")
    from packs import attestation as at

    private_pem, public_pem = at.generate_keypair()
    signed = at.sign_pack(pack_dict, private_pem, kid="ao-pack-publisher-v1")
    ok = at.verify_pack(signed, public_pem)          # True
    forged = dict(signed); forged["version"] = "9.9.9"
    ok = at.verify_pack(forged, public_pem)          # False
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from datetime import datetime, timezone

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    HAS_CRYPTO = True
except ImportError:  # pragma: no cover - exercised by fail-closed tests
    HAS_CRYPTO = False

ALG = "PS256"
_SHA256 = hashes.SHA256()
_PSS = padding.PSS(
    mgf=padding.MGF1(hashes.SHA256()),
    salt_length=padding.PSS.DIGEST_LENGTH,
)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PackAttestationError(Exception):
    """Attestation could not be produced or verified (fails closed)."""


def now_utc():
    """RFC 3339 UTC timestamp, second precision (e.g. 2026-09-08T12:00:00Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_bytes(obj):
    """Deterministic UTF-8 canonical JSON: keys recursively sorted, compact."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def signed_payload(pack):
    """Deep copy of the pack with ``attestation.signature`` removed.

    The signature is computed over every field EXCEPT the signature value
    itself, so tampering with any other field (including kid/alg/signedAt)
    breaks verification.
    """
    payload = copy.deepcopy(pack)
    att = payload.get("attestation")
    if isinstance(att, dict):
        att.pop("signature", None)
    return payload


def generate_keypair(key_size=2048):
    """Return (private_key_pem, public_key_pem) as bytes.

    The private key is for the publisher only and must never be committed
    (GR-6); the public key is what ships in ``publisher-key.pem``.
    """
    if not HAS_CRYPTO:  # pragma: no cover - fail closed
        raise PackAttestationError(
            "cryptography is not importable; cannot generate a keypair "
            "(fail closed)"
        )
    private_key = rsa.generate_private_key(public_exponent=65537,
                                           key_size=key_size)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _load_private_key(private_key_pem):
    if not HAS_CRYPTO:  # pragma: no cover - fail closed
        raise PackAttestationError(
            "cryptography is not importable; cannot sign (fail closed)"
        )
    if isinstance(private_key_pem, str):
        private_key_pem = private_key_pem.encode("utf-8")
    try:
        return serialization.load_pem_private_key(private_key_pem, password=None)
    except Exception as exc:  # pragma: no cover - defensive
        raise PackAttestationError("invalid private key: %s" % exc)


def _load_public_key(public_key_pem):
    if not HAS_CRYPTO:
        raise PackAttestationError(
            "cryptography is not importable; cannot verify (fail closed)"
        )
    if isinstance(public_key_pem, str):
        public_key_pem = public_key_pem.encode("utf-8")
    try:
        return serialization.load_pem_public_key(public_key_pem)
    except Exception as exc:  # pragma: no cover - defensive
        raise PackAttestationError("invalid public key: %s" % exc)


def sign_pack(pack, private_key_pem, kid="ao-pack-publisher-v1"):
    """Return ``pack`` with a signed ``attestation`` block inserted.

    Any existing ``attestation`` on the pack is replaced. The signature is
    over the canonical JSON of the pack excluding the signature value.
    """
    private_key = _load_private_key(private_key_pem)
    signed_at = pack.get("attestation", {}).get("signedAt") if isinstance(
        pack.get("attestation"), dict) else None
    if not signed_at or not TS_RE.match(signed_at):
        signed_at = now_utc()
    payload = signed_payload(pack)
    payload["attestation"] = {"kid": kid, "alg": ALG, "signedAt": signed_at}
    digest = canonical_bytes(payload)
    try:
        signature = private_key.sign(digest, _PSS, _SHA256)
    except Exception as exc:  # pragma: no cover - defensive
        raise PackAttestationError("signing failed: %s" % exc)
    out = copy.deepcopy(pack)
    out["attestation"] = {
        "kid": kid,
        "alg": ALG,
        "signedAt": signed_at,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return out


def verify_pack(pack, public_key_pem):
    """Verify the pack's attestation signature against a public key.

    Returns True when the signature verifies. Returns False when the
    attestation block is missing/malformed, the algorithm is not PS256, or the
    signature does not verify. RAISES ``PackAttestationError`` when the
    cryptography library is unavailable (fails closed — never trust because a
    verifier went missing).
    """
    public_key = _load_public_key(public_key_pem)
    att = pack.get("attestation")
    if not isinstance(att, dict):
        return False
    if att.get("alg") != ALG:
        return False
    signature_b64 = att.get("signature")
    if not isinstance(signature_b64, str) or not signature_b64:
        return False
    try:
        signature = base64.b64decode(signature_b64)
    except Exception:
        return False
    digest = canonical_bytes(signed_payload(pack))
    try:
        public_key.verify(signature, digest, _PSS, _SHA256)
        return True
    except InvalidSignature:
        return False
    except Exception:  # pragma: no cover - defensive (e.g. wrong key type)
        return False
