"""Session token issuance + verification (issue #35, AC #2 and AC #3).

Two token kinds, matching the consumed models:

1. **API session token** (AC #2) - HS256, mirroring the registry/service
   (issue #10) scoped-claims session credential. Claims always carry
   ``tenantId`` plus subject/role (rbac issue #12 vocabulary); the verifier
   rejects a token whose tenant claim is missing or not a known tenant and a
   caller can only use it inside that one tenant (no cross-tenant fallback).
2. **Console session token** (AC #3) - RS256 with ``purpose:
   "os-session-token"``, signed by a dedicated RSA key and verified
   kid-indexed against the published JWKS (``kid`` = RFC 7638 thumbprint),
   ported from shared-frontend's console auth module. The auth-hub relay
   ``state`` JWT (PKCE verifier + callback) is modeled as an HS256 short-lived
   token here.

Revocation: every session id (``jti``) can be revoked (logout /
impersonation revoke); a revoked token is refused even while unexpired.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from .errors import (
    InvalidSessionTokenError,
    RelayStateError,
    SessionRevokedError,
    SsoError,
)
from .jose import (
    ALG_HS256,
    ALG_RS256,
    jwt_encode,
    jwt_unsign,
    rfc7638_thumbprint,
)
from .model import (
    API_SESSION_PURPOSE,
    CONSOLE_AUDIENCE,
    CONSOLE_TOKEN_PURPOSE,
    MAX_TTL_SECONDS,
    RELAY_STATE_TTL_SECONDS,
    SESSION_AUDIENCE,
    SSO_ISSUER,
    SUBJECT_TYPES,
)
from .store import InMemoryStore

CONSOLE_ISSUER = "urn:agent-orchestrator:console"
RELAY_STATE_PURPOSE = "auth-hub-relay-state"
CONSOLE_ALLOWLIST_ROLE = "root_admin"
CONSOLE_PLAIN_ROLE = "user"

# --- API session token (AC #2) ----------------------------------------------


def _aud(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def issue_session_token(
    hmac_key: bytes,
    *,
    tenant_id: str,
    subject_id: str,
    subject_type: str,
    role: str,
    email: str = "",
    now: int,
    ttl: int = 3600,
    jti: Optional[str] = None,
    purpose: str = API_SESSION_PURPOSE,
    operator_user_id: Optional[str] = None,
    grant_id: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Mint an HS256 session token scoped to exactly one tenant.

    Claims use the registry (issue #10) + shared-governance vocabulary
    (``iss``/``sub``/``aud``/``iat``/``exp``/``jti`` + ``tenantId``) and the
    rbac (issue #12) subject/role snapshot. ``tenant_id`` is mandatory - a
    session without a tenant claim cannot be issued.
    """
    if not tenant_id:
        raise SsoError("cannot issue a session token without a tenant")
    if not subject_id:
        raise SsoError("cannot issue a session token without a subject")
    if subject_type not in SUBJECT_TYPES:
        raise SsoError(f"invalid subject_type {subject_type!r}")
    if ttl > MAX_TTL_SECONDS:
        raise SsoError(f"session ttl {ttl} exceeds maximum {MAX_TTL_SECONDS}")

    session_id = jti or f"jit_{uuid.uuid4().hex}"
    claims: dict[str, Any] = {
        "iss": SSO_ISSUER,
        "sub": subject_id,
        "aud": list(SESSION_AUDIENCE),
        "iat": int(now),
        "exp": int(now) + int(ttl),
        "jti": session_id,
        "purpose": purpose,
        "tenantId": tenant_id,
        "subjectType": subject_type,
        "role": role,
    }
    if email:
        claims["email"] = email
    if operator_user_id:
        claims["operatorUserId"] = operator_user_id
    if grant_id:
        claims["impersonationGrantId"] = grant_id

    token = jwt_encode(claims, alg=ALG_HS256, key=hmac_key)
    return token, claims


def _require_valid_tenant_claim(claims: dict[str, Any]) -> str:
    from .errors import InvalidTenantClaimError, MissingTenantClaimError

    tenant_id = claims.get("tenantId")
    if tenant_id is None or tenant_id == "":
        raise MissingTenantClaimError(
            "session token carries no tenantId claim"
        )
    if not isinstance(tenant_id, str):
        raise InvalidTenantClaimError("session token tenantId is not a string")
    return tenant_id


def verify_session_token(
    token: str,
    hmac_key: bytes,
    *,
    now: int,
    require_tenant: bool = True,
) -> dict[str, Any]:
    """Verify an HS256 session token and return its claims.

    Fail-closed sequence: signature, expiry, and (when ``require_tenant``) the
    tenant claim. The caller then checks the tenant the token is being *used
    in* against the claim (see ``session_matches_tenant``) - a tenant-A token
    presented to tenant B is refused there.
    """
    try:
        claims = jwt_unsign(token, alg=ALG_HS256, key=hmac_key)
    except SsoError as exc:
        raise InvalidSessionTokenError(f"session token invalid: {exc}") from exc

    expiry = claims.get("exp")
    if not isinstance(expiry, int) or int(now) > expiry:
        raise InvalidSessionTokenError("session token has expired")
    if claims.get("purpose") not in (API_SESSION_PURPOSE, CONSOLE_TOKEN_PURPOSE):
        raise InvalidSessionTokenError(
            f"session token has unexpected purpose {claims.get('purpose')!r}"
        )

    if require_tenant:
        _require_valid_tenant_claim(claims)
    return claims


def session_matches_tenant(claims: dict[str, Any], tenant_id: str) -> bool:
    """True when the session's tenant claim is exactly ``tenant_id``."""
    return claims.get("tenantId") == tenant_id


def check_not_revoked(store: InMemoryStore, claims: dict[str, Any]) -> None:
    """Refuse a session whose jti has been revoked (logout / impersonation)."""
    jti = claims.get("jti")
    if jti is not None and store.is_revoked(str(jti)):
        raise SessionRevokedError(f"session {jti} has been revoked")


# --- Console session token (AC #3, shared-frontend model) --------------------


def issue_console_session_token(
    private_key: Any,
    *,
    kid: str,
    tenant_id: str,
    subject_id: str,
    email: str,
    name: str,
    role: str,
    now: int,
    ttl: int = 3600,
) -> tuple[str, dict[str, Any]]:
    """Mint the RS256 ``os-session-token`` the shell hands to modules.

    ``kid`` is the signing key's RFC 7638 thumbprint; verifiers resolve the
    public key from the published JWKS by ``kid`` (fail closed on an unknown
    kid). ``purpose: os-session-token`` distinguishes it from the HS256
    session cookie / OAuth state token, both of which are rejected by
    console-token verification (shared-frontend issue #86/#96 semantics).
    """
    session_id = f"jit_{uuid.uuid4().hex}"
    claims: dict[str, Any] = {
        "iss": CONSOLE_ISSUER,
        "sub": subject_id,
        "aud": list(CONSOLE_AUDIENCE),
        "iat": int(now),
        "exp": int(now) + int(ttl),
        "jti": session_id,
        "purpose": CONSOLE_TOKEN_PURPOSE,
        "tenantId": tenant_id,
        "email": email,
        "name": name,
        "role": role,
    }
    token = jwt_encode(claims, alg=ALG_RS256, key=private_key, kid=kid)
    return token, claims


def console_jwks(public_keys: list[tuple[str, Any]]) -> dict[str, Any]:
    """The public JWKS for console-token verification (``{keys: [...]}``)."""
    from .jose import jwks_for_keys

    return jwks_for_keys(public_keys)


def verify_console_session_token(
    token: str,
    trusted_public_keys: dict[str, Any],
    *,
    now: int,
    require_purpose: bool = True,
) -> dict[str, Any]:
    """Verify an RS256 console token kid-indexed against trusted public keys.

    Fail closed: a token whose ``kid`` matches no trusted key is rejected
    (shared-frontend issue #131), as is any token that is not the
    ``os-session-token`` purpose or that has expired.
    """
    header, claims = _split_console_token(token)
    kid = header.get("kid")
    if not kid or kid not in trusted_public_keys:
        raise InvalidSessionTokenError(
            f"console token kid {kid!r} is not in the trusted key set"
        )
    public_key = trusted_public_keys[kid]
    try:
        verified = jwt_unsign(token, alg=ALG_RS256, key=public_key)
    except SsoError as exc:
        raise InvalidSessionTokenError(
            f"console token invalid: {exc}"
        ) from exc
    expiry = verified.get("exp")
    if not isinstance(expiry, int) or int(now) > expiry:
        raise InvalidSessionTokenError("console token has expired")
    if require_purpose and verified.get("purpose") != CONSOLE_TOKEN_PURPOSE:
        raise InvalidSessionTokenError(
            f"console token purpose {verified.get('purpose')!r} is not "
            f"{CONSOLE_TOKEN_PURPOSE!r}"
        )
    _require_valid_tenant_claim(verified)
    return verified


def _split_console_token(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from .jose import _parse_segments

    try:
        header, claims, _ = _parse_segments(token)
    except InvalidSessionTokenError:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed token is a denial
        raise InvalidSessionTokenError("malformed console token") from exc
    if header.get("alg") != ALG_RS256:
        raise InvalidSessionTokenError(
            f"console token alg {header.get('alg')!r} is not {ALG_RS256}"
        )
    return header, claims


def console_kid_for(public_key: Any) -> str:
    """The RFC 7638 thumbprint used as this key's JWKS kid."""
    return rfc7638_thumbprint(public_key)


# --- Relay state (auth-hub relay + PKCE, shared-frontend model) --------------


def encode_relay_state(
    hmac_key: bytes,
    *,
    backend_callback_url: str,
    code_verifier: str,
    now: int,
    ttl: int = RELAY_STATE_TTL_SECONDS,
) -> str:
    """Encode the short-lived auth-gate ``state`` JWT (HS256).

    The shared-frontend auth-hub does not sign ``state``, so the auth-gate
    embeds an HS256 JWT carrying the backend callback + PKCE code verifier;
    nothing about the flow works if that JWT is tampered with.
    """
    claims: dict[str, Any] = {
        "iss": CONSOLE_ISSUER,
        "iat": int(now),
        "exp": int(now) + int(ttl),
        "jti": f"jit_{uuid.uuid4().hex}",
        "purpose": RELAY_STATE_PURPOSE,
        "backend_callback_url": backend_callback_url,
        "code_verifier": code_verifier,
    }
    return jwt_encode(claims, alg=ALG_HS256, key=hmac_key)


def verify_relay_state(
    token: str,
    hmac_key: bytes,
    *,
    now: int,
) -> dict[str, Any]:
    """Verify a relay ``state`` JWT; a tampered/expired one is RelayStateError."""
    try:
        claims = jwt_unsign(token, alg=ALG_HS256, key=hmac_key)
    except SsoError as exc:
        raise RelayStateError(f"relay state invalid: {exc}") from exc
    if claims.get("purpose") != RELAY_STATE_PURPOSE:
        raise RelayStateError(
            f"token purpose {claims.get('purpose')!r} is not relay state"
        )
    expiry = claims.get("exp")
    if not isinstance(expiry, int) or int(now) > expiry:
        raise RelayStateError("relay state has expired")
    for field_name in ("backend_callback_url", "code_verifier"):
        if not claims.get(field_name):
            raise RelayStateError(f"relay state carries no {field_name}")
    return claims


# --- allowlist policy (shared-frontend ROOT_ADMIN_EMAILS) ---------------------


def allowlist_decision(
    email: str,
    *,
    root_admin_emails: tuple[str, ...],
    allowlist_only: bool = True,
) -> tuple[str, bool]:
    """The console allowlist verdict for an email.

    Mirrors shared-frontend: allowlisted emails (lowercased) become
    ``root_admin``; with ``allowlist_only`` (default) everyone else is denied;
    otherwise a non-allowlisted user is allowed with role ``user``.
    """
    normalized = email.strip().lower()
    if normalized in {item.strip().lower() for item in root_admin_emails}:
        return CONSOLE_ALLOWLIST_ROLE, True
    if allowlist_only:
        return CONSOLE_PLAIN_ROLE, False
    return CONSOLE_PLAIN_ROLE, True


# --- revocation helpers --------------------------------------------------------


def revoke_jti(store: InMemoryStore, jti: str, revoked_at: int) -> None:
    store.revoke_jti(jti, int(revoked_at))


def is_revoked(store: InMemoryStore, jti: str) -> bool:
    return store.is_revoked(jti)


def tenant_id_of(claims: dict[str, Any]) -> str:
    """The tenant claim of verified session claims (validated non-empty)."""
    return _require_valid_tenant_claim(claims)
