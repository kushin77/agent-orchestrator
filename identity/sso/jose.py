"""Minimal offline JOSE: base64url, HS256/RS256 JWT, JWKS, RFC 7638.

The fleet-standardized stack allows ``cryptography`` when importable, so all
RSA/RS256/cert paths use it and degrade to a typed error when it is absent
(HS256 needs only the stdlib ``hmac``). This module implements just enough
JOSE for the offline SSO model (issue #35): signing/verification of HS256
session tokens (registry issue #10 shape) and RS256 console session tokens
with kid-indexed JWKS verification (shared-frontend ``os-session-token``).

Everything is deterministic and offline: no network, no JWKS fetch - keys are
injected (fixtures / keystore) and ``now`` is passed in for testability.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Optional, Union

from .errors import InvalidSessionTokenError, SignatureVerificationError, SsoError
from .model import ALG_HS256, ALG_RS256

try:  # pragma: no cover - exercised when cryptography is importable
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.asymmetric.rsa import (
        RSAPrivateKey,
        RSAPublicKey,
    )
    from cryptography.x509 import (
        Certificate,
        load_der_x509_certificate,
        load_pem_x509_certificate,
    )

    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - environment without cryptography
    _CRYPTO_AVAILABLE = False

    class _RSAKey:  # type: ignore[no-redef]
        pass

    RSAPrivateKey = _RSAKey  # type: ignore[misc,assignment]
    RSAPublicKey = _RSAKey  # type: ignore[misc,assignment]
    Certificate = _RSAKey  # type: ignore[misc,assignment]

_JSON_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}

KeyLike = Union[bytes, str]


def _require_crypto() -> None:
    if not _CRYPTO_AVAILABLE:
        raise SsoError(
            "cryptography is required for this operation and is not importable"
        )


# --- base64url ---------------------------------------------------------------


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:  # noqa: BLE001 - any decode failure is invalid
        raise InvalidSessionTokenError("malformed base64url segment") from exc


# --- PEM loading --------------------------------------------------------------


def load_private_key(pem: bytes):
    """Load a PKCS#8/PKCS#1 RSA private key PEM (cryptography)."""
    _require_crypto()
    return serialization.load_pem_private_key(pem, password=None)


def load_public_key(pem: bytes):
    """Load an SPKI RSA public key PEM (cryptography)."""
    _require_crypto()
    return serialization.load_pem_public_key(pem)


def load_x509_certificate(pem: bytes):
    """Load an X.509 certificate PEM (cryptography)."""
    _require_crypto()
    return load_pem_x509_certificate(pem)


def serialize_public_key_spki(key) -> bytes:
    _require_crypto()
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# --- signatures ---------------------------------------------------------------


def hs256_sign(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def hs256_verify(key: bytes, data: bytes, signature: bytes) -> None:
    expected = hs256_sign(key, data)
    if not hmac.compare_digest(expected, signature):
        raise SignatureVerificationError("HS256 signature does not verify")


def rsa_sha256_sign(private_key, data: bytes) -> bytes:
    """RS256 = RSASSA-PKCS1-v1_5 with SHA-256 (cryptography)."""
    _require_crypto()
    return private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def rsa_sha256_verify(public_key, data: bytes, signature: bytes) -> None:
    """Verify an RS256 signature; raises SignatureVerificationError on failure."""
    _require_crypto()
    try:
        public_key.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise SignatureVerificationError("RS256 signature does not verify") from exc


def rsa_public_key_matches(left, right) -> bool:
    """True when two RSA public keys are the same (numbers equal)."""
    if type(left).__name__ != type(right).__name__:
        return False
    ln = left.public_numbers()
    rn = right.public_numbers()
    return ln.n == rn.n and ln.e == rn.e


def cert_public_key(cert):
    """Extract the RSA public key from an X.509 certificate."""
    _require_crypto()
    return cert.public_key()


def cert_der(cert) -> bytes:
    _require_crypto()
    return cert.public_bytes(serialization.Encoding.DER)


def public_key_der(public_key) -> bytes:
    """DER SubjectPublicKeyInfo for a public key (used by SAML cert compare)."""
    _require_crypto()
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# --- JWT ----------------------------------------------------------------------


def _b64_claims(claims: dict[str, Any]) -> str:
    return b64url_encode(json.dumps(claims, **_JSON_KW).encode("utf-8"))


def _parse_segments(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidSessionTokenError("malformed token (expected 3 segments)")
    header_part, payload_part, sig_part = parts
    try:
        header = json.loads(b64url_decode(header_part))
        claims = json.loads(b64url_decode(payload_part))
    except (ValueError, TypeError) as exc:
        raise InvalidSessionTokenError("token segments are not valid JSON") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise InvalidSessionTokenError("token header/payload must be JSON objects")
    return header, claims, b64url_decode(sig_part)


def jwt_encode(
    claims: dict[str, Any],
    *,
    alg: str,
    key: KeyLike,
    kid: Optional[str] = None,
) -> str:
    """Encode a compact JWT signed with HS256 (key=bytes) or RS256 (key=RSA)."""
    header: dict[str, Any] = {"alg": alg, "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    header_part = _b64_claims(header)
    payload_part = _b64_claims(claims)
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    if alg == ALG_HS256:
        if not isinstance(key, bytes):
            raise SsoError("HS256 requires a bytes key")
        signature = hs256_sign(key, signing_input)
    elif alg == ALG_RS256:
        signature = rsa_sha256_sign(key, signing_input)
    else:
        raise SsoError(f"unsupported alg: {alg}")
    return f"{header_part}.{payload_part}.{b64url_encode(signature)}"


def jwt_verify_signature(
    token: str,
    *,
    alg: str,
    key: KeyLike,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a JWT's signature and return (header, claims).

    The ``alg`` is pinned by the caller (no algorithm confusion): a token
    whose header alg differs from the expected one, or that uses ``none``, is
    rejected before any signature work.
    """
    header, claims, sig = _parse_segments(token)
    if header.get("alg") != alg:
        raise InvalidSessionTokenError(
            f"algorithm mismatch (expected {alg}, got {header.get('alg')!r})"
        )
    header_part, payload_part, _ = token.split(".")
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    if alg == ALG_HS256:
        if not isinstance(key, bytes):
            raise SsoError("HS256 verification requires a bytes key")
        hs256_verify(key, signing_input, sig)
    elif alg == ALG_RS256:
        rsa_sha256_verify(key, signing_input, sig)
    else:
        raise SsoError(f"unsupported alg: {alg}")
    return header, claims


def jwt_unsign(token: str, *, alg: str, key: KeyLike) -> dict[str, Any]:
    """Verify a JWT signature and return its claims (no exp checks here)."""
    _, claims = jwt_verify_signature(token, alg=alg, key=key)
    return claims


# --- JWKS / RFC 7638 ----------------------------------------------------------


def rsa_public_jwk(public_key) -> dict[str, str]:
    """The RSA public JWK ``{kty,n,e}`` for a public key."""
    _require_crypto()
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "n": b64url_encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": b64url_encode(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def public_key_from_jwk(jwk: dict[str, str]):
    """Rebuild an RSA public key from its public JWK ``{n,e}`` (offline)."""
    _require_crypto()
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    n = int.from_bytes(b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(b64url_decode(jwk["e"]), "big")
    return RSAPublicNumbers(e, n).public_key()


def rfc7638_thumbprint(public_key) -> str:
    """RFC 7638 SHA-256 thumbprint of a public key (used as the JWKS kid)."""
    _require_crypto()
    canonical = json.dumps(
        {"e": rsa_public_jwk(public_key)["e"], "kty": "RSA", "n": rsa_public_jwk(public_key)["n"]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return b64url_encode(hashlib.sha256(canonical.encode("utf-8")).digest())


def jwks_for_keys(keys: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a ``{"keys": [...]}`` JWKS for ``[(kid, RSA public key), ...]``.

    The ``kid`` is the key's RFC 7638 thumbprint when one is not supplied, so
    a consumer holding a cached JWKS keeps working across a key rollover that
    only *widens* the trusted set (shared-frontend issue #131 semantics).
    """
    entries = []
    for kid, key in keys:
        jwk = rsa_public_jwk(key)
        jwk.update({"alg": ALG_RS256, "use": "sig", "kid": kid})
        entries.append(jwk)
    return {"keys": entries}


# --- RSA keygen (offline fixtures only - never persisted to the repo) ----------


def generate_rsa_keypair(key_size: int = 2048):
    """Generate an (RSAPrivateKey, RSAPublicKey) pair via cryptography.

    Used only to produce in-memory test fixtures / boot-time signing keys;
    private keys are never written to the repo (the secrets gate forbids it).
    """
    _require_crypto()
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=key_size
    )
    return private_key, private_key.public_key()
