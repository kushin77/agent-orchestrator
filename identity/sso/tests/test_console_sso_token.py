"""AC #3 - console SSO model (shared-frontend port): auth-hub relay ``state``
JWT (PKCE in state), RS256 ``os-session-token`` + JWKS, allowlist. Everything
is modeled offline - no Google/IdP network calls.
"""

import base64
import json

import pytest

from identity.sso.errors import (
    InvalidSessionTokenError,
    LoginDeniedError,
    RelayStateError,
)
from identity.sso.jose import generate_rsa_keypair, rfc7638_thumbprint
from identity.sso.model import CONSOLE_TOKEN_PURPOSE
from identity.sso.tokens import (
    console_jwks,
    issue_console_session_token,
)


def _console_service(env, *, root_admins=("root@acme.example.com",),
                     allowlist_only=True, retain_old_key=False):
    current_priv, current_pub = generate_rsa_keypair()
    old_priv = old_pub = None
    verify = {}
    if retain_old_key:
        old_priv, old_pub = generate_rsa_keypair()
        verify[rfc7638_thumbprint(old_pub)] = old_pub
    svc = env.service(
        console_signing_key=current_priv,
        console_verify_public_keys=verify,
        root_admin_emails=root_admins,
        allowlist_only=allowlist_only,
    )
    svc._current_priv = current_priv
    svc._old_priv = old_priv
    return svc


def _payload_of(token: str) -> dict:
    payload = token.split(".")[1]
    padding = "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload + padding))


def test_relay_state_and_allowlisted_console_login(env, now):
    svc = _console_service(env)
    relay = svc.console_relay_state(
        "https://console.example.com/callback", now=now
    )
    assert "state" in relay and "code_verifier" in relay
    session = svc.complete_console_login(
        relay["state"],
        identity_email="root@acme.example.com",
        tenant_id="acme",
        now=now,
    )
    assert session.role == "root_admin"
    assert session.purpose == CONSOLE_TOKEN_PURPOSE
    claims = svc.verify_console(session.token, now=now)
    assert claims["purpose"] == "os-session-token"
    assert claims["tenantId"] == "acme"
    assert claims["role"] == "root_admin"


def test_non_allowlisted_email_denied_when_allowlist_only(env, now):
    svc = _console_service(env)
    relay = svc.console_relay_state(
        "https://console.example.com/callback", now=now
    )
    with pytest.raises(LoginDeniedError):
        svc.complete_console_login(
            relay["state"],
            identity_email="intruder@example.net",
            tenant_id="acme",
            now=now,
        )


def test_non_allowlisted_email_allowed_as_user_when_not_allowlist_only(env, now):
    svc = _console_service(env, allowlist_only=False)
    relay = svc.console_relay_state(
        "https://console.example.com/callback", now=now
    )
    session = svc.complete_console_login(
        relay["state"],
        identity_email="member@acme.example.com",
        tenant_id="acme",
        now=now,
    )
    assert session.role == "user"


def test_tampered_relay_state_rejected(env, now):
    svc = _console_service(env)
    relay = svc.console_relay_state(
        "https://console.example.com/callback", now=now
    )
    tampered = relay["state"] + "x"
    with pytest.raises(RelayStateError):
        svc.complete_console_login(
            tampered,
            identity_email="root@acme.example.com",
            tenant_id="acme",
            now=now,
        )


def test_expired_relay_state_rejected(env, now):
    from identity.sso.tokens import encode_relay_state

    svc = _console_service(env)
    expired_state = encode_relay_state(
        svc.relay_hmac_key,
        backend_callback_url="https://console.example.com/callback",
        code_verifier="verifier-123",
        now=now - 600,
        ttl=300,
    )
    with pytest.raises(RelayStateError):
        svc.complete_console_login(
            expired_state,
            identity_email="root@acme.example.com",
            tenant_id="acme",
            now=now,
        )


def test_console_token_verifies_via_jwks_payload(env, now):
    svc = _console_service(env)
    relay = svc.console_relay_state(
        "https://console.example.com/callback", now=now
    )
    session = svc.complete_console_login(
        relay["state"],
        identity_email="root@acme.example.com",
        tenant_id="acme",
        now=now,
    )
    jwks = svc.console_jwks_payload()
    assert jwks["keys"], "JWKS must list at least the current key"
    assert jwks["keys"][0]["alg"] == "RS256"
    assert jwks["keys"][0]["kty"] == "RSA"
    assert jwks["keys"][0]["use"] == "sig"
    kid = jwks["keys"][0]["kid"]
    claims = svc.verify_console(session.token, now=now)
    assert claims["sub"] == "root@acme.example.com"
    assert kid  # kid present for offline verification


def test_unknown_kid_console_token_rejected(env, now):
    svc = _console_service(env)
    other_priv, other_pub = generate_rsa_keypair()
    stranger_kid = rfc7638_thumbprint(other_pub)
    token, _ = issue_console_session_token(
        other_priv,
        kid=stranger_kid,
        tenant_id="acme",
        subject_id="root@acme.example.com",
        email="root@acme.example.com",
        name="Root",
        role="root_admin",
        now=now,
    )
    with pytest.raises(InvalidSessionTokenError):
        svc.verify_console(token, now=now)


def test_key_rollover_retained_key_still_verifies(env, now):
    """During a rollover window the JWKS lists current + retained keys and a
    token minted under the old key keeps verifying; an unknown kid is denied.
    """
    svc = _console_service(env, retain_old_key=True)
    jwks = console_jwks(
        [(kid, key) for kid, key in sorted(svc.console_verify.items())]
    )
    kids = {entry["kid"] for entry in jwks["keys"]}
    assert len(kids) >= 2  # current + retained

    old_kid = rfc7638_thumbprint(svc._old_priv.public_key())
    old_token, _ = issue_console_session_token(
        svc._old_priv,
        kid=old_kid,
        tenant_id="acme",
        subject_id="root@acme.example.com",
        email="root@acme.example.com",
        name="Root",
        role="root_admin",
        now=now,
    )
    claims = svc.verify_console(old_token, now=now)
    assert claims["sub"] == "root@acme.example.com"


def test_hs256_cookie_token_rejected_by_console_verifier(env, now):
    """The console verifier only accepts RS256 os-session-tokens; the HS256
    session cookie / OAuth state token must be rejected (alg + purpose)."""
    svc = _console_service(env)
    from identity.sso.jose import ALG_HS256, jwt_encode

    cookie = jwt_encode(
        {
            "iss": svc.__class__.__module__,
            "sub": "root@acme.example.com",
            "iat": now,
            "exp": now + 3600,
            "tenantId": "acme",
        },
        alg=ALG_HS256,
        key=svc.session_hmac_key,
    )
    with pytest.raises(InvalidSessionTokenError):
        svc.verify_console(cookie, now=now)


def test_console_token_claims_tenant_scoped(env, now):
    svc = _console_service(env)
    relay = svc.console_relay_state(
        "https://console.example.com/callback", now=now
    )
    session = svc.complete_console_login(
        relay["state"],
        identity_email="root@acme.example.com",
        tenant_id="acme",
        now=now,
    )
    claims = _payload_of(session.token)
    assert claims["tenantId"] == "acme"
    assert claims["purpose"] == "os-session-token"
