"""The chat front door - the auth-gate identity, consumed as the portal does.

There is one front door in this product: the shared-frontend auth gate runs
Google OAuth and mints the RS256 ``os-session-token`` (``purpose:
os-session-token``, ``kid`` = RFC 7638 thumbprint, published at
``GET /auth/.well-known/jwks.json``). The chat surface **verifies** that token
and mints nothing of its own at the door. It re-implements no JOSE and no
allowlist: it calls the merged ``identity.sso.tokens``
(``verify_console_session_token``, ``allowlist_decision``) exactly as
``portal/server/sso.py`` does, and it is configuration-compatible with the
portal's environment contract (same cookie name, same purpose, same
``ROOT_ADMIN_EMAILS`` allowlist name) so both surfaces read one front door.

The trust split, in one place:

======================  ==============================================
input                   authority
======================  ==============================================
``os-session-token``    RS256 auth-gate token, verified offline (fail closed)
``X-Forwarded-Email``   the reverse proxy's authenticated client identity
``X-AO-Chat-Client``    which client integration is calling (declared)
request body / query    the conversation being addressed - **never** the tenant
======================  ==============================================

The tenant comes from the declared binding map only. A ``tenantId`` in the
body or a ``tenant`` query parameter is not a source of authority; when it
disagrees with the credential it is a refusal (:func:`assert_no_foreign_tenant`),
and when it agrees it has changed nothing. The auth-gate identity's own tenant
claim, when present, must equal the bound tenant - a mismatch is a refusal, not
a fallback.

Role resolution follows the portal: the role comes from *our* allowlist and
from *our* declared binding, never from the token's ``role`` claim and never
from anything the client asserts (see ``binding.Resolution.role_ignored``).

The JWKS mirror is the composition root's job. ``portal.server.sso`` translates
the published JWKS payload into a ``kid -> public key`` map with its own
``trusted_keys_from_jwks`` helper; this module accepts that translated map so
no second translation exists to drift.


---knowledge---
module_id: identity.chat.frontdoor
system: identity
app: chat
solution_class: enterprise
patterns: [consume-never-restate, fail-closed, declared-authority]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [ChatRequest, AuthGateIdentity, session_token_from_request, cookie_value]
invariants: "the chat surface verifies the auth-gate token and mints nothing of its own at the door"
gotchas: "it re-implements no JOSE and no allowlist: it calls identity.sso.tokens exactly as portal/server/sso.py does"
related: ["#505"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional

from . import credential as credential_mod
from .binding import IdentityMap, load_default_map
from .errors import (
    CredentialRequired,
    CrossTenantRefused,
    FrontDoorRefused,
    InvalidScope,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .credential import ChatCredential

#: The auth gate's session cookie (identical to ``portal.server.sso.SESSION_COOKIE``).
SESSION_COOKIE = "os-session-token"

#: The only token purpose accepted at the front door.
CONSOLE_TOKEN_PURPOSE = "os-session-token"

#: The super-admin role name (``identity.sso.tokens.CONSOLE_ALLOWLIST_ROLE``).
ROOT_ADMIN_ROLE = "root_admin"

#: Declares which client integration is calling; there is no default client.
CLIENT_HEADER = "x-ao-chat-client"

#: The proxy-injected trusted identity header (see ``binding``).
IDENTITY_HEADER = "x-forwarded-email"

#: Body / query fields a client may use to *attempt* to name a tenant.
#: They are never a source of authority - only grounds for a refusal.
TENANT_BODY_FIELD = "tenantId"
TENANT_QUERY_FIELD = "tenant"

#: Body / query fields naming the conversation being addressed.
CONVERSATION_BODY_FIELD = "conversationId"
CONVERSATION_QUERY_FIELD = "conversation"

#: Body field a client may use to attempt to assert its own role (discarded).
ROLE_BODY_FIELD = "role"


@dataclass(frozen=True)
class ChatRequest:
    """The request-shaped inputs the front door reads (no framework coupling)."""

    body: Mapping[str, Any] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    session_token: str = ""


@dataclass(frozen=True)
class AuthGateIdentity:
    """The verified auth-gate identity behind a chat session."""

    email: str
    name: str
    subject_id: str
    tenant_id: str
    role: str
    super_admin: bool


def session_token_from_request(request: ChatRequest) -> str:
    """The auth-gate session token: the explicit seam, else the cookie."""
    if request.session_token:
        return request.session_token
    return cookie_value(request.headers, SESSION_COOKIE)


def cookie_value(headers: Optional[Mapping[str, str]], name: str) -> str:
    """Read one cookie from a ``Cookie`` header (case-insensitive header name)."""
    if not headers:
        return ""
    raw = ""
    for header_name, header_value in headers.items():
        if str(header_name).strip().lower() == "cookie" and isinstance(header_value, str):
            raw = header_value
            break
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip()
    return ""


def header_value(headers: Optional[Mapping[str, str]], wanted: str) -> str:
    """Read one header case-insensitively."""
    if not headers:
        return ""
    wanted = wanted.strip().lower()
    for header_name, header_value in headers.items():
        if str(header_name).strip().lower() == wanted:
            return header_value.strip() if isinstance(header_value, str) else ""
    return ""


def default_console_verifier() -> Callable[..., Dict[str, Any]]:
    """The merged console-token verifier (the same one the portal consumes)."""
    from identity.sso.tokens import verify_console_session_token

    return verify_console_session_token


class ChatFrontDoor:
    """Verifies the auth-gate identity and binds it to one tenant + conversation.

    ``trusted_keys`` is the translated auth-gate JWKS mirror (``kid -> public
    key``); with none configured every session is refused (fail closed).
    ``signing_key`` mints the scoped chat credential and has no default.
    ``revocation_store`` is the merged jti deny list consulted on every
    verification.
    """

    def __init__(
        self,
        *,
        signing_key: Optional[bytes],
        revocation_store: Any,
        identity_map: Optional[IdentityMap] = None,
        trusted_keys: Optional[Mapping[str, Any]] = None,
        root_admin_emails: Optional[tuple] = None,
        allowlist_only: bool = True,
        verifier: Optional[Callable[..., Dict[str, Any]]] = None,
    ) -> None:
        self.signing_key = signing_key
        self.revocation_store = revocation_store
        self.identity_map = identity_map if identity_map is not None else load_default_map()
        self.trusted_keys: Dict[str, Any] = dict(trusted_keys or {})
        self.root_admin_emails: tuple = tuple(root_admin_emails or ())
        self.allowlist_only = bool(allowlist_only)
        self.console_verifier = verifier if verifier is not None else default_console_verifier()

    # --- front door ------------------------------------------------------- #

    def verify_session(
        self, token: str, *, now: Optional[int] = None
    ) -> Dict[str, Any]:
        """Verify an auth-gate ``os-session-token``; every error is a refusal."""
        if not token or not isinstance(token, str) or not token.strip():
            raise CredentialRequired(
                "no os-session-token was presented; the chat surface has no "
                "anonymous mode"
            )
        if not self.trusted_keys:
            raise FrontDoorRefused(
                "no auth-gate JWKS mirror is configured; refusing every session"
            )
        now_i = int(now if now is not None else time.time())
        try:
            claims = self.console_verifier(token, self.trusted_keys, now=now_i)
        except Exception as exc:  # noqa: BLE001 - any verification error is a refusal
            raise FrontDoorRefused(f"auth-gate session token refused: {exc}") from exc
        if not isinstance(claims, Mapping):
            raise FrontDoorRefused("auth-gate session token carried no claims")
        return dict(claims)

    def identity_from_claims(self, claims: Mapping[str, Any]) -> AuthGateIdentity:
        """Map verified claims onto the identity; the role is ours, not the token's."""
        from identity.sso.tokens import allowlist_decision

        subject_id = str(claims.get("sub") or "").strip()
        email = str(claims.get("email") or subject_id).strip().lower()
        if not subject_id or not email:
            raise FrontDoorRefused("auth-gate session carries no identity claim")
        role, allowed = allowlist_decision(
            email,
            root_admin_emails=self.root_admin_emails,
            allowlist_only=self.allowlist_only,
        )
        if not allowed:
            raise FrontDoorRefused(
                f"identity {email!r} is not authorized by the root-admin allowlist"
            )
        return AuthGateIdentity(
            email=email,
            name=str(claims.get("name") or email),
            subject_id=subject_id,
            tenant_id=str(claims.get("tenantId") or ""),
            role=role,
            super_admin=role == ROOT_ADMIN_ROLE,
        )

    # --- establish -------------------------------------------------------- #

    def establish(
        self, request: ChatRequest, *, now: Optional[int] = None
    ) -> "ChatCredential":
        """Verify the front door and mint the credential for one conversation."""
        claims = self.verify_session(
            session_token_from_request(request), now=now
        )
        identity = self.identity_from_claims(claims)
        client = header_value(request.headers, CLIENT_HEADER)
        if not client:
            raise FrontDoorRefused(
                f"no {CLIENT_HEADER} header: the chat surface refuses an "
                "undeclared client rather than assuming one"
            )
        resolution = self.identity_map.resolve_request(
            client=client,
            headers=request.headers,
            claimed_role=request.body.get(ROLE_BODY_FIELD, ""),
        )
        if identity.tenant_id and identity.tenant_id != resolution.tenant_id:
            raise CrossTenantRefused(
                f"auth-gate identity is in tenant {identity.tenant_id!r} but the "
                f"declared binding puts {resolution.external_id!r} in "
                f"{resolution.tenant_id!r}"
            )
        conversation_id = conversation_from_request(request)
        credential = credential_mod.mint_chat_credential(
            tenant_id=resolution.tenant_id,
            agent_id=resolution.agent_id,
            conversation_id=conversation_id,
            signing_key=self.signing_key,
            role=resolution.role,
            subject=identity.subject_id,
            now=now,
        )
        assert_no_foreign_tenant(request, credential)
        return credential

    def resume(
        self,
        token: str,
        *,
        conversation_id: Optional[str] = None,
        now: Optional[int] = None,
    ) -> "ChatCredential":
        """Verify an already-minted chat credential (revocation always consulted)."""
        return credential_mod.verify_chat_credential(
            token,
            self.signing_key,
            revocation_store=self.revocation_store,
            conversation_id=conversation_id,
            now=now,
        )

    def revoke(self, jti: str, *, now: Optional[int] = None) -> str:
        """Revoke a credential's jti on the merged deny list (logout)."""
        if not jti:
            raise InvalidScope("a jti is required to revoke a chat credential")
        from identity.sso.tokens import revoke_jti

        revoke_jti(
            self.revocation_store,
            jti,
            int(now if now is not None else time.time()),
        )
        return jti


def conversation_from_request(request: ChatRequest) -> str:
    """The conversation the request addresses (body first, then query)."""
    for field_name, source in (
        (CONVERSATION_BODY_FIELD, request.body),
        (CONVERSATION_QUERY_FIELD, request.query),
    ):
        value = source.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise InvalidScope(
        "a conversationId is required; the chat surface is scoped per conversation"
    )


def requested_tenant(request: ChatRequest) -> str:
    """Whatever tenant a client *claims*, from the body then the query.

    Returned only so the claim can be refused; it is never an authority.
    """
    for value in (
        request.body.get(TENANT_BODY_FIELD),
        request.query.get(TENANT_QUERY_FIELD),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def assert_no_foreign_tenant(request: ChatRequest, credential: "ChatCredential") -> None:
    """Refuse a request that names a tenant other than the credential's own.

    A ``tenantId`` in the body or a ``tenant`` query parameter cannot select a
    tenant; naming a foreign one is a refusal, and naming the correct one has
    changed nothing.
    """
    claimed = requested_tenant(request)
    if claimed and claimed != credential.tenant_id:
        raise CrossTenantRefused(
            f"request names tenant {claimed!r} but the credential is scoped to "
            f"{credential.tenant_id!r} (a request never selects a tenant)"
        )
