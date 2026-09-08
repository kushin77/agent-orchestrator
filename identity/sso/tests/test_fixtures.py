"""Static-fixture verification (issue #35 offline constraint).

The committed fixtures under ``tests/fixtures/`` are real artifact files - a
signed SAML Response with its IdP certificate, and an RS256 OIDC id_token with
its public JWKS + discovery document - produced once by the authoring script
(private keys never touched disk). These tests prove the offline parse path
against the shipped bytes rather than only against freshly generated ones.
"""

import json
from pathlib import Path

import pytest

from identity.sso.jose import public_key_from_jwk
from identity.sso.model import OIDC, SAML

FIXTURES = Path(__file__).parent / "fixtures"


def _saml_env(env):
    cert_pem = (FIXTURES / "saml-acme-idp-cert.pem").read_bytes()
    config = env.register(
        SAML,
        "acme",
        domain="acme.example.com",
        issuer="https://idp.acme.example.com",
        entity_id="urn:acme:sp",
        acs_url="https://acme.example.com/acs",
        idp_sso_url="https://idp.acme.example.com/sso",
        idp_tenant_id="idp-acme",
        email_attribute="email",
    )
    env.keystore.put_cert_pem(config.cert_alias, cert_pem)
    return env.service()


def _oidc_env(env):
    jwks = json.loads((FIXTURES / "oidc-acme-jwks.json").read_text())
    key = public_key_from_jwk(jwks["keys"][0])
    config = env.register(
        OIDC,
        "acme",
        domain="acme.example.com",
        issuer="https://issuer.acme.example.com",
        entity_id="acme-client",
        acs_url="https://acme.example.com/cb",
        idp_sso_url="https://issuer.acme.example.com/authorize",
        idp_tenant_id="idp-acme",
        email_attribute="email",
    )
    env.keystore.put_public_key(config.cert_alias, key)
    return env.service()


def test_static_signed_saml_assertion_parses_and_verifies(env, now):
    svc = _saml_env(env)
    assertion = (FIXTURES / "saml-acme-response.xml").read_bytes()
    session = svc.complete_saml("acme.example.com", assertion, now=now)
    assert session.tenant_id == "acme"
    assert session.email == "alice@acme.example.com"


def test_static_saml_assertion_is_tamper_evident(env, now):
    svc = _saml_env(env)
    assertion = (FIXTURES / "saml-acme-response.xml").read_bytes()
    tampered = assertion.replace(
        b"alice@acme.example.com", b"mallory@acme.example.com"
    )
    from identity.sso.errors import SignatureVerificationError

    with pytest.raises(SignatureVerificationError):
        svc.complete_saml("acme.example.com", tampered, now=now)


def test_static_oidc_id_token_verifies_against_fixture_jwks(env, now):
    svc = _oidc_env(env)
    token = (FIXTURES / "oidc-acme-idtoken.jwt").read_text().strip()
    session = svc.complete_oidc("acme.example.com", token, now=now)
    assert session.tenant_id == "acme"
    assert session.email == "alice@acme.example.com"
    claims = svc.verify_session(session.token, expected_tenant="acme", now=now)
    assert claims["tenantId"] == "acme"


def test_static_oidc_discovery_document_validates(env):
    discovery = json.loads((FIXTURES / "oidc-acme-discovery.json").read_text())
    from identity.sso.oidc import discovery_endpoints

    endpoints = discovery_endpoints(discovery)
    assert endpoints["issuer"] == "https://issuer.acme.example.com"
    assert endpoints["authorization_endpoint"].endswith("/authorize")
    assert endpoints["jwks_uri"].endswith("/jwks")
