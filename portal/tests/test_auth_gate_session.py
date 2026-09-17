"""Auth-gate session contract tests (issue #272).

The portal must establish its console session **only** from a verified
shared-frontend auth-gate ``os-session-token``: RS256, kid-indexed against the
gate's published JWKS, ``purpose: os-session-token``, fail closed. There is no
private login left to bypass it, so every negative below is either a credential
the removed login used to accept or a sibling token kind (session-cookie JWT,
OAuth ``state`` JWT) replayed as a module token — all of them must be refused
with a 401, and the removed routes must be gone.
"""

from __future__ import annotations

import time

import pytest
from conftest import (
    AUTH_GATE,
    ROOT_ADMIN_EMAILS,
    SCOPED_USER_EMAIL,
    ApiClient,
    console_sso,
    login_as,
)

from identity.sso.jose import generate_rsa_keypair
from identity.sso.model import API_SESSION_PURPOSE, CONSOLE_TOKEN_PURPOSE
from identity.sso.tokens import encode_relay_state, issue_session_token
from portal.server.app import build_app
from portal.server.sso import JWKS_ENV, JWKS_FILE_ENV, ConsoleSso

SESSION_HMAC_KEY = b"portal-test-session-hmac-key-0123456789"


def _presenting(app, token: str) -> ApiClient:
    """A console client whose session cookie is ``token`` (unverified input)."""
    api = ApiClient(app)
    api.present(token)
    return api


def _assert_unauthorized(api: ApiClient) -> None:
    status, payload = api.get("/api/tenants")
    assert status == 401, payload
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unauthorized"


# -- the happy path: a real auth-gate session token --------------------------

def test_real_os_session_token_establishes_the_console_session(app):
    api = login_as(app, SCOPED_USER_EMAIL, "acme")
    status, payload = api.get("/api/console/me")
    assert status == 200
    me = payload["data"]
    assert me["email"] == SCOPED_USER_EMAIL
    assert me["superAdmin"] is False
    assert me["scopedTenants"] == ["acme"]


def test_verified_session_opens_the_shell(app):
    api = login_as(app, SCOPED_USER_EMAIL, "acme")
    status, _ = api.get("/")
    assert status == 302
    assert api.header("Location") == "/views/shell.html"


def test_gate_accurate_token_without_tenant_claim_is_accepted(app):
    """The **real** shared-frontend gate mints the ``os-session-token``
    tenant-agnostic — ``{purpose, sub, email, name, role}`` and no ``tenantId``
    (one gate serves every portal origin; the consumer maps tenants from its own
    org directory, never the token).

    This is the regression guard for the live redirect loop: the console once
    hard-required a ``tenantId`` claim the gate never sends, so a genuine gate
    token was refused and the browser bounced ``/`` → ``/auth/login`` → ``/``
    forever. The token is minted here in the gate's exact shape (no tenant) and
    the console must accept it and open the shell.
    """
    now = int(time.time())
    token = AUTH_GATE.sign(
        {
            "purpose": CONSOLE_TOKEN_PURPOSE,
            "sub": SCOPED_USER_EMAIL,
            "email": SCOPED_USER_EMAIL,
            "name": "Alice",
            "role": "user",
            "iat": now,
            "exp": now + 600,
            # deliberately NO tenantId — the shared-frontend gate has none.
        }
    )
    api = _presenting(app, token)
    status, payload = api.get("/api/console/me")
    assert status == 200, payload
    assert payload["data"]["email"] == SCOPED_USER_EMAIL

    # ...and the same gate-shaped token opens the shell (the loop's exit).
    status, _ = api.get("/")
    assert status == 302
    assert api.header("Location") == "/views/shell.html"


# -- the front door: unauthenticated document requests -> the OS auth gate ----

def test_unauthenticated_document_request_redirects_to_the_os_gate(client):
    status, payload = client.get("/")
    assert status == 302
    assert payload == b""
    assert client.header("Location") == "/auth/login"


def test_refused_session_still_lands_on_the_os_gate(app):
    """A garbage cookie is not a login attempt — it reaches the gate too."""
    api = _presenting(app, "not.a.jwt")
    status, _ = api.get("/")
    assert status == 302
    assert api.header("Location") == "/auth/login"


# -- the private login is gone ------------------------------------------------

def test_unauthenticated_login_route_probe_reveals_nothing(client):
    """The removed route is unreachable unauthenticated (401, not a login)."""
    status, payload = client.post(
        "/api/console/login",
        {"email": SCOPED_USER_EMAIL, "tenantId": "acme", "state": "x"},
    )
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_removed_login_routes_are_404(super_client):
    """No login, relay-state or self-published JWKS route remains (404)."""
    status, payload = super_client.post(
        "/api/console/login",
        {"email": SCOPED_USER_EMAIL, "tenantId": "acme", "state": "x"},
    )
    assert status == 404
    assert payload["error"]["code"] == "not_found"
    for path in ("/api/console/relay-state", "/api/console/jwks"):
        status, payload = super_client.get(path)
        assert status == 404, path
        assert payload["error"]["code"] == "not_found"


def test_login_view_renders_no_credential_form(app):
    """The login view is a pure redirect: no form, no inputs, no demo ids."""
    api = ApiClient(app)
    status, html = api.get("/views/login.html")
    assert status == 200
    text = html.decode("utf-8")
    assert "<form" not in text
    assert "<input" not in text
    assert "<select" not in text
    assert "/auth/login" in text
    # the removed demo identities must not survive anywhere in the page
    for demo_email in (
        "root@platform.example.com",
        "alice@acme.example.com",
        "carol@globex.example.com",
    ):
        assert demo_email not in text


# -- fail-closed negatives ----------------------------------------------------

def test_missing_session_cookie_is_refused(client):
    _assert_unauthorized(client)


def test_expired_os_session_token_is_refused(app):
    token = AUTH_GATE.mint(
        SCOPED_USER_EMAIL, "acme", now=int(time.time()) - 7200, ttl=60
    )
    _assert_unauthorized(_presenting(app, token))


def test_forged_token_signed_by_another_key_is_refused(app):
    """A token signed by a key the published JWKS never carried."""
    attacker_key, _ = generate_rsa_keypair()
    forged = AUTH_GATE.sign(
        AUTH_GATE.claims(SCOPED_USER_EMAIL, "acme"), key=attacker_key
    )
    _assert_unauthorized(_presenting(app, forged))


def test_token_with_unknown_kid_is_refused(app):
    """The right key under a kid the console does not trust is refused."""
    rotated = AUTH_GATE.sign(
        AUTH_GATE.claims(SCOPED_USER_EMAIL, "acme"), kid="gate-key-not-published"
    )
    _assert_unauthorized(_presenting(app, rotated))


def test_session_cookie_jwt_replayed_as_a_module_token_is_refused(app):
    """The HS256 session-cookie JWT (API session purpose) is not a module token."""
    token, _ = issue_session_token(
        SESSION_HMAC_KEY,
        tenant_id="acme",
        subject_id=SCOPED_USER_EMAIL,
        subject_type="user",
        role="owner",
        email=SCOPED_USER_EMAIL,
        now=int(time.time()),
        purpose=API_SESSION_PURPOSE,
    )
    _assert_unauthorized(_presenting(app, token))


def test_oauth_state_jwt_replayed_as_a_module_token_is_refused(app):
    """The auth-gate OAuth ``state`` JWT (HS256 relay state) is not a session."""
    state_token = encode_relay_state(
        SESSION_HMAC_KEY,
        backend_callback_url="/cb",
        code_verifier="v" * 43,
        now=int(time.time()),
    )
    _assert_unauthorized(_presenting(app, state_token))


@pytest.mark.parametrize("purpose", [API_SESSION_PURPOSE, "auth-hub-relay-state"])
def test_rs256_token_with_the_wrong_purpose_is_refused(app, purpose):
    """Validly signed by the gate's own key, wrong purpose — still refused."""
    token = AUTH_GATE.sign(AUTH_GATE.claims(SCOPED_USER_EMAIL, "acme", purpose=purpose))
    _assert_unauthorized(_presenting(app, token))


def test_allowlist_only_console_refuses_a_verified_non_allowlisted_identity(
):
    """In allowlist-only mode a verified token for a non-allowlisted email is
    refused: the gate's allowlist is enforced again at the module boundary."""
    app = build_app(sso=console_sso(allowlist_only=True))
    api = _presenting(app, AUTH_GATE.mint(SCOPED_USER_EMAIL, "acme"))
    _assert_unauthorized(api)


def test_console_without_a_jwks_mirror_refuses_every_session(monkeypatch):
    """No configured JWKS means no trusted key — so no session, ever."""
    monkeypatch.delenv(JWKS_ENV, raising=False)
    monkeypatch.delenv(JWKS_FILE_ENV, raising=False)
    app = build_app(sso=ConsoleSso(root_admin_emails=ROOT_ADMIN_EMAILS))
    _assert_unauthorized(_presenting(app, AUTH_GATE.mint(SCOPED_USER_EMAIL, "acme")))


# -- RBAC integrity -----------------------------------------------------------

def test_super_admin_comes_from_the_allowlist_not_the_token_role(app):
    """A token cannot promote itself: the local allowlist alone decides."""
    allowlisted = login_as(app, ROOT_ADMIN_EMAILS[0], "acme", role="user")
    status, payload = allowlisted.get("/api/console/me")
    assert status == 200
    assert payload["data"]["superAdmin"] is True

    self_promoted = login_as(app, SCOPED_USER_EMAIL, "acme", role="root_admin")
    status, payload = self_promoted.get("/api/console/me")
    assert status == 200
    me = payload["data"]
    assert me["superAdmin"] is False
    assert me["scopedTenants"] == ["acme"]
    # ...and the promotion attempt grants no cross-tenant reach.
    status, payload = self_promoted.get("/api/tenants/globex/agents")
    assert status == 403
    assert payload["error"]["code"] == "scope_denied"


def test_verified_identity_with_no_org_binding_is_scope_denied(app):
    """Authentication is not authorization: an unbound verified user sees none
    of the tenants (fail closed, no default tenant)."""
    api = login_as(app, "stranger@elsewhere.example.com", "acme")
    status, payload = api.get("/api/tenants")
    assert status == 200
    assert payload["data"]["tenants"] == []
    status, payload = api.get("/api/tenants/acme/agents")
    assert status == 403
    assert payload["error"]["code"] == "scope_denied"


def test_console_token_purpose_constant_matches_the_shared_contract():
    """Pin the purpose string the module verifies (shared-frontend contract)."""
    assert CONSOLE_TOKEN_PURPOSE == "os-session-token"
