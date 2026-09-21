"""Session authentication: verify (and, for offline exercise, mint) HS256
JWT-shaped tenant-scoped credentials.

The token format and claim vocabulary are consumed from the registry service
identity contract (issue #10, ``registry/service/identity.py``): an
HMAC-SHA256 signed ``header.payload.signature`` token whose payload carries the
scoped claims ``tenantId`` / ``agentId`` / ``role`` / ``allowedTools`` plus the
standard ``iss`` / ``sub`` / ``aud`` / ``iat`` / ``exp`` / ``jti`` names.

Encode and verify are not implemented here: both delegate to the identity
lane's canonical HS256 codec (``identity/sso/jose.py``, issue #1204), so this
module no longer carries its own base64url/HMAC. The wire format is unchanged -
it was always byte-identical to the registry's, and now it is literally the
same code.

Production issuance of these sessions belongs to the registry / control plane
(``registry.service.IdentityService.issue_session``); the gateway's job is
verification only. ``mint_session`` exists so the gateway is fully exercisable
offline (demo + tests) and is documented as an offline convenience - it is
never the gateway's production issuance path.

---knowledge---
module_id: gateway.mcp.authn
system: gateway
app: mcp
solution_class: enterprise
patterns: [consume-never-reimplement, delegate-to-canonical-codec, fail-closed]
derives_from: identity/sso/jose.py
owner_sme: security-sme
tier: L1
interfaces: [verify_token, session_to_token, mint_session]
invariants: "encode and verify delegate to the identity lane's canonical HS256 codec; this module carries no base64url or HMAC of its own"
gotchas: "the wire format is unchanged because it is now literally the same code, not a re-implementation"
related: ["#20", "#1204"]
do_not_duplicate: identity/sso/jose.py
---knowledge---
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Callable, Optional, Tuple

# The HS256 JWT codec is the identity lane's canonical one
# (``identity/sso/jose.py``); this module delegates to it rather than
# hand-rolling base64url/HMAC (issue #1204). ``gateway/`` is on ``sys.path`` for
# the ``mcp`` package, so the repository root (three levels above this file) is
# inserted here to resolve ``identity.sso`` - the same bootstrap the sibling
# ``mcp.sources`` / ``registry.packs`` modules use for cross-pillar imports.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from identity.sso.errors import (  # noqa: E402
    SignatureVerificationError as JoseSignatureError,
    SsoError as JoseError,
)
from identity.sso.jose import jwt_encode, jwt_unsign  # noqa: E402
from identity.sso.model import ALG_HS256  # noqa: E402

from .errors import InvalidCredentialError, SessionExpiredError  # noqa: E402
from .model import SessionIdentity  # noqa: E402

ISSUER = "urn:agent-orchestrator:mcp"
DEFAULT_AUDIENCE = ("control-plane",)
DEFAULT_ROLE = "agent"
DEFAULT_TTL_SECONDS = 3600


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
    """Encode a session to an HS256 token (header.payload.signature).

    Delegates to the canonical codec (``identity/sso/jose.py``, issue #1204);
    the output is byte-identical to what this module emitted before the
    collapse, so tokens minted by the old code still verify and vice versa.
    """
    return jwt_encode(session.to_claims(), alg=ALG_HS256, key=signing_key)


def verify_token(
    token: str,
    signing_key: bytes,
    *,
    now: Optional[int] = None,
) -> SessionIdentity:
    """Verify a token's signature + expiry; return the session it carries.

    Signature verification is the canonical codec's job
    (``identity/sso/jose.py``, issue #1204): a constant-time HMAC compare with
    ``alg`` pinned to HS256, so a ``none`` / algorithm-confusion token is
    refused before any signature work. Raises ``InvalidCredentialError`` on a
    malformed token, a signature mismatch or incomplete claims, and
    ``SessionExpiredError`` once ``exp`` has passed. A forged claim never
    verifies (HMAC signature).
    """
    try:
        claims = jwt_unsign(token, alg=ALG_HS256, key=signing_key)
    except JoseSignatureError as exc:
        raise InvalidCredentialError("token signature does not verify") from exc
    except JoseError as exc:
        raise InvalidCredentialError(f"malformed token: {exc}") from exc
    try:
        session = SessionIdentity.from_claims(claims)
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
