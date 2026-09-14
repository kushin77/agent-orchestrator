"""Shared pytest fixtures for the portal console suite (issues #39, #272).

Bootstraps the repo root so the portal server can import the merged
``identity/sso`` lane (issue #35) and exercises the console through an
in-process API client over ``ConsoleApplication.handle`` (no sockets).

AuthN is the shared-frontend contract (issue #272): the console has no login of
its own, so the suite plays the **auth gate**. :class:`FakeAuthGate` holds the
RS256 signing key and publishes the JWKS payload the gate serves at
``GET /auth/.well-known/jwks.json``; the console under test holds only that
public mirror and verifies tokens offline, exactly as it does in production.
``ApiClient.authenticate`` mints a genuine ``os-session-token`` and presents it
as the session cookie — the only session path the console has.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from identity.sso.jose import (  # noqa: E402
    generate_rsa_keypair,
    jwks_for_keys,
    jwt_encode,
)
from identity.sso.model import CONSOLE_AUDIENCE, CONSOLE_TOKEN_PURPOSE  # noqa: E402
from identity.sso.tokens import (  # noqa: E402
    console_kid_for,
    issue_console_session_token,
)
from portal.server.app import build_app  # noqa: E402
from portal.server.sso import SESSION_COOKIE, ConsoleSso  # noqa: E402

#: The ROOT_ADMIN_EMAILS allowlist the console under test is configured with.
ROOT_ADMIN_EMAILS = ("root@platform.example.com",)

#: The console issuer string (identity/sso tokens.CONSOLE_ISSUER).
GATE_ISSUER = "urn:agent-orchestrator:console"

#: A non-allowlisted, org-bound identity (scoped user, never super-admin).
SCOPED_USER_EMAIL = "alice@acme.example.com"


class FakeAuthGate:
    """Offline stand-in for the shared-frontend OS auth gate."""

    def __init__(self) -> None:
        self._private_key, self._public_key = generate_rsa_keypair()
        self.kid = console_kid_for(self._public_key)
        #: what the gate publishes at GET /auth/.well-known/jwks.json
        self.jwks = jwks_for_keys([(self.kid, self._public_key)])

    def mint(
        self,
        email: str,
        tenant_id: str,
        *,
        role: str = "user",
        name: str = "",
        now: int | None = None,
        ttl: int = 3600,
    ) -> str:
        """A genuine ``os-session-token`` for ``email`` (the happy path)."""
        token, _ = issue_console_session_token(
            self._private_key,
            kid=self.kid,
            tenant_id=tenant_id,
            subject_id=email,
            email=email,
            name=name or email.split("@", 1)[0],
            role=role,
            now=int(now if now is not None else time.time()),
            ttl=ttl,
        )
        return token

    def claims(
        self, email: str, tenant_id: str, *, purpose: str = CONSOLE_TOKEN_PURPOSE
    ) -> dict:
        """The claim set of a console token, parameterised by purpose."""
        now = int(time.time())
        return {
            "iss": GATE_ISSUER,
            "sub": email,
            "aud": list(CONSOLE_AUDIENCE),
            "iat": now,
            "exp": now + 600,
            "jti": f"jit_{uuid.uuid4().hex}",
            "purpose": purpose,
            "tenantId": tenant_id,
            "email": email,
            "name": email.split("@", 1)[0],
            "role": "user",
        }

    def sign(self, claims: dict, *, key=None, kid: str | None = None) -> str:
        """RS256-sign ``claims``, defaulting to this gate's key and kid.

        ``key``/``kid`` are overridable so a test can build a token the console
        must refuse: another key under this kid, or this key under a kid the
        published JWKS does not carry.
        """
        return jwt_encode(
            claims,
            alg="RS256",
            key=key if key is not None else self._private_key,
            kid=self.kid if kid is None else kid,
        )


#: The single gate the suite's consoles are configured against.
AUTH_GATE = FakeAuthGate()


def console_sso(**kwargs) -> ConsoleSso:
    """The console verifier, holding only the gate's published JWKS mirror."""
    return ConsoleSso(jwks=AUTH_GATE.jwks, root_admin_emails=ROOT_ADMIN_EMAILS, **kwargs)


class ApiClient:
    """In-process console client with a cookie jar."""

    def __init__(self, app) -> None:
        self.app = app
        self.cookies: dict[str, str] = {}
        self.last_headers: list[tuple[str, str]] = []

    def _request(self, method: str, path: str, query=None, body=None):
        response = self.app.handle(
            method, path, query=query or {}, body=body or {}, cookies=self.cookies
        )
        self.last_headers = list(response.headers)
        for name, value in response.headers:
            if name.lower() != "set-cookie":
                continue
            pair, _, rest = value.partition(";")
            cookie_name, _, cookie_value = pair.partition("=")
            if "Max-Age=0" in rest or not cookie_value:
                self.cookies.pop(cookie_name.strip(), None)
            else:
                self.cookies[cookie_name.strip()] = cookie_value
        raw = response.as_bytes()
        payload = None
        if response.is_json:
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                payload = raw.decode("utf-8", "replace")
        else:
            payload = raw
        return response.status, payload

    def get(self, path: str, query=None):
        return self._request("GET", path, query=query)

    def post(self, path: str, body=None):
        return self._request("POST", path, body=body)

    def header(self, name: str) -> str:
        """The last response's value for ``name`` (e.g. ``Location``)."""
        for header_name, value in self.last_headers:
            if header_name.lower() == name.lower():
                return value
        return ""

    def authenticate(self, email: str, tenant_id: str, *, role: str = "user") -> None:
        """Present a genuine auth-gate os-session-token (the only session path)."""
        self.cookies[SESSION_COOKIE] = AUTH_GATE.mint(email, tenant_id, role=role)

    def present(self, token: str) -> None:
        """Present an arbitrary token as the session cookie (negative tests)."""
        self.cookies[SESSION_COOKIE] = token


@pytest.fixture
def app():
    return build_app(sso=console_sso())


@pytest.fixture
def client(app):
    return ApiClient(app)


@pytest.fixture
def super_client(app):
    api = ApiClient(app)
    api.authenticate("root@platform.example.com", "acme")
    return api


def login_as(app, email: str, tenant_id: str, *, role: str = "user") -> ApiClient:
    api = ApiClient(app)
    api.authenticate(email, tenant_id, role=role)
    return api


@pytest.fixture(autouse=True)
def isolate_control_rails(tmp_path, monkeypatch):
    """No portal test may write the repository's own control rail.

    RC-4 (#555) wired the audited ledger (``portal/server/control_audit.py``) in
    as the control surface's default collaborator. Before that the default was
    an in-memory guard, so a test that applied a mutating verb wrote nothing;
    now such a command appends to a real rail, and without this fixture every
    run that exercises one writes
    ``<repo>/.portal/control/ledger/<tenant>.jsonl`` — dirtying the working tree
    and leaking one test's records into the next.

    The rails are pointed at this test's own ``tmp_path`` for the whole suite. A
    test that wants a specific rail still overrides these (see
    ``test_control_audit.py``'s ``rails`` fixture); this removes only the
    possibility of silently using the repository's own.
    """
    from portal.server.control_audit import LEDGER_DIR_ENV, SLOG_ENV

    monkeypatch.setenv(LEDGER_DIR_ENV, str(tmp_path / "control-ledger"))
    monkeypatch.setenv(SLOG_ENV, str(tmp_path / "fleet" / "slog.jsonl"))
