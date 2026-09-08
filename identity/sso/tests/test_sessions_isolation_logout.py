"""Tenant isolation + logout/revocation end-to-end (issue #35, AC #2).

The platform doctrine, enforced here: a user from tenant A can authenticate
into tenant A and **only** tenant A. A tenant-A session is refused in tenant B
(CrossTenantDenied - no fallback), and a logged-out / revoked session is
refused even while unexpired.
"""

import pytest

from identity.sso.errors import (
    CrossTenantDenied,
    SessionRevokedError,
    UnknownTenantError,
)
from identity.sso.model import SAML


def _two_tenant_env(env):
    env.register(SAML, "acme", domain="acme.example.com")
    env.register(SAML, "othercorp", domain="othercorp.example.com")
    return env.service()


def test_user_authenticates_into_own_tenant_only(env, now):
    svc = _two_tenant_env(env)
    acme_assertion = env.saml_assertion("acme")
    session = svc.complete_saml("acme.example.com", acme_assertion, now=now)
    assert session.tenant_id == "acme"

    # Usable inside acme ...
    svc.verify_session(session.token, expected_tenant="acme", now=now)
    # ... and refused inside othercorp - no cross-tenant fallback.
    with pytest.raises(CrossTenantDenied):
        svc.verify_session(session.token, expected_tenant="othercorp", now=now)


def test_assertion_for_tenant_a_cannot_log_into_tenant_b(env, now):
    svc = _two_tenant_env(env)
    acme_assertion = env.saml_assertion("acme")
    # Delivered on othercorp's host -> parsed against othercorp's IdP config,
    # which acme's IdP did not issue -> denied before any session exists.
    from identity.sso.errors import InvalidAssertionError

    with pytest.raises(InvalidAssertionError):
        svc.complete_saml("othercorp.example.com", acme_assertion, now=now)


def test_logout_revokes_session_jti(env, now):
    svc = _two_tenant_env(env)
    session = svc.complete_saml(
        "acme.example.com", env.saml_assertion("acme"), now=now
    )
    svc.verify_session(session.token, expected_tenant="acme", now=now)
    jti = svc.logout(session.token, now=now)
    assert jti == session.session_id
    assert env.store.is_revoked(jti)
    with pytest.raises(SessionRevokedError):
        svc.verify_session(session.token, expected_tenant="acme", now=now)
    # The audit trail records login and logout.
    events = [e.event for e in env.store.audit_log()]
    assert events.count("sso.login") == 1
    assert events.count("sso.logout") == 1


def test_unknown_host_is_never_a_login(env, now):
    svc = _two_tenant_env(env)
    with pytest.raises(UnknownTenantError):
        svc.complete_saml("mallory.example.com", env.saml_assertion("acme"), now=now)


def test_session_carries_role_and_subject_snapshot(env, now):
    env.register(SAML, "acme", domain="acme.example.com", role_attribute="role")
    svc = env.service()
    assertion = env.saml_assertion(
        "acme",
        attributes={
            "email": ["bob@acme.example.com"],
            "role": ["agent-operator"],
        },
    )
    session = svc.complete_saml("acme.example.com", assertion, now=now)
    claims = svc.verify_session(session.token, now=now)
    assert claims["role"] == "agent-operator"
    assert claims["subjectType"] == "user"
    assert claims["email"] == "bob@acme.example.com"
