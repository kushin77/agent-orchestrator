"""Authentication at the public edge - the authN side of authn-never-authz.

The edge **authenticates**: it verifies the caller's session token through an
injected verifier seam whose contract is issue #35's
``identity.sso.sessions.SsoService.verify_session`` - ``verify(token,
expected_tenant=None, now=None) -> claims`` raising on an invalid/expired/
revoked token or a tenant mismatch.  The seam is duck-typed so tests inject a
stub and the integration test injects the *real* offline ``SsoService``; the
edge never reimplements token verification.

This module performs **no authorization**.  It extracts from the verified
claims the caller's identity + a role *snapshot* (context for audit, never a
grant) and hands them to the forwarding layer.  Whether the caller *may*
perform the requested action is answered downstream (identity/rbac #12), not
here.

Fail closed: a missing, malformed, expired, revoked, tenant-mismatched or
otherwise invalid token is an ``unauthenticated`` rejection - the edge never
continues unauthenticated and never guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from identity.edges.model import (
    CLAIM_EMAIL,
    CLAIM_ROLE,
    CLAIM_SUBJECT,
    CLAIM_SUBJECT_TYPE,
    CLAIM_TENANT_ID,
    CODE_UNAUTHENTICATED,
    DEFAULT_SUBJECT_TYPE,
    SUBJECT_TYPES,
    ForwardedIdentity,
)

#: The injected session verifier contract (issue #35 verify_session
#: semantics): returns verified claims, raises on any invalid token.
Verifier = Callable[..., dict[str, Any]]

#: A rejected authentication (no caller identity).
UNAUTHENTICATED_MESSAGE = "authentication required - a valid session token is needed"


@dataclass(frozen=True)
class AuthnResult:
    """The outcome of authenticating one request at the edge.

    ``ok`` is True only for a verified caller; ``identity`` carries what the
    edge vouches for downstream and ``role_snapshot`` the session's role keys
    as context.  ``code``/``message`` describe the fail-closed rejection when
    ``ok`` is False.
    """

    ok: bool
    identity: Optional[ForwardedIdentity] = None
    role_snapshot: tuple[str, ...] = ()
    code: str = ""
    message: str = ""


def bearer_token(headers: Mapping[str, str]) -> Optional[str]:
    """Extract a Bearer session token from the request headers.

    Returns ``None`` when the header is absent or not a well-formed
    ``Authorization: Bearer <token>`` (fail closed - never guess).
    """
    if not isinstance(headers, Mapping):
        return None
    auth = headers.get("authorization")
    if not isinstance(auth, str):
        auth = headers.get("Authorization")
    if not isinstance(auth, str) or not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _denied(code: str, message: str) -> AuthnResult:
    return AuthnResult(ok=False, code=code, message=message)


def verify_caller(
    verifier: Optional[Verifier],
    token: Optional[str],
    *,
    expected_tenant: Optional[str] = None,
    now: Optional[int] = None,
) -> AuthnResult:
    """Verify a Bearer token at the edge (fail closed).

    Missing/empty token or any verifier failure is an ``unauthenticated``
    rejection.  A verified token yields the caller identity + role snapshot
    derived strictly from the claims.

    ``expected_tenant`` pins the use-time tenant (issue #35 no-cross-tenant
    rule) when the route carries a tenant pin; ``None`` lets the downstream
    resolve tenant scoping with the claims tenant.
    """
    if verifier is None:
        # A public edge must have an authenticator configured for routes that
        # require one - no verifier means no authentication (fail closed).
        return _denied(CODE_UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
    if not token:
        return _denied(CODE_UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
    try:
        claims = verifier(token, expected_tenant=expected_tenant, now=now)
    except Exception:
        # Never reveal the precise failure (expired vs revoked vs tampered) -
        # each is an unauthenticated rejection.
        return _denied(CODE_UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
    return _caller_from_claims(claims)


def _caller_from_claims(claims: Mapping[str, Any]) -> AuthnResult:
    """Build the caller identity from *verified* claims (authN, not authz).

    Defensive fail-closed extraction: a verified token that still lacks a
    usable tenant/subject/kind is refused rather than guessed at.
    """
    tenant_id = claims.get(CLAIM_TENANT_ID)
    subject_id = claims.get(CLAIM_SUBJECT)
    if not isinstance(tenant_id, str) or not tenant_id:
        return _denied(CODE_UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
    if not isinstance(subject_id, str) or not subject_id:
        return _denied(CODE_UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
    subject_type = claims.get(CLAIM_SUBJECT_TYPE) or DEFAULT_SUBJECT_TYPE
    if subject_type not in SUBJECT_TYPES:
        # An unknown subject kind is refused - never forwarded half-verified.
        return _denied(CODE_UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
    email = claims.get(CLAIM_EMAIL)
    if not isinstance(email, str):
        email = ""
    role = claims.get(CLAIM_ROLE)
    role_snapshot = (role,) if isinstance(role, str) and role else ()
    identity = ForwardedIdentity(
        tenant_id=tenant_id,
        subject_id=subject_id,
        subject_type=subject_type,
        email=email,
    )
    return AuthnResult(ok=True, identity=identity, role_snapshot=role_snapshot)


def sso_verifier(service: Any) -> Verifier:
    """Adapt an issue #35 ``SsoService`` into the edge's verifier seam.

    The returned callable forwards straight to the service's ``verify_session``
    (signature + expiry + revocation + optional expected-tenant checks) - the
    edge consumes #35's real semantics rather than reimplementing them.
    """

    def _verify(
        token: str, *, expected_tenant: Optional[str] = None, now: Optional[int] = None
    ) -> dict[str, Any]:
        return service.verify_session(
            token, expected_tenant=expected_tenant, now=now
        )

    return _verify
