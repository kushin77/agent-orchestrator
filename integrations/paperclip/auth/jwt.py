"""A minimal, stdlib-only HS256 JWT mint/verify for the paperclip boundary.

The upstream surface accepts an agent key or JWT and a board token
(``Authorization: Bearer``). Adopting it across a process boundary (ADR-0013)
means the fleet mints those credentials **from its own records** and verifies
them on the way back in. This module is the crypto primitive only: it holds no
key and knows no identity — the key is injected on every call and is never read
from, or written to, the repository (GR-6).

Deliberately small and closed: ``HS256`` is the only algorithm accepted, the
``alg`` header cannot be downgraded (an ``alg: none`` token is refused), and the
signature comparison is constant-time. Anything the verifier cannot fully check
is refused rather than waved through (fail closed).

---knowledge---
module_id: integrations.paperclip.auth.jwt
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [sign, verify, peek_claims]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from .model import invalid_token, token_expired

#: The only signature algorithm this boundary speaks.
ALGORITHM = "HS256"

#: Tolerance (seconds) applied to ``exp`` / ``nbf`` / ``iat`` comparisons.
DEFAULT_LEEWAY = 0


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(segment: str) -> bytes:
    if not isinstance(segment, str) or segment == "":
        raise invalid_token("the token is malformed")
    pad = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + pad)
    except (binascii.Error, ValueError) as exc:
        raise invalid_token("the token is not valid base64url") from exc


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _json_load(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise invalid_token("the token payload is not valid JSON") from exc


def sign(claims: Dict[str, Any], secret: str) -> str:
    """Mint a compact HS256 JWT over ``claims``.

    The caller supplies every claim (including ``exp``); this function adds
    nothing, so what a token asserts is exactly what the minting lane decided.
    """
    if not secret:
        raise ValueError("a signing key is required")
    header = {"alg": ALGORITHM, "typ": "JWT"}
    segments = [_b64u_encode(_json_bytes(header)), _b64u_encode(_json_bytes(claims))]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return ".".join(segments + [_b64u_encode(signature)])


def _signature_ok(token: str, secret: str) -> None:
    header_seg, payload_seg, sig_seg = token.split(".")
    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64u_decode(sig_seg)):
        raise invalid_token("the token signature does not verify")


def _check_algorithm(header: Any) -> None:
    if not isinstance(header, dict):
        raise invalid_token("the token header is malformed")
    if header.get("alg") != ALGORITHM:
        # an `alg: none` token, or any unsupported algorithm, is refused here
        raise invalid_token("the token algorithm is not supported")


def _int_claim(claims: Dict[str, Any], name: str) -> Optional[int]:
    value = claims.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise invalid_token(f"the token {name!r} claim is not an integer")
    return value


def _check_time(claims: Dict[str, Any], now: int, leeway: int) -> None:
    exp = _int_claim(claims, "exp")
    if exp is None:
        raise invalid_token("the token has no expiry claim")
    if now > exp + leeway:
        raise token_expired()
    nbf = _int_claim(claims, "nbf")
    if nbf is not None and now < nbf - leeway:
        raise invalid_token("the token is not yet valid")
    iat = _int_claim(claims, "iat")
    if iat is not None and now < iat - leeway:
        raise invalid_token("the token was issued in the future")


def verify(
    token: str,
    *,
    secret: str,
    now: int,
    audience: str,
    issuer: str,
    leeway: int = DEFAULT_LEEWAY,
) -> Dict[str, Any]:
    """Verify a token fully, or raise :class:`AuthError` naming the rule.

    Checks, in order: shape, algorithm, signature, JSON payload, ``iss``,
    ``aud``, ``exp`` / ``nbf`` / ``iat``, and a present ``sub``. Every failure is
    a 401 with a code that names the rule — never the value.
    """
    if not secret:
        raise ValueError("a signing key is required")
    if not isinstance(token, str) or token.count(".") != 2:
        raise invalid_token("the token is malformed")
    header_seg, _, _ = token.split(".")
    _check_algorithm(_json_load(_b64u_decode(header_seg)))
    _signature_ok(token, secret)
    payload_seg = token.split(".")[1]
    claims = _json_load(_b64u_decode(payload_seg))
    if not isinstance(claims, dict):
        raise invalid_token("the token payload is not an object")
    if claims.get("iss") != issuer:
        raise invalid_token("the token issuer is not recognised")
    if claims.get("aud") != audience:
        raise invalid_token("the token audience is not recognised")
    _check_time(claims, now, leeway)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise invalid_token("the token carries no subject")
    return claims


def peek_claims(token: str) -> Dict[str, Any]:
    """Read a token's claims WITHOUT verifying the signature.

    Used only to route a token to the right full verifier (agent vs human);
    the result must never be trusted. Every field a caller acts on is checked
    again by :func:`verify` inside the chosen verifier.
    """
    if not isinstance(token, str) or token.count(".") != 2:
        raise invalid_token("the token is malformed")
    payload_seg = token.split(".")[1]
    claims = _json_load(_b64u_decode(payload_seg))
    if not isinstance(claims, dict):
        raise invalid_token("the token payload is not an object")
    return claims

