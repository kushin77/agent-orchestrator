"""AC #4 - impersonation with explicit grant + audit stamp (enterprise support
path, harvested from capital-underwriting).

A session is never impersonated implicitly: an explicit, unrevoked, unexpired
grant must exist for the operator/tenant/target triple. The impersonated
session token's jti equals the grant's jti, so revoking the grant kills the
session, and every impersonation audit event stamps the real operator.
"""

import pytest

from identity.sso.errors import (
    CrossTenantDenied,
    GrantExpiredError,
    GrantRevokedError,
    ImpersonationError,
    ImpersonationNotGrantedError,
    SessionRevokedError,
    UnknownGrantError,
)
from identity.sso.impersonation import (
    create_impersonation_grant,
    issue_impersonated_session,
    revoke_impersonation,
)
from identity.sso.model import EVENT_IMP_GRANT, EVENT_IMP_LOGIN, EVENT_IMP_REVOKE, SAML


def _env_with_tenant(env, tenant_id="acme"):
    env.register(SAML, tenant_id, domain=f"{tenant_id}.example.com")
    return env


def test_no_grant_means_no_impersonation(env, now):
    _env_with_tenant(env)
    with pytest.raises(UnknownGrantError):
        issue_impersonated_session(
            env.store,
            hmac_key=env.session_hmac_key,
            grant_id="imp_does_not_exist",
            operator_user_id="support-1@platform.example.com",
            tenant_id="acme",
            target_user_id="alice@acme.example.com",
            now=now,
        )


def test_explicit_grant_issues_scoped_impersonated_session(env, now):
    _env_with_tenant(env)
    grant = create_impersonation_grant(
        env.store,
        tenant_id="acme",
        operator_user_id="support-1@platform.example.com",
        target_user_id="alice@acme.example.com",
        target_role="admin",
        reason="ticket #1234: alice is on leave",
        now=now,
    )
    session = issue_impersonated_session(
        env.store,
        hmac_key=env.session_hmac_key,
        grant_id=grant.grant_id,
        operator_user_id=grant.operator_user_id,
        tenant_id="acme",
        target_user_id=grant.target_user_id,
        now=now,
    )
    # The session is scoped to the grant's tenant and acts as the target.
    assert session.tenant_id == "acme"
    assert session.subject_id == "alice@acme.example.com"
    assert session.role == "admin"
    assert session.session_id == grant.jti  # jti bound to the grant
    assert session.operator_user_id == "support-1@platform.example.com"
    assert session.grant_id == grant.grant_id

    claims = env.service().verify_session(
        session.token, expected_tenant="acme", now=now
    )
    assert claims["operatorUserId"] == "support-1@platform.example.com"
    assert claims["impersonationGrantId"] == grant.grant_id
    assert claims["sub"] == "alice@acme.example.com"


def test_grant_must_cover_operator_tenant_and_target(env, now):
    _env_with_tenant(env)
    grant = create_impersonation_grant(
        env.store,
        tenant_id="acme",
        operator_user_id="support-1@platform.example.com",
        target_user_id="alice@acme.example.com",
        target_role="admin",
        reason="ticket #1234",
        now=now,
    )
    # A different operator cannot ride the same grant.
    with pytest.raises(ImpersonationNotGrantedError):
        issue_impersonated_session(
            env.store,
            hmac_key=env.session_hmac_key,
            grant_id=grant.grant_id,
            operator_user_id="support-2@platform.example.com",
            tenant_id="acme",
            target_user_id="alice@acme.example.com",
            now=now,
        )
    # Nor can it be aimed at a different tenant.
    with pytest.raises(ImpersonationNotGrantedError):
        issue_impersonated_session(
            env.store,
            hmac_key=env.session_hmac_key,
            grant_id=grant.grant_id,
            operator_user_id="support-1@platform.example.com",
            tenant_id="othercorp",
            target_user_id="alice@acme.example.com",
            now=now,
        )


def test_revoking_grant_kills_the_session(env, now):
    _env_with_tenant(env)
    grant = create_impersonation_grant(
        env.store,
        tenant_id="acme",
        operator_user_id="support-1@platform.example.com",
        target_user_id="alice@acme.example.com",
        target_role="admin",
        reason="ticket #1234",
        now=now,
    )
    session = issue_impersonated_session(
        env.store,
        hmac_key=env.session_hmac_key,
        grant_id=grant.grant_id,
        operator_user_id="support-1@platform.example.com",
        tenant_id="acme",
        target_user_id="alice@acme.example.com",
        now=now,
    )
    svc = env.service()
    svc.verify_session(session.token, expected_tenant="acme", now=now)

    revoke_impersonation(
        env.store,
        grant_id=grant.grant_id,
        revoked_by_user_id="security@platform.example.com",
        now=now,
    )
    # The jti is on the revocation list -> the session is dead.
    assert env.store.is_revoked(grant.jti)
    with pytest.raises(SessionRevokedError):
        svc.verify_session(session.token, expected_tenant="acme", now=now)
    # And a new session under the revoked grant is refused.
    with pytest.raises(GrantRevokedError):
        issue_impersonated_session(
            env.store,
            hmac_key=env.session_hmac_key,
            grant_id=grant.grant_id,
            operator_user_id="support-1@platform.example.com",
            tenant_id="acme",
            target_user_id="alice@acme.example.com",
            now=now,
        )


def test_expired_grant_is_refused(env, now):
    _env_with_tenant(env)
    grant = create_impersonation_grant(
        env.store,
        tenant_id="acme",
        operator_user_id="support-1@platform.example.com",
        target_user_id="alice@acme.example.com",
        target_role="admin",
        reason="short window",
        now=now - 3600,
        ttl=300,
    )
    with pytest.raises(GrantExpiredError):
        issue_impersonated_session(
            env.store,
            hmac_key=env.session_hmac_key,
            grant_id=grant.grant_id,
            operator_user_id="support-1@platform.example.com",
            tenant_id="acme",
            target_user_id="alice@acme.example.com",
            now=now,
        )


def test_operator_cannot_impersonate_itself(env, now):
    _env_with_tenant(env)
    with pytest.raises(ImpersonationError):
        create_impersonation_grant(
            env.store,
            tenant_id="acme",
            operator_user_id="alice@acme.example.com",
            target_user_id="alice@acme.example.com",
            target_role="admin",
            reason="self",
            now=now,
        )


def test_impersonated_session_cannot_cross_tenants(env, now):
    _env_with_tenant(env, "acme")
    _env_with_tenant(env, "othercorp")
    grant = create_impersonation_grant(
        env.store,
        tenant_id="acme",
        operator_user_id="support-1@platform.example.com",
        target_user_id="alice@acme.example.com",
        target_role="admin",
        reason="ticket #1234",
        now=now,
    )
    session = issue_impersonated_session(
        env.store,
        hmac_key=env.session_hmac_key,
        grant_id=grant.grant_id,
        operator_user_id="support-1@platform.example.com",
        tenant_id="acme",
        target_user_id="alice@acme.example.com",
        now=now,
    )
    svc = env.service()
    with pytest.raises(CrossTenantDenied):
        svc.verify_session(session.token, expected_tenant="othercorp", now=now)


def test_audit_stamps_real_operator_throughout(env, now):
    _env_with_tenant(env)
    grant = create_impersonation_grant(
        env.store,
        tenant_id="acme",
        operator_user_id="support-1@platform.example.com",
        target_user_id="alice@acme.example.com",
        target_role="admin",
        reason="ticket #1234",
        now=now,
    )
    issue_impersonated_session(
        env.store,
        hmac_key=env.session_hmac_key,
        grant_id=grant.grant_id,
        operator_user_id="support-1@platform.example.com",
        tenant_id="acme",
        target_user_id="alice@acme.example.com",
        now=now,
    )
    revoke_impersonation(
        env.store,
        grant_id=grant.grant_id,
        revoked_by_user_id="security@platform.example.com",
        now=now,
    )
    events = env.store.audit_log()
    kinds = {e.event for e in events}
    assert {EVENT_IMP_GRANT, EVENT_IMP_LOGIN, EVENT_IMP_REVOKE} <= kinds
    for event in events:
        if event.event in (EVENT_IMP_GRANT, EVENT_IMP_LOGIN, EVENT_IMP_REVOKE):
            # Every impersonation event stamps the real operator + grant.
            assert event.operator_user_id is not None
            assert event.grant_id == grant.grant_id
    login = next(e for e in events if e.event == EVENT_IMP_LOGIN)
    assert login.subject_id == "alice@acme.example.com"
    assert login.operator_user_id == "support-1@platform.example.com"
