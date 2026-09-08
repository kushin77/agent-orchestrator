"""SAML 2.0 flow tests (issue #35): signed-assertion parsing, RSA signature
verification with a configured IdP cert, and the full negative battery -
tampered / forged / unsigned / wrong-tenant / expired / out-of-audience
assertions are all denied.
"""

import pytest

from identity.sso.errors import (
    AssertionExpiredError,
    AssertionNotYetValidError,
    InvalidAssertionError,
    LoginDeniedError,
    MissingSignatureError,
    SignatureVerificationError,
    UntrustedSignerError,
)
from identity.sso.model import SAML, SsoSession


def _acme_service(env):
    env.register(SAML, "acme", domain="acme.example.com")
    return env.service()


def test_valid_signed_assertion_yields_scoped_session(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion("acme")
    session = svc.complete_saml("acme.example.com", assertion, now=now)
    assert isinstance(session, SsoSession)
    assert session.tenant_id == "acme"
    assert session.subject_id == "alice@acme.example.com"
    assert session.email == "alice@acme.example.com"
    assert session.purpose == "api-session"
    claims = svc.verify_session(session.token, expected_tenant="acme", now=now)
    assert claims["tenantId"] == "acme"
    assert claims["sub"] == "alice@acme.example.com"
    assert claims["subjectType"] == "user"
    # A login audit event was stamped.
    events = [e.event for e in env.store.audit_log()]
    assert "sso.login" in events


def test_attribute_mapping_uses_configured_email_attribute(env, now):
    env.register(
        SAML,
        "acme",
        domain="acme.example.com",
        email_attribute="mail",
    )
    svc = env.service()
    assertion = env.saml_assertion(
        "acme", email_attribute="mail", email="alice@acme.example.com"
    )
    session = svc.complete_saml("acme.example.com", assertion, now=now)
    assert session.subject_id == "alice@acme.example.com"


def test_role_attribute_is_carried_as_session_claim(env, now):
    env.register(
        SAML, "acme", domain="acme.example.com", role_attribute="role"
    )
    svc = env.service()
    assertion = env.saml_assertion(
        "acme",
        attributes={
            "email": ["alice@acme.example.com"],
            "role": ["team-admin"],
        },
    )
    session = svc.complete_saml("acme.example.com", assertion, now=now)
    assert session.role == "team-admin"


def test_unsigned_assertion_rejected_when_cert_required(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion("acme", sign=False)
    with pytest.raises(MissingSignatureError):
        svc.complete_saml("acme.example.com", assertion, now=now)


def test_tampered_assertion_rejected(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion("acme")
    tampered = assertion.replace(
        b"alice@acme.example.com", b"mallory@acme.example.com"
    )
    assert tampered != assertion
    with pytest.raises(SignatureVerificationError):
        svc.complete_saml("acme.example.com", tampered, now=now)


def test_forged_assertion_with_wrong_key_rejected(env, now):
    svc = _acme_service(env)
    # A second tenant exists, so we can sign with *its* IdP key.
    env.register(SAML, "othercorp", domain="othercorp.example.com")
    wrong_keys = env.idp_keys[(SAML, "othercorp")]
    forged = env.saml_assertion("acme", keys_override=wrong_keys)
    # The embedded cert does not match acme's trusted IdP cert -> untrusted.
    with pytest.raises(UntrustedSignerError):
        svc.complete_saml("acme.example.com", forged, now=now)


def test_assertion_from_other_issuer_rejected(env, now):
    svc = _acme_service(env)
    env.register(SAML, "othercorp", domain="othercorp.example.com")
    other_assertion = env.saml_assertion("othercorp")
    with pytest.raises(InvalidAssertionError):
        # Presented on acme's ACS but issued by othercorp's IdP.
        svc.complete_saml("acme.example.com", other_assertion, now=now)


def test_expired_assertion_rejected(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion(
        "acme", not_before="2020-01-01T00:00:00Z",
        not_on_or_after="2021-01-01T00:00:00Z",
    )
    with pytest.raises(AssertionExpiredError):
        svc.complete_saml("acme.example.com", assertion, now=now)


def test_not_yet_valid_assertion_rejected(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion(
        "acme", not_before="2099-01-01T00:00:00Z",
        not_on_or_after="2100-01-01T00:00:00Z",
    )
    with pytest.raises(AssertionNotYetValidError):
        svc.complete_saml("acme.example.com", assertion, now=now)


def test_out_of_audience_assertion_rejected(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion("acme", audience="urn:someone-elses:sp")
    with pytest.raises(InvalidAssertionError):
        svc.complete_saml("acme.example.com", assertion, now=now)


def test_assertion_without_mappable_email_rejected(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion(
        "acme", attributes={"groups": ["team-a"]}
    )
    with pytest.raises(LoginDeniedError):
        svc.complete_saml("acme.example.com", assertion, now=now)


def test_xxe_doctype_assertion_rejected(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion("acme")
    poisoned = (
        b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe '
        b'SYSTEM "file:///etc/passwd">]>'
    ) + assertion
    with pytest.raises(InvalidAssertionError):
        svc.complete_saml("acme.example.com", poisoned, now=now)


def test_malformed_xml_rejected(env, now):
    svc = _acme_service(env)
    with pytest.raises(InvalidAssertionError):
        svc.complete_saml("acme.example.com", b"<Assertion><broken>", now=now)


def test_email_domain_restriction_denies_valid_assertion(env, now):
    env.register(
        SAML,
        "acme",
        domain="acme.example.com",
        domain_restrictions=("acme.example.com",),
    )
    svc = env.service()
    assertion = env.saml_assertion(
        "acme", email="mallory@evil.example.net"
    )
    # Signature is valid, but the identity's domain is not allowed.
    with pytest.raises(LoginDeniedError):
        svc.complete_saml("acme.example.com", assertion, now=now)


def test_unknown_host_for_assertion_fails_closed(env, now):
    svc = _acme_service(env)
    assertion = env.saml_assertion("acme")
    with pytest.raises(Exception) as excinfo:
        svc.complete_saml("not-a-tenant.example.com", assertion, now=now)
    from identity.sso.errors import UnknownTenantError

    assert isinstance(excinfo.value, UnknownTenantError)


def test_saml_authn_request_and_redirect_url(env):
    env.register(SAML, "acme", domain="acme.example.com")
    svc = env.service()
    flow = svc.start_saml("acme")
    assert flow["protocol"] == "saml"
    assert flow["tenant_id"] == "acme"
    import base64

    xml = base64.b64decode(flow["request_b64"]).decode("utf-8")
    assert "AuthnRequest" in xml
    assert "urn:acme:sp" in xml  # SP entity id as Issuer
    assert "https://idp.acme.example.com/sso" in xml  # Destination
    assert flow["redirect_url"].startswith("https://idp.acme.example.com/sso?")
