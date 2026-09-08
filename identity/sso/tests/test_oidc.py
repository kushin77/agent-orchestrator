"""OIDC id_token verification tests (issue #35): signature, issuer/audience,
time window, nonce, and the negative battery - tampered, forged, alg-confused,
expired, wrong-issuer/audience, mismatched-nonce tokens are all denied.
"""

import pytest

from identity.sso.errors import (
    InvalidIdTokenError,
    InvalidNonceError,
    LoginDeniedError,
)
from identity.sso.model import ALG_HS256, OIDC, SsoSession


def _acme_service(env):
    env.register(OIDC, "acme", domain="acme.example.com")
    return env.service()


def test_valid_id_token_yields_scoped_session(env, now):
    svc = _acme_service(env)
    token = env.id_token("acme", nonce="n-123")
    session = svc.complete_oidc(
        "acme.example.com", token, nonce="n-123", now=now
    )
    assert isinstance(session, SsoSession)
    assert session.tenant_id == "acme"
    assert session.subject_id == "alice@acme.example.com"
    claims = svc.verify_session(session.token, expected_tenant="acme", now=now)
    assert claims["tenantId"] == "acme"
    assert claims["sub"] == "alice@acme.example.com"


def test_tampered_id_token_rejected(env, now):
    svc = _acme_service(env)
    token = env.id_token("acme")
    header, payload, signature = token.split(".")
    import base64
    import json

    def b64url_decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    claims = json.loads(b64url_decode(payload))
    claims["email"] = "mallory@evil.example.net"
    forged_payload = (
        base64.urlsafe_b64encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    tampered = f"{header}.{forged_payload}.{signature}"
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", tampered, now=now)


def test_forged_id_token_with_wrong_key_rejected(env, now):
    svc = _acme_service(env)
    env.register(OIDC, "othercorp", domain="othercorp.example.com")
    wrong_key = env.idp_keys[(OIDC, "othercorp")].private_key
    forged = env.id_token("acme", key_override=wrong_key)
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", forged, now=now)


def test_wrong_issuer_rejected(env, now):
    svc = _acme_service(env)
    token = env.id_token(
        "acme", iss="https://issuer.othercorp.example.com"
    )
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", token, now=now)


def test_wrong_audience_rejected(env, now):
    svc = _acme_service(env)
    token = env.id_token("acme", aud="someone-elses-client")
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", token, now=now)


def test_expired_id_token_rejected(env, now):
    svc = _acme_service(env)
    token = env.id_token("acme", iat=now - 7200, exp=now - 3600)
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", token, now=now)


def test_not_yet_valid_id_token_rejected(env, now):
    svc = _acme_service(env)
    token = env.id_token(
        "acme", iat=now + 3600, exp=now + 7200,
        extra={"nbf": now + 3600},
    )
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", token, now=now)


def test_nonce_mismatch_rejected(env, now):
    svc = _acme_service(env)
    token = env.id_token("acme", nonce="expected-nonce")
    with pytest.raises(InvalidNonceError):
        svc.complete_oidc(
            "acme.example.com", token, nonce="attacker-nonce", now=now
        )


def test_missing_email_claim_cannot_map_to_user(env, now):
    svc = _acme_service(env)
    token = env.id_token("acme", email="")
    with pytest.raises(LoginDeniedError):
        svc.complete_oidc("acme.example.com", token, now=now)


def test_alg_confusion_hs256_token_rejected_by_rs256_config(env, now):
    svc = _acme_service(env)
    # Signed with the client secret as HS256, but the config pins RS256.
    hs256_token = env.id_token(
        "acme", alg=ALG_HS256, key_override=bytes(range(32))
    )
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", hs256_token, now=now)


def test_hs256_client_secret_flow_verifies(env, now):
    secret = bytes(range(32))
    env.register(
        OIDC,
        "acme",
        domain="acme.example.com",
        cert_alias="",
        secret_alias="sec:acme-oidc",
    )
    env.keystore.put_secret("sec:acme-oidc", secret)
    svc = env.service()
    token = env.id_token(
        "acme", alg=ALG_HS256, key_override=secret
    )
    session = svc.complete_oidc("acme.example.com", token, now=now)
    assert session.tenant_id == "acme"


def test_oidc_from_other_issuer_tenant_cannot_login(env, now):
    svc = _acme_service(env)
    env.register(OIDC, "othercorp", domain="othercorp.example.com")
    other_token = env.id_token("othercorp")
    with pytest.raises(InvalidIdTokenError):
        svc.complete_oidc("acme.example.com", other_token, now=now)


def test_authorization_url_carries_client_id_state_and_pkce(env):
    env.register(OIDC, "acme", domain="acme.example.com")
    svc = env.service()
    url = svc.start_oidc(
        "acme", state="state-xyz", nonce="nonce-abc",
        code_challenge="challenge-1",
    )
    assert url.startswith("https://issuer.acme.example.com/authorize?")
    assert "client_id=acme-client" in url
    assert "state=state-xyz" in url
    assert "nonce=nonce-abc" in url
    assert "code_challenge=challenge-1" in url
    assert "code_challenge_method=S256" in url
