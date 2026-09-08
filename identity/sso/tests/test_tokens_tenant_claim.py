"""AC #2 - session tokens carry tenant (+subject/agent-or-user +role) claims
and the verifier rejects a missing/invalid tenant claim. Ports the registry
(issue #10) scoped-claims doctrine: a session is only ever usable inside the
one tenant it names - no cross-tenant fallback.
"""

import base64
import json

import pytest

from identity.sso.errors import (
    CrossTenantDenied,
    InvalidSessionTokenError,
    SsoError,
)
from identity.sso.model import SUBJECT_AGENT, SUBJECT_USER
from identity.sso.tokens import (
    issue_session_token,
    session_matches_tenant,
    verify_session_token,
)


def _claims_of(token: str) -> dict:
    payload = token.split(".")[1]
    padding = "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload + padding))


def test_issued_session_claims_carry_tenant_subject_and_role(env, now):
    token, claims = issue_session_token(
        env.session_hmac_key,
        tenant_id="acme",
        subject_id="alice@acme.example.com",
        subject_type=SUBJECT_USER,
        role="admin",
        email="alice@acme.example.com",
        now=now,
    )
    assert claims["tenantId"] == "acme"
    assert claims["subjectType"] == "user"
    assert claims["role"] == "admin"
    assert claims["sub"] == "alice@acme.example.com"
    assert claims["jti"]
    assert claims["exp"] == now + 3600
    verified = verify_session_token(
        token, env.session_hmac_key, now=now, require_tenant=True
    )
    assert verified["tenantId"] == "acme"
    assert session_matches_tenant(verified, "acme") is True
    assert session_matches_tenant(verified, "othercorp") is False


def test_agent_subject_session(env, now):
    token, claims = issue_session_token(
        env.session_hmac_key,
        tenant_id="acme",
        subject_id="worker-1",
        subject_type=SUBJECT_AGENT,
        role="agent",
        now=now,
    )
    assert claims["subjectType"] == "agent"


def test_cannot_issue_session_without_a_tenant(env, now):
    with pytest.raises(SsoError):
        issue_session_token(
            env.session_hmac_key,
            tenant_id="",
            subject_id="alice@acme.example.com",
            subject_type=SUBJECT_USER,
            role="admin",
            now=now,
        )


def test_session_without_tenant_claim_is_rejected(env, now):
    # Hand-craft a signed token that omits the tenantId claim entirely.
    from identity.sso.jose import ALG_HS256, jwt_encode

    claims = {
        "iss": "urn:agent-orchestrator:sso",
        "sub": "alice@acme.example.com",
        "aud": ["urn:agent-orchestrator:api"],
        "iat": now,
        "exp": now + 3600,
        "jti": "jit_noteenant",
        "purpose": "api-session",
    }
    token = jwt_encode(claims, alg=ALG_HS256, key=env.session_hmac_key)
    with pytest.raises(InvalidSessionTokenError) as excinfo:
        verify_session_token(
            token, env.session_hmac_key, now=now, require_tenant=True
        )
    from identity.sso.errors import MissingTenantClaimError

    assert isinstance(excinfo.value, MissingTenantClaimError)


def test_expired_session_token_rejected(env, now):
    token, _ = issue_session_token(
        env.session_hmac_key,
        tenant_id="acme",
        subject_id="alice@acme.example.com",
        subject_type=SUBJECT_USER,
        role="admin",
        now=now - 7200,
        ttl=3600,
    )
    with pytest.raises(InvalidSessionTokenError):
        verify_session_token(token, env.session_hmac_key, now=now)


def test_tampered_claims_fail_signature(env, now):
    token, _ = issue_session_token(
        env.session_hmac_key,
        tenant_id="acme",
        subject_id="alice@acme.example.com",
        subject_type=SUBJECT_USER,
        role="admin",
        now=now,
    )
    header, payload, signature = token.split(".")
    claims = _claims_of(token)
    claims["tenantId"] = "othercorp"  # forge the tenant claim
    raw = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    forged_payload = (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    )
    forged = f"{header}.{forged_payload}.{signature}"
    with pytest.raises(InvalidSessionTokenError):
        verify_session_token(
            forged, env.session_hmac_key, now=now, require_tenant=True
        )


def test_wrong_signing_key_rejected(env, now):
    token, _ = issue_session_token(
        bytes(range(64, 96)),  # a different platform secret
        tenant_id="acme",
        subject_id="alice@acme.example.com",
        subject_type=SUBJECT_USER,
        role="admin",
        now=now,
    )
    with pytest.raises(InvalidSessionTokenError):
        verify_session_token(token, env.session_hmac_key, now=now)


def test_ttl_capped_at_maximum(env, now):
    from identity.sso.model import MAX_TTL_SECONDS

    with pytest.raises(SsoError):
        issue_session_token(
            env.session_hmac_key,
            tenant_id="acme",
            subject_id="alice@acme.example.com",
            subject_type=SUBJECT_USER,
            role="admin",
            now=now,
            ttl=MAX_TTL_SECONDS + 1,
        )


def test_unknown_tenant_in_claim_has_no_default(env, now):
    """An invalid tenant claim is refused at use time - no fallback tenant."""
    token, claims = issue_session_token(
        env.session_hmac_key,
        tenant_id="ghost-tenant",
        subject_id="alice@acme.example.com",
        subject_type=SUBJECT_USER,
        role="admin",
        now=now,
    )
    # Using it inside any real tenant is a cross-tenant denial.
    from identity.sso.sessions import SsoService

    svc = SsoService(env.store, env.keystore, session_hmac_key=env.session_hmac_key)
    with pytest.raises(CrossTenantDenied):
        svc.verify_session(token, expected_tenant="acme", now=now)
    # And without an expected tenant it still carries its own tenant claim.
    verified = svc.verify_session(token, now=now)
    assert verified["tenantId"] == "ghost-tenant"
