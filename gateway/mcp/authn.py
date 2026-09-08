"""Session authentication: verify (and, for offline exercise, mint) HS256
JWT-shaped tenant-scoped credentials.

The token format and claim vocabulary are consumed from the registry service
identity contract (issue #10, ``registry/service/identity.py``): an
HMAC-SHA256 signed ``header.payload.signature`` token whose payload carries the
scoped claims ``tenantId`` / ``agentId`` / ``role`` / ``allowedTools`` plus the
standard ``iss`` / ``sub`` / ``aud`` / ``iat`` / ``exp`` / ``jti`` names.

Production issuance of these sessions belongs to the registry / control plane
(``registry.service.IdentityService.issue_session``); the gateway's job is
verification only. ``mint_session`` exists so the gateway is fully exercisable
offline (demo + tests) with byte-identical tokens to what the registry issues,
and is documented as an offline convenience - it is never the gateway's
production issuance path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Callable, Optional, Tuple

from .errors import InvalidCredentialError, SessionExpiredError
from .model import SessionIdentity

ISSUER = "urn:agent-orchestrator:mcp"
DEFAULT_AUDIENCE = ("control-plane",)
DEFAULT_ROLE = "agent"
DEFAULT_TTL_SECONDS = 3600

_JSON_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}
_HEADER = {"alg": "HS256", "typ": "JWT"}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(signing_input: bytes, signing_key: bytes) -> str:
    return _b64url_encode(
        hmac.new(signing_key, signing_input, hashlib.sha256).digest()
    )


def mint_session(
    tenant_id: str,
    agent_id: str,
    signing_key: bytes,
    *,
    role: str = DEFAULT_ROLE,
    allowed_tools: Tuple[str, ...] = (),
    subject: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[int] = None,
    issuer: str = ISSUER,
    audience: Tuple[str, ...] = DEFAULT_AUDIENCE,
) -> SessionIdentity:
    """Build a session identity (offline minting convenience; see module doc)."""
    now = now if now is not None else int(time.time())
    return SessionIdentity(
        tenant_id=tenant_id,
        agent_id=agent_id,
        role=role,
        allowed_tools=tuple(allowed_tools),
        subject=subject or agent_id,
        issuer=issuer,
        audience=tuple(audience),
        issued_at=now,
        expires_at=now + int(ttl_seconds),
        token_id=uuid.uuid4().hex,
    )


def session_to_token(session: SessionIdentity, signing_key: bytes) -> str:
    """Encode a session to an HS256 token (header.payload.signature)."""
    header_part = _b64url_encode(
        json.dumps(_HEADER, **_JSON_KW).encode("utf-8")
    )
    payload_part = _b64url_encode(
        json.dumps(session.to_claims(), **_JSON_KW).encode("utf-8")
    )
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    return f"{header_part}.{payload_part}.{_sign(signing_input, signing_key)}"


def verify_token(
    token: str,
    signing_key: bytes,
    *,
    now: Optional[int] = None,
) -> SessionIdentity:
    """Verify a token's signature + expiry; return the session it carries.

    Raises ``InvalidCredentialError`` on a malformed token, a signature
    mismatch or incomplete claims, and ``SessionExpiredError`` once ``exp``
    has passed. A forged claim never verifies (HMAC signature).
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidCredentialError("malformed token: expected 3 segments")
    header_part, payload_part, signature_part = parts
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    expected = _sign(signing_input, signing_key)
    if not hmac.compare_digest(expected, signature_part):
        raise InvalidCredentialError("token signature does not verify")
    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCredentialError("token payload is not valid JSON") from exc
    try:
        session = SessionIdentity.from_claims(payload)
    except ValueError as exc:
        raise InvalidCredentialError(str(exc)) from exc
    now = now if now is not None else int(time.time())
    if session.expires_at and now >= session.expires_at:
        raise SessionExpiredError("session token has expired")
    return session


# A session verifier is any callable taking the raw token and returning a
# :class:`SessionIdentity` (or raising an :class:`AuthnError`). The default is
# ``verify_token`` bound to the gateway's signing key; a deployment that mints
# sessions through the registry service injects its own verifier here.
SessionVerifier = Callable[[str], SessionIdentity]
