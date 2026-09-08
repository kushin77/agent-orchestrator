"""Consumer SDK auth — short-lived per-tenant session tokens (issue #41).

SDK auth doctrine (acceptance criterion 3): the SDK consumes **short-lived
session tokens** issued by the platform (console/session, per tenant), and
never stores, generates or hardcodes keys.  Tokens arrive from an injected
provider — by default the ``AGENTORCH_SESSION_TOKEN`` environment variable or
a caller-supplied callback — and every request attaches
``Authorization: Bearer <token>``.  Verification (signature, revocation,
expiry) is the platform's job at the public edge / control plane (issues
#35/#37/#38); the SDK only parses the claims it needs (tenant scoping + expiry)
and fails closed when the token is absent or unparseable.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .errors import ConfigurationError, UnauthorizedError
from .model import (
    CLAIM_AGENT_ID,
    CLAIM_EXPIRY,
    CLAIM_ROLE,
    CLAIM_SUBJECT,
    CLAIM_SUBJECT_TYPE,
    CLAIM_TENANT_ID,
    decode_claims,
)

#: Default environment variable holding the short-lived session token.
DEFAULT_TOKEN_ENV = "AGENTORCH_SESSION_TOKEN"

#: TokenProvider = a zero-argument callable returning the current session
#: token (or None when absent).  A deployment can return a freshly refreshed
#: short-lived token on every call.
TokenProvider = Callable[[], Optional[str]]


@dataclass(frozen=True)
class SessionToken:
    """Parsed view of a JWT-shaped session token (claims the SDK consumes)."""

    tenant_id: str
    subject: str
    subject_type: str = "user"  # user | agent (issue #10/#12 vocab)
    role: Optional[str] = None
    agent_id: Optional[str] = None
    expires_at: Optional[int] = None
    raw_claims: Dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        """True when the token carries an ``exp`` in the past (short-lived)."""
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at

    @classmethod
    def parse(cls, compact_token: str) -> "SessionToken":
        """Parse a compact session token, failing closed on a bad shape.

        Only the claims the SDK needs are surfaced; the platform verifies the
        signature.  A missing mandatory ``tenantId`` claim is a hard error (a
        session token is never tenant-less — issue #35/#10 vocabulary).
        """
        claims = decode_claims(compact_token)
        tenant_id = claims.get(CLAIM_TENANT_ID)
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("session token must carry a non-empty tenantId claim")
        return cls(
            tenant_id=tenant_id,
            subject=str(claims.get(CLAIM_SUBJECT) or ""),
            subject_type=str(claims.get(CLAIM_SUBJECT_TYPE) or "user"),
            role=claims.get(CLAIM_ROLE),
            agent_id=claims.get(CLAIM_AGENT_ID),
            expires_at=int(claims[CLAIM_EXPIRY]) if claims.get(CLAIM_EXPIRY) is not None else None,
            raw_claims=claims,
        )


def token_from_env(env_var: str = DEFAULT_TOKEN_ENV) -> Optional[str]:
    """Read the session token from ``env_var`` (defaults to
    ``AGENTORCH_SESSION_TOKEN``).  Returns ``None`` when unset so callers can
    fall through to a callback.
    """
    value = os.environ.get(env_var, "").strip()
    return value or None


class TokenSource:
    """Resolves the current session token from an env var and/or a callback.

    The callback (when given) is tried first — it may refresh the short-lived
    token on every call; otherwise the env var is read.  ``require()`` raises
    ``ConfigurationError`` when no token is available so a request never goes
    out unauthenticated (fail closed).
    """

    def __init__(
        self,
        *,
        env_var: str = DEFAULT_TOKEN_ENV,
        callback: Optional[TokenProvider] = None,
    ) -> None:
        self._env_var = env_var
        self._callback = callback

    def token(self) -> Optional[str]:
        if self._callback is not None:
            value = self._callback()
            if value:
                return value
        return token_from_env(self._env_var)

    def require(self) -> str:
        token = self.token()
        if not token:
            raise ConfigurationError(
                f"no session token available: set {self._env_var} or supply a "
                "token callback (short-lived, per-tenant; never hardcode keys)"
            )
        return token

    def bearer(self) -> str:
        """The ``Authorization`` header value for the current token."""
        return f"Bearer {self.require()}"


def bearer_token(compact_token: str) -> str:
    """Build an ``Authorization`` header value from a compact session token."""
    return f"Bearer {compact_token}"


def verify_not_expired(token: str) -> "SessionToken":
    """Parse a token and raise ``UnauthorizedError`` when it is invalid/expired.

    Used by SDK-side preflight so a caller does not wait for a round trip to
    learn a token lapsed or malformed; the platform remains the authority on
    signature/revocation.
    """
    try:
        session = SessionToken.parse(token)
    except ValueError as exc:
        raise UnauthorizedError("invalid session token") from exc
    if session.expired:
        raise UnauthorizedError("session token expired")
    return session
