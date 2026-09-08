"""SsoService - orchestrates tenant SSO login flows + session lifecycle.

This is the enforcement core of the tenant-identity lane (issue #35):

- **Host -> tenant -> provider** resolution (fail closed, no default tenant);
- **SAML / OIDC completion** into a tenant principal (signature + conditions +
  attribute mapping verified upstream in ``saml.py`` / ``oidc.py``);
- **email-domain policy** - a valid assertion from tenant A's IdP can still be
  denied if the identity's email domain is not allowed in tenant A (verified
  domains, capital-underwriting vocabulary);
- **session issuance** scoped to exactly the tenant the principal
  authenticated into, and **usage** that refuses a tenant-A session in tenant B
  (no cross-tenant fallback - the rbac issue #12 / registry issue #10
  doctrine);
- **logout / revocation** (jti-keyed) and an append-only SSO audit log;
- the **console SSO model** (AC #3): auth-hub relay ``state`` JWT (PKCE),
  allowlist, RS256 ``os-session-token`` + JWKS (shared-frontend port).
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Optional

from . import oidc as oidc_mod
from . import saml as saml_mod
from .config import require_sso_config
from .domains import email_domain, require_tenant_for_host
from .errors import (
    CrossTenantDenied,
    LoginDeniedError,
    SsoConfigError,
    SsoError,
)
from .keystore import KeyStore
from .model import (
    API_SESSION_PURPOSE,
    EVENT_LOGIN,
    EVENT_LOGOUT,
    ResolvedPrincipal,
    SsoAuditEvent,
    SsoSession,
    TenantSsoConfig,
    utcnow_iso,
)
from .store import InMemoryStore
from .tokens import (
    check_not_revoked,
    console_kid_for,
    encode_relay_state,
    issue_console_session_token,
    issue_session_token,
    session_matches_tenant,
    verify_console_session_token,
    verify_session_token,
    verify_relay_state,
    allowlist_decision,
    console_jwks,
)


class SsoService:
    """Facade bundling the tenant SSO flows + console SSO model.

    ``session_hmac_key`` signs HS256 API session tokens (injected from
    env/secrets - never stored). ``console_signing_key`` (RSA private) signs
    the RS256 console token; ``console_verify_public_keys`` optionally adds
    retained (previously-signing) public keys so a rollover window keeps old
    tokens verifying (shared-frontend issue #131 semantics).
    """

    def __init__(
        self,
        store: InMemoryStore,
        keystore: KeyStore,
        *,
        session_hmac_key: bytes,
        relay_hmac_key: Optional[bytes] = None,
        console_signing_key: Any = None,
        console_verify_public_keys: Optional[dict[str, Any]] = None,
        root_admin_emails: tuple[str, ...] = (),
        allowlist_only: bool = True,
        clock_tolerance_s: int = 60,
    ) -> None:
        self.store = store
        self.keystore = keystore
        self.session_hmac_key = session_hmac_key
        self.relay_hmac_key = relay_hmac_key or session_hmac_key
        self.console_signing_key = console_signing_key
        self.console_verify = dict(console_verify_public_keys or {})
        if console_signing_key is not None:
            self.console_verify[console_kid_for(console_signing_key.public_key())] = (
                console_signing_key.public_key()
            )
        self.root_admin_emails = root_admin_emails
        self.allowlist_only = allowlist_only
        self.clock_tolerance_s = clock_tolerance_s

    # --- host resolution (fail closed) --------------------------------------

    def resolve_tenant(self, host_header: str) -> str:
        return require_tenant_for_host(self.store, host_header)

    def config_for_host(self, host_header: str) -> TenantSsoConfig:
        """The tenant + its enabled SSO config for a host, or fail closed."""
        tenant_id = self.resolve_tenant(host_header)
        return require_sso_config(self.store, tenant_id)

    # --- SAML flow -----------------------------------------------------------

    def start_saml(self, tenant_id: str) -> dict[str, str]:
        """SP-initiated SAML: build the AuthnRequest + IdP redirect URL."""
        config = require_sso_config(self.store, tenant_id)
        request_id = f"_{uuid.uuid4().hex}"
        issue_instant = utcnow_iso()
        authn_xml = saml_mod.build_authn_request(
            config, request_id=request_id, issue_instant=issue_instant
        )
        request_b64 = base64.b64encode(authn_xml).decode("ascii")
        return {
            "protocol": "saml",
            "tenant_id": tenant_id,
            "authn_request": authn_xml.decode("utf-8"),
            "request_b64": request_b64,
            "redirect_url": saml_mod.sso_redirect_url(config, request_b64),
        }

    # --- OIDC flow ------------------------------------------------------------

    def start_oidc(
        self,
        tenant_id: str,
        *,
        state: str,
        nonce: str = "",
        code_challenge: str = "",
    ) -> str:
        config = require_sso_config(self.store, tenant_id)
        return oidc_mod.build_authorization_url(
            config,
            state=state,
            nonce=nonce or None,
            code_challenge=code_challenge or None,
        )

    # --- completion (assertion -> principal -> session) -----------------------

    def complete_saml(
        self,
        host_header: str,
        assertion_xml: bytes | str,
        *,
        now: Optional[int] = None,
    ) -> SsoSession:
        """Handle a SAML ACS callback: verify + map + issue a session."""
        config = self.config_for_host(host_header)
        principal = saml_mod.parse_and_verify_assertion(
            assertion_xml, config, self.keystore, now=now
        )
        return self._issue(config, principal, now=now)

    def complete_oidc(
        self,
        host_header: str,
        id_token: str,
        *,
        nonce: Optional[str] = None,
        now: Optional[int] = None,
    ) -> SsoSession:
        """Handle an OIDC redirect: verify id_token + map + issue a session."""
        config = self.config_for_host(host_header)
        claims = oidc_mod.verify_id_token(
            id_token, config, self.keystore, now=now, nonce=nonce
        )
        principal = oidc_mod.principal_from_id_token(claims, config)
        return self._issue(config, principal, now=now)

    def _issue(
        self,
        config: TenantSsoConfig,
        principal: ResolvedPrincipal,
        *,
        now: Optional[int] = None,
    ) -> SsoSession:
        """Issue a session for an authenticated principal.

        The session is scoped to **the tenant the principal authenticated
        into** (``config.tenant_id``) - never to a caller-supplied tenant.
        """
        if principal.tenant_id != config.tenant_id:
            # Defensive: the parsed principal must land in its own tenant.
            raise CrossTenantDenied(
                f"principal tenant {principal.tenant_id!r} != config tenant "
                f"{config.tenant_id!r}"
            )
        self._enforce_email_domain(config, principal.email)

        now_i = int(now if now is not None else time.time())
        token, claims = issue_session_token(
            self.session_hmac_key,
            tenant_id=config.tenant_id,
            subject_id=principal.subject_id,
            subject_type=principal.subject_type,
            role=principal.role,
            email=principal.email,
            now=now_i,
            ttl=config.token_ttl_seconds,
            purpose=API_SESSION_PURPOSE,
        )
        session = SsoSession(
            token=token,
            tenant_id=config.tenant_id,
            subject_id=principal.subject_id,
            subject_type=principal.subject_type,
            role=principal.role,
            email=principal.email,
            session_id=str(claims["jti"]),
            purpose=API_SESSION_PURPOSE,
            issued_at=int(claims["iat"]),
            expires_at=int(claims["exp"]),
        )
        self._audit(
            EVENT_LOGIN,
            config.tenant_id,
            principal.subject_id,
            session_id=session.session_id,
            detail=f"{config.protocol} login (idp_subject={principal.idp_subject})",
        )
        return session

    # --- email-domain policy ---------------------------------------------------

    def _enforce_email_domain(self, config: TenantSsoConfig, email: str) -> None:
        """Verified-domains gate: an identity's email domain must be allowed.

        Allowed domains = the config's ``domain_restrictions`` when set,
        otherwise the tenant's registered (custom) domains. When a tenant has
        neither, no domain gate applies. Refusals are LoginDeniedError - a
        *valid* assertion can still be denied at this policy boundary.
        """
        if config.domain_restrictions:
            allowed = {item.strip().lower() for item in config.domain_restrictions}
        else:
            domains = self.store.tenant_domains(config.tenant_id)
            if domains is None:
                return
            primary, aliases = domains
            allowed = {primary.lower(), *(item.lower() for item in aliases)}
        domain = email_domain(email)
        if not allowed or not domain:
            return
        if domain not in allowed:
            raise LoginDeniedError(
                f"email domain {domain!r} is not allowed for tenant "
                f"{config.tenant_id!r} (verified domains: {sorted(allowed)})"
            )

    # --- session use -----------------------------------------------------------

    def verify_session(
        self,
        token: str,
        *,
        expected_tenant: Optional[str] = None,
        now: Optional[int] = None,
    ) -> dict[str, Any]:
        """Verify a session token for use.

        ``expected_tenant`` enforces the no-cross-tenant rule at use time: a
        session minted for tenant A presented in tenant B is a
        ``CrossTenantDenied`` - there is no cross-tenant fallback.
        """
        now_i = int(now if now is not None else time.time())
        claims = verify_session_token(token, self.session_hmac_key, now=now_i)
        check_not_revoked(self.store, claims)
        if expected_tenant is not None and not session_matches_tenant(
            claims, expected_tenant
        ):
            raise CrossTenantDenied(
                f"session claims tenant {claims.get('tenantId')!r} but is "
                f"being used in tenant {expected_tenant!r}"
            )
        return claims

    def logout(self, token: str, *, now: Optional[int] = None) -> str:
        """Revoke a session (logout): the jti enters the revocation list."""
        from .tokens import jwt_unsign  # local import avoids a cycle

        try:
            claims = jwt_unsign(token, alg="HS256", key=self.session_hmac_key)
        except SsoError as exc:
            raise SsoError(f"logout refused: {exc}") from exc
        jti = str(claims.get("jti", ""))
        if not jti:
            raise SsoError("logout refused: token carries no jti")
        tenant_id = str(claims.get("tenantId", ""))
        subject = str(claims.get("sub", ""))
        now_i = int(now if now is not None else time.time())
        self.store.revoke_jti(jti, now_i)
        self._audit(
            EVENT_LOGOUT, tenant_id, subject, session_id=jti, detail="logout"
        )
        return jti

    def _audit(
        self,
        event: str,
        tenant_id: str,
        subject_id: str,
        *,
        session_id: str = "",
        operator_user_id: Optional[str] = None,
        grant_id: Optional[str] = None,
        detail: str = "",
    ) -> None:
        self.store.add_audit(
            SsoAuditEvent(
                event=event,
                tenant_id=tenant_id,
                subject_id=subject_id,
                at=utcnow_iso(),
                session_id=session_id,
                operator_user_id=operator_user_id,
                grant_id=grant_id,
                detail=detail,
            )
        )

    # --- console SSO model (AC #3, shared-frontend port) ------------------------

    def console_relay_state(
        self, backend_callback_url: str, *, now: Optional[int] = None
    ) -> dict[str, str]:
        """Model the auth-hub relay handoff: build the signed ``state`` JWT
        carrying the backend callback + a PKCE code verifier."""
        if self.relay_hmac_key is None:
            raise SsoConfigError("relay_hmac_key is not configured")
        now_i = int(now if now is not None else time.time())
        code_verifier = "".join(uuid.uuid4().hex for _ in range(4))[:64]
        state_token = encode_relay_state(
            self.relay_hmac_key,
            backend_callback_url=backend_callback_url,
            code_verifier=code_verifier,
            now=now_i,
        )
        return {"state": state_token, "code_verifier": code_verifier}

    def complete_console_login(
        self,
        state_token: str,
        *,
        identity_email: str,
        tenant_id: str,
        now: Optional[int] = None,
    ) -> SsoSession:
        """Model the console callback: verify relay state, run the allowlist,
        and mint the RS256 ``os-session-token`` for the console session."""
        if self.relay_hmac_key is None or self.console_signing_key is None:
            raise SsoConfigError(
                "console SSO requires relay_hmac_key + console_signing_key"
            )
        now_i = int(now if now is not None else time.time())
        state = verify_relay_state(
            state_token, self.relay_hmac_key, now=now_i
        )
        role, allowed = allowlist_decision(
            identity_email,
            root_admin_emails=self.root_admin_emails,
            allowlist_only=self.allowlist_only,
        )
        if not allowed:
            raise LoginDeniedError(
                f"email {identity_email!r} is not allowlisted for the console"
            )
        kid = console_kid_for(self.console_signing_key.public_key())
        token, claims = issue_console_session_token(
            self.console_signing_key,
            kid=kid,
            tenant_id=tenant_id,
            subject_id=identity_email,
            email=identity_email,
            name=identity_email.split("@", 1)[0],
            role=role,
            now=now_i,
        )
        session = SsoSession(
            token=token,
            tenant_id=tenant_id,
            subject_id=identity_email,
            subject_type="user",
            role=role,
            email=identity_email,
            session_id=str(claims["jti"]),
            purpose=str(claims["purpose"]),
            issued_at=int(claims["iat"]),
            expires_at=int(claims["exp"]),
        )
        self._audit(
            EVENT_LOGIN,
            tenant_id,
            identity_email,
            session_id=session.session_id,
            detail=f"console login via relay (role={role})",
        )
        return session

    def verify_console(self, token: str, *, now: Optional[int] = None) -> dict[str, Any]:
        """Verify an RS256 console token against the trusted kid set."""
        if not self.console_verify:
            raise SsoConfigError("no console verification keys configured")
        now_i = int(now if now is not None else time.time())
        return verify_console_session_token(
            token, self.console_verify, now=now_i
        )

    def console_jwks_payload(self) -> dict[str, Any]:
        """The public JWKS for offline console-token verification."""
        if not self.console_verify:
            raise SsoConfigError("no console verification keys configured")
        return console_jwks(
            [(kid, key) for kid, key in sorted(self.console_verify.items())]
        )
