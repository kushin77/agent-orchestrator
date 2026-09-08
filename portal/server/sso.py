"""portal.server.sso — console SSO (injects identity/sso, issue #35).

The console's session check consumes the merged identity/sso console model
verbatim: auth-hub relay ``state`` JWT (HS256, PKCE code verifier) and the
RS256 ``os-session-token`` (``purpose: os-session-token``, kid = RFC 7638
thumbprint, published JWKS, ROOT_ADMIN allowlist). This module is a thin
adapter over :class:`identity.sso.sessions.SsoService` so the portal exercises
the real #35 implementation rather than a re-implementation.

Roles: allowlisted emails become ``root_admin`` (super-admin); everyone else
is a scoped ``user`` whose tenant/role comes from the org directory (authz).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# Repo root bootstrap: identity/ is a PEP-420 namespace under the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Session cookie name (HttpOnly; the shell never reads the token).
SESSION_COOKIE = "os-session-token"

#: Allowlisted emails -> root_admin (super-admin) console role.
DEFAULT_ROOT_ADMIN_EMAILS = ("root@platform.example.com",)


class ConsoleSso:
    """Thin wrapper over the real #35 console SSO flow."""

    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        root_admin_emails: tuple[str, ...] = DEFAULT_ROOT_ADMIN_EMAILS,
        allowlist_only: bool = False,
        session_hmac_key: bytes = b"portal-demo-session-hmac-key-0000000000000",
        relay_hmac_key: Optional[bytes] = None,
        console_signing_key: Any = None,
    ) -> None:
        import sys

        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            from identity.sso.sessions import SsoService
            from identity.sso.store import InMemoryStore
            from identity.sso.keystore import KeyStore
        except Exception as exc:  # noqa: BLE001 - surface a clear boot error
            raise RuntimeError(
                "portal SSO requires the merged identity/sso lane (issue #35); "
                f"import failed: {exc}"
            ) from exc

        if console_signing_key is None:
            from cryptography.hazmat.primitives.asymmetric import rsa

            console_signing_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            )
        self._store = InMemoryStore()
        self._keystore = KeyStore()
        self._service = SsoService(
            self._store,
            self._keystore,
            session_hmac_key=session_hmac_key,
            relay_hmac_key=relay_hmac_key or session_hmac_key,
            console_signing_key=console_signing_key,
            root_admin_emails=root_admin_emails,
            allowlist_only=allowlist_only,
        )
        self._root_admin_emails = tuple(root_admin_emails)
        self._allowlist_only = bool(allowlist_only)
        #: Console jti revocation set (a revoked console session is refused
        #: even while unexpired — issue #35 logout semantics).
        self._revoked: set[str] = set()

    # -- allowlist ----------------------------------------------------------
    def allowlist_role(self, email: str) -> str:
        """root_admin for allowlisted emails, else ``user`` (denied when
        allowlist_only). Consumes identity/sso tokens.allowlist_decision."""
        from identity.sso.tokens import allowlist_decision

        role, allowed = allowlist_decision(
            email, root_admin_emails=self._root_admin_emails,
            allowlist_only=self._allowlist_only,
        )
        if not allowed:
            raise PermissionError(f"email {email!r} is not allowlisted")
        return role

    def is_root_admin(self, email: str) -> bool:
        return email.strip().lower() in {
            item.strip().lower() for item in self._root_admin_emails
        }

    # -- console flow -------------------------------------------------------
    def relay_state(self, callback_url: str = "https://console.local/cb") -> dict[str, str]:
        return self._service.console_relay_state(callback_url)

    def login(self, state: str, email: str, tenant_id: str) -> dict[str, Any]:
        """Complete the console login and return the issued session."""
        session = self._service.complete_console_login(
            state, identity_email=email, tenant_id=tenant_id
        )
        return {
            "token": session.token,
            "tenantId": session.tenant_id,
            "email": session.subject_id,
            "role": session.role,
            "sessionId": session.session_id,
        }

    def verify(self, token: str) -> dict[str, Any]:
        """Verify an os-session-token and return its claims (fail closed)."""
        claims = self._service.verify_console(token)
        if self.is_revoked(str(claims.get("jti", ""))):
            from identity.sso.errors import SessionRevokedError

            raise SessionRevokedError(
                f"console session {claims.get('jti')} has been revoked"
            )
        return claims

    def revoke(self, jti: str) -> None:
        """Revoke a console session by its jti (logout)."""
        if jti:
            self._revoked.add(jti)

    def is_revoked(self, jti: str) -> bool:
        return jti in self._revoked

    def logout(self, token: str) -> str:
        """Verify then revoke a console session token."""
        claims = self.verify(token)
        jti = str(claims.get("jti", ""))
        self.revoke(jti)
        return jti

    def jwks(self) -> dict[str, Any]:
        return self._service.console_jwks_payload()
