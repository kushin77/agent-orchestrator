"""portal.server.sso — console session verification against the OS auth gate.

---knowledge---
module_id: portal.server.sso
system: portal
app: server
solution_class: enterprise
patterns: [session-verification, no-own-login]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [ConsoleAuthError, ConsoleIdentity, trusted_keys_from_jwks, load_auth_gate_jwks, configured_root_admin_emails, ConsoleSso]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

The console has **no login of its own**. The shared-frontend ``auth/`` gate
("one front door") runs Google OAuth and mints the RS256 ``os-session-token``
(``purpose: os-session-token``, ``kid`` = RFC 7638 thumbprint, published at
``GET /auth/.well-known/jwks.json``); the OS shell hands that token to a framed
module over the ``os:session`` bridge. This module is the module half of that
contract: the portal **verifies** the token offline against a configured mirror
of the auth-gate JWKS and fails closed on every error. It mints nothing and
re-implements no JOSE.

Consumed read-only from the merged ``identity/sso`` lane (issue #35):

* ``identity.sso.tokens.verify_console_session_token`` — RS256 signature
  kid-indexed against the published JWKS, expiry, and the
  ``purpose: os-session-token`` check;
* ``identity.sso.tokens.allowlist_decision`` — the ``ROOT_ADMIN_EMAILS``
  allowlist.

RBAC is never taken from the token: the local allowlist decides super-admin,
and a scoped user's tenant roles come from the org directory (``authz``), so a
token can never promote itself.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

# Repo root bootstrap: identity/ is a PEP-420 namespace under the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Session cookie name — the auth gate's os-session-token (HttpOnly).
SESSION_COOKIE = "os-session-token"

#: The only console token purpose accepted (shared-frontend module contract).
CONSOLE_TOKEN_PURPOSE = "os-session-token"

#: The OS auth gate's login path: the console's only front door.
AUTH_GATE_LOGIN_PATH = "/auth/login"

#: Env carrying the mirrored auth-gate JWKS: inline JSON payload, or file path.
JWKS_ENV = "PORTAL_AUTH_GATE_JWKS"
JWKS_FILE_ENV = "PORTAL_AUTH_GATE_JWKS_FILE"

#: Env carrying the ROOT_ADMIN_EMAILS allowlist (comma-separated).
ROOT_ADMIN_ENV = "ROOT_ADMIN_EMAILS"

#: The super-admin role name (identity/sso ``CONSOLE_ALLOWLIST_ROLE``).
ROOT_ADMIN_ROLE = "root_admin"

#: No baked-in super-admin: the allowlist is configured (env) or empty.
DEFAULT_ROOT_ADMIN_EMAILS: tuple[str, ...] = ()


class ConsoleAuthError(Exception):
    """A refused console session — every verification failure lands here."""


@dataclass(frozen=True)
class ConsoleIdentity:
    """The verified auth-gate identity behind a console session."""

    email: str
    name: str
    subject_id: str
    tenant_id: str
    role: str
    super_admin: bool


def trusted_keys_from_jwks(jwks: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Map a published JWKS payload to its ``kid``-indexed RSA public keys.

    Entries that are not usable RSA signing keys are dropped rather than
    guessed at, so a malformed mirror narrows the trust set instead of
    widening it; an empty result trusts no key at all, and every token is
    then refused (fail closed).
    """
    keys: dict[str, Any] = {}
    if not isinstance(jwks, Mapping):
        return keys
    from identity.sso.jose import public_key_from_jwk

    for entry in jwks.get("keys") or ():
        if not isinstance(entry, Mapping):
            continue
        kid = str(entry.get("kid") or "")
        if not kid or entry.get("kty") != "RSA" or not entry.get("n") or not entry.get("e"):
            continue
        try:
            keys[kid] = public_key_from_jwk(dict(entry))
        except Exception:  # noqa: BLE001 - an unusable key is simply not trusted
            continue
    return keys


def load_auth_gate_jwks(
    jwks: Optional[Mapping[str, Any]] = None,
) -> Optional[Mapping[str, Any]]:
    """The auth-gate JWKS mirror: the explicit argument, else the environment.

    ``PORTAL_AUTH_GATE_JWKS`` carries the payload inline (the auth gate's
    ``GET /auth/.well-known/jwks.json`` body) and ``PORTAL_AUTH_GATE_JWKS_FILE``
    points at a mounted copy. A malformed configuration raises at boot rather
    than degrading into a silently empty trust set.
    """
    if jwks is not None:
        return jwks
    inline = (os.environ.get(JWKS_ENV) or "").strip()
    if inline:
        try:
            return json.loads(inline)
        except ValueError as exc:
            raise ConsoleAuthError(f"{JWKS_ENV} is not valid JSON: {exc}") from exc
    path = (os.environ.get(JWKS_FILE_ENV) or "").strip()
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConsoleAuthError(f"{JWKS_FILE_ENV}={path} cannot be read: {exc}") from exc
    except ValueError as exc:
        raise ConsoleAuthError(
            f"{JWKS_FILE_ENV}={path} is not valid JSON: {exc}"
        ) from exc


def configured_root_admin_emails() -> tuple[str, ...]:
    """The ROOT_ADMIN_EMAILS allowlist from the environment (may be empty)."""
    raw = os.environ.get(ROOT_ADMIN_ENV, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class ConsoleSso:
    """Verifies auth-gate ``os-session-token``s; it issues nothing.

    ``jwks`` is the auth-gate JWKS mirror (offline); when omitted it is read
    from ``PORTAL_AUTH_GATE_JWKS`` / ``PORTAL_AUTH_GATE_JWKS_FILE``. With no
    mirror configured every session is refused (fail closed). ``revoke`` is a
    process-local deny list for logout — the auth gate owns real revocation.
    """

    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        jwks: Optional[Mapping[str, Any]] = None,
        root_admin_emails: Optional[tuple[str, ...]] = None,
        allowlist_only: bool = False,
    ) -> None:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            import identity.sso.tokens  # noqa: F401 - boot-time availability probe
        except Exception as exc:  # noqa: BLE001 - surface a clear boot error
            raise RuntimeError(
                "portal SSO requires the merged identity/sso lane (issue #35); "
                f"import failed: {exc}"
            ) from exc

        self.jwks = load_auth_gate_jwks(jwks)
        self.trusted_keys = trusted_keys_from_jwks(self.jwks)
        self._root_admin_emails = (
            tuple(root_admin_emails)
            if root_admin_emails is not None
            else configured_root_admin_emails()
        )
        self._allowlist_only = bool(allowlist_only)
        #: Process-local console jti deny list (logout); the auth gate owns
        #: the authoritative revocation of the tokens it mints.
        self._revoked: set[str] = set()

    # -- allowlist (ROOT_ADMIN_EMAILS decides super-admin, never the token) --
    def role_for(self, email: str) -> str:
        """root_admin for allowlisted emails, else ``user`` (denied when
        allowlist_only). Consumes identity/sso tokens.allowlist_decision."""
        from identity.sso.tokens import allowlist_decision

        role, allowed = allowlist_decision(
            email,
            root_admin_emails=self._root_admin_emails,
            allowlist_only=self._allowlist_only,
        )
        if not allowed:
            raise PermissionError(f"email {email!r} is not allowlisted")
        return role

    def is_root_admin(self, email: str) -> bool:
        return email.strip().lower() in {
            item.strip().lower() for item in self._root_admin_emails
        }

    # -- session verification (offline JWKS; fail closed) --------------------
    def verify(self, token: str, *, now: Optional[int] = None) -> dict[str, Any]:
        """Verify an auth-gate ``os-session-token`` and return its claims.

        The consumed identity/sso verifier enforces the RS256 signature by
        ``kid`` against the published JWKS, expiry, and ``purpose:
        os-session-token`` (so a session-cookie JWT or an OAuth ``state`` JWT
        replayed as a module token is refused); every error is re-raised as a
        :class:`ConsoleAuthError`.
        """
        if not token or not isinstance(token, str):
            raise ConsoleAuthError("missing console session token")
        if not self.trusted_keys:
            raise ConsoleAuthError(
                "no auth-gate JWKS is configured; refusing every session"
            )
        from identity.sso.tokens import verify_console_session_token

        now_i = int(now if now is not None else time.time())
        try:
            claims = verify_console_session_token(
                token, self.trusted_keys, now=now_i
            )
        except Exception as exc:  # noqa: BLE001 - any verification error is a refusal
            raise ConsoleAuthError(f"auth-gate session token refused: {exc}") from exc
        jti = str(claims.get("jti") or "")
        if jti and self.is_revoked(jti):
            raise ConsoleAuthError(f"console session {jti} has been revoked")
        return claims

    def identity_from_claims(self, claims: Mapping[str, Any]) -> ConsoleIdentity:
        """Map verified auth-gate claims onto the console identity.

        Identity is the token ``sub`` (display email from ``email``); the
        console role comes from the local ROOT_ADMIN allowlist — **never** from
        the token's own ``role`` claim.
        """
        subject_id = str(claims.get("sub") or "").strip()
        email = str(claims.get("email") or subject_id).strip().lower()
        if not email or not subject_id:
            raise ConsoleAuthError("console session carries no identity claim")
        try:
            role = self.role_for(email)
        except PermissionError as exc:
            raise ConsoleAuthError(str(exc)) from exc
        return ConsoleIdentity(
            email=email,
            name=str(claims.get("name") or email),
            subject_id=subject_id,
            tenant_id=str(claims.get("tenantId") or ""),
            role=role,
            super_admin=role == ROOT_ADMIN_ROLE,
        )

    # -- revocation (process-local logout deny list) -------------------------
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
