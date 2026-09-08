"""Encryption at rest for audit payloads (telemetry/ledger, issue #31).

Sensitive payloads are encrypted with AES-256-GCM before they enter the hash
chain; the clear payload is never stored. Each ciphertext is wrapped in a
self-describing envelope so a consumer can see, without the key, which
algorithm and key-id produced it:

.. code-block:: json

    {"v": 1, "alg": "AES-256-GCM", "keyId": "acme:k1",
     "nonce": "<base64>", "ct": "<base64 ciphertext||tag>"}

Design (adapted from the capital-underwriting ``secureAuditLog.ts`` envelope
pattern, see ``docs/CANNIBALIZATION.md``): versioned, algorithm-tagged
envelopes survive a future key rotation without destructive rewriting - a
record whose ``alg``/``keyId`` no longer matches the current key is still
readable with the key that produced it.

FAIL-CLOSED SEAM. The cipher library (``cryptography``) is imported lazily. If
it is not importable in a given environment, encryption and decryption raise
:class:`CipherUnavailableError`: the ledger never falls back to storing a
plaintext payload, so a missing cipher is a loud refusal, never a silent
downgrade. Chain hashing itself is pure stdlib ``hashlib`` and needs no
third-party import.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Dict

from .errors import (
    CipherUnavailableError,
    DecryptionError,
    LedgerCryptoError,
    LedgerValidationError,
)

# The cipher library is optional at import time: everything else in the ledger
# works without it, and only encryption/decryption fail closed when absent.
try:  # pragma: no cover - exercised by the monkeypatched fail-closed tests
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

    _AESGCM = AESGCM
    _CIPHER_AVAILABLE = True
except Exception:  # pragma: no cover - depends on environment
    _AESGCM = None  # type: ignore[assignment]
    _CIPHER_AVAILABLE = False

ENVELOPE_VERSION = 1
ENVELOPE_ALG = "AES-256-GCM"

# AES-256 requires a 32-byte key.
_KEY_BYTES = 32
_NONCE_BYTES = 12


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception as exc:  # (binascii.Error / ValueError)
        raise DecryptionError("envelope base64 field is malformed") from exc


def cipher_available() -> bool:
    """True when the AES-256-GCM cipher library is importable."""
    return _CIPHER_AVAILABLE


def normalize_key(key: Any) -> bytes:
    """Coerce a key to 32 raw bytes, accepting bytes or 64-char hex.

    Raises :class:`LedgerCryptoError` when the key is not exactly 32 bytes
    (AES-256), so a misconfigured key fails closed instead of silently using
    the wrong strength.
    """
    if isinstance(key, str):
        try:
            key = bytes.fromhex(key.strip())
        except ValueError as exc:
            raise LedgerCryptoError("hex key is malformed") from exc
    if not isinstance(key, (bytes, bytearray)):
        raise LedgerCryptoError("key must be bytes or a hex string")
    key = bytes(key)
    if len(key) != _KEY_BYTES:
        raise LedgerCryptoError(
            f"key must be {_KEY_BYTES} bytes (AES-256), got {len(key)}"
        )
    return key


def _require_cipher() -> None:
    if not _CIPHER_AVAILABLE:
        raise CipherUnavailableError(
            "AES-256-GCM cipher library (cryptography) is not importable; "
            "refusing to store plaintext payloads (fail closed)"
        )


def _check_envelope(envelope: Dict[str, Any]) -> None:
    if not isinstance(envelope, dict):
        raise DecryptionError("payload envelope must be an object")
    if envelope.get("v") != ENVELOPE_VERSION:
        raise DecryptionError(
            f"unsupported envelope version {envelope.get('v')!r} "
            f"(expected {ENVELOPE_VERSION})"
        )
    if envelope.get("alg") != ENVELOPE_ALG:
        raise DecryptionError(
            f"unsupported envelope algorithm {envelope.get('alg')!r} "
            f"(expected {ENVELOPE_ALG}); refusing to guess"
        )
    for field in ("nonce", "ct"):
        if not isinstance(envelope.get(field), str) or not envelope[field]:
            raise DecryptionError(f"envelope field {field!r} is missing or empty")


def encrypt_payload(plaintext: bytes, key: Any, key_id: str) -> Dict[str, Any]:
    """Encrypt ``plaintext`` into a versioned AES-256-GCM envelope.

    Raises :class:`CipherUnavailableError` when the cipher is unavailable and
    :class:`LedgerCryptoError` when the key is not a valid AES-256 key.
    """
    _require_cipher()
    key = normalize_key(key)
    if not isinstance(key_id, str) or not key_id:
        raise LedgerValidationError("keyId must be a non-empty string")
    if not isinstance(plaintext, (bytes, bytearray)):
        raise LedgerCryptoError("plaintext must be bytes")
    nonce = os.urandom(_NONCE_BYTES)
    # AESGCM.encrypt returns ciphertext||tag in one blob.
    sealed = _AESGCM(key).encrypt(nonce, bytes(plaintext), None)
    return {
        "v": ENVELOPE_VERSION,
        "alg": ENVELOPE_ALG,
        "keyId": key_id,
        "nonce": _b64encode(nonce),
        "ct": _b64encode(sealed),
    }


def decrypt_payload(envelope: Dict[str, Any], key: Any) -> bytes:
    """Decrypt an envelope produced by :func:`encrypt_payload`.

    Raises :class:`DecryptionError` on any failure (wrong key, tampered
    ciphertext, unknown algorithm/version) and :class:`CipherUnavailableError`
    when the cipher is unavailable.
    """
    _require_cipher()
    key = normalize_key(key)
    _check_envelope(envelope)
    nonce = _b64decode(envelope["nonce"])
    sealed = _b64decode(envelope["ct"])
    try:
        return _AESGCM(key).decrypt(nonce, sealed, None)
    except CipherUnavailableError:  # pragma: no cover - defensive
        raise
    except Exception as exc:
        raise DecryptionError(
            "payload decryption failed (wrong key or tampered ciphertext)"
        ) from exc
