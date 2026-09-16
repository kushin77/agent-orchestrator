"""e2e/erp/gate.py — an offline console session, minted locally.

The ERP module's surface is served by the console (ERP-07), and the console
authenticates every read behind its own session token. Proving the module end to end
therefore needs a session — and the honest way to get one offline is to play the auth
gate's role the way the console expects: hold a signing key locally, publish the
matching JWKS to the console verifier, and hand the console a token it will verify.

This is the e2e harness's own established pattern, not a new one: ``e2e/wiring.py``
and ``e2e/workbook11_portal.py`` do exactly this for the golden path's tenants, and
``portal/tests/conftest.py``'s ``FakeAuthGate`` is its portal-side twin. It is repeated
here rather than imported because those are a sibling stage's private helpers and a
suite's conftest, and a stage importing another stage's underscore function would make
one lane's refactor break another's proof.

What this file deliberately does NOT do: reach a network, read a credential, or stub a
verifier. The console verifies a real RS256 token against a real JWKS with the same code
path it uses in production; only the key's *provenance* differs (it is generated here,
in-process, and never leaves it).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

#: The tenant the harness's staged tenants run under.
DEFAULT_TENANT = "acme"

#: The console's root-admin e-mail. The console admits it because the verifier is built
#: with it in ``root_admin_emails``; nothing else about the address is significant.
ROOT_ADMIN_EMAIL = "root@platform.example.com"


@dataclass(frozen=True)
class ConsoleSession:
    """A minted console session: the cookies to present, and the verifier to build.

    ``cookies`` is what a request presents; ``sso()`` builds the console's own verifier
    over the published JWKS. Keeping both on one value is the point — a session that
    minted a token the verifier would reject is a failure this type makes impossible to
    express by accident.
    """

    tenant: str
    subject: str
    role: str
    kid: str
    cookies: Mapping[str, str]
    _jwks: Mapping[str, Any]
    _root_admin_emails: Tuple[str, ...]

    def sso(self) -> Any:
        """The console verifier holding only this session's published JWKS."""
        from portal.server.sso import ConsoleSso

        return ConsoleSso(jwks=dict(self._jwks), root_admin_emails=self._root_admin_emails)

    def headers(self) -> Dict[str, str]:
        """The request headers carrying the session cookie."""
        return {"cookie": "; ".join(f"{name}={value}" for name, value in sorted(self.cookies.items()))}


def mint_session(
    *,
    tenant: str = DEFAULT_TENANT,
    subject: str = ROOT_ADMIN_EMAIL,
    role: str = "admin",
    ttl: int = 3600,
) -> ConsoleSession:
    """Mint a console session for ``tenant`` offline, from a freshly generated key."""
    from identity.sso.jose import generate_rsa_keypair, jwks_for_keys
    from identity.sso.tokens import console_kid_for, issue_console_session_token
    from portal.server.sso import SESSION_COOKIE

    private_key, public_key = generate_rsa_keypair()
    kid = console_kid_for(public_key)
    token, _claims = issue_console_session_token(
        private_key,
        kid=kid,
        tenant_id=tenant,
        subject_id=subject,
        email=subject,
        name=subject.split("@", 1)[0],
        role=role,
        now=int(time.time()),
        ttl=ttl,
    )
    return ConsoleSession(
        tenant=tenant,
        subject=subject,
        role=role,
        kid=kid,
        cookies={SESSION_COOKIE: token},
        _jwks=jwks_for_keys([(kid, public_key)]),
        _root_admin_emails=(ROOT_ADMIN_EMAIL,),
    )
