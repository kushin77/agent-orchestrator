"""AC #1 - tenant <-> IdP mapping, per-tenant SSO config, custom-domain
resolution. Every assertion here is fail-closed: no default tenant, no
fallback to a tenant named in a token, and no inference from anything else.
"""

import pytest

from identity.sso.config import (
    register_tenant_sso,
    validate_sso_config,
)
from identity.sso.domains import (
    email_domain,
    normalize_hostname,
    require_tenant_for_host,
)
from identity.sso.errors import (
    IdpMappingConflictError,
    SsoConfigError,
    UnknownTenantError,
)
from identity.sso.model import OIDC, SAML, TenantSsoConfig


# --- host normalization + resolution -----------------------------------------


def test_normalize_hostname_strips_port_and_lowercases(env):
    assert normalize_hostname("Acme.Example.com:8443") == "acme.example.com"
    assert normalize_hostname("acme.example.com") == "acme.example.com"
    assert normalize_hostname(None) is None
    assert normalize_hostname("   ") is None
    assert normalize_hostname("[::1]:8080") == "[::1]"
    assert normalize_hostname("") is None


def test_host_resolves_primary_domain(env):
    env.register(SAML, "acme", domain="acme.example.com")
    assert (
        require_tenant_for_host(env.store, "acme.example.com") == "acme"
    )


def test_custom_domain_and_alias_resolve_to_tenant(env):
    env.register(
        SAML,
        "acme",
        domain="acme.example.com",
        aliases=("acme.io", "custom.acme.co"),
    )
    assert require_tenant_for_host(env.store, "custom.acme.co") == "acme"
    assert require_tenant_for_host(env.store, "acme.io") == "acme"
    # Port suffixes and case are normalized before lookup.
    assert require_tenant_for_host(env.store, "ACME.EXAMPLE.COM:443") == "acme"


def test_unknown_host_fails_closed_no_default_tenant(env):
    env.register(SAML, "acme", domain="acme.example.com")
    with pytest.raises(UnknownTenantError):
        require_tenant_for_host(env.store, "other.example.com")
    # An unusable Host header is also a denial, never "no tenant needed".
    with pytest.raises(UnknownTenantError):
        require_tenant_for_host(env.store, "")
    with pytest.raises(UnknownTenantError):
        require_tenant_for_host(env.store, None)  # type: ignore[arg-type]


def test_email_domain_helper():
    assert email_domain("Alice@Acme.Example.com") == "acme.example.com"
    assert email_domain("not-an-email") == ""


# --- config validation --------------------------------------------------------


def test_saml_config_validation_requires_core_fields(env):
    with pytest.raises(SsoConfigError):
        env.register(SAML, "acme", issuer="")  # issuer required


def test_idp_config_name_prefix_rule(env):
    # saas-rbac: saml config names must start with "saml.", oidc with "oidc."
    values = env.default_config(SAML, "acme", idp_config_name="oidc.acme")
    with pytest.raises(SsoConfigError):
        validate_sso_config(TenantSsoConfig(**values))
    values = env.default_config(SAML, "acme", idp_config_name="")
    with pytest.raises(SsoConfigError):
        validate_sso_config(TenantSsoConfig(**values))


def test_oidc_config_needs_signing_key_and_single_alg(env):
    # No signing key at all -> refused.
    values = env.default_config(OIDC, "acme")
    values.update({"cert_alias": "", "secret_alias": ""})
    with pytest.raises(SsoConfigError):
        validate_sso_config(TenantSsoConfig(**values))
    # Both cert and secret (ambiguous alg) -> refused.
    values = env.default_config(OIDC, "acme")
    values.update({"cert_alias": "cert:x", "secret_alias": "sec:y"})
    with pytest.raises(SsoConfigError):
        validate_sso_config(TenantSsoConfig(**values))
    # HS256-only via secret_alias is valid.
    cfg = TenantSsoConfig(
        **env.default_config(
            OIDC, "acme", cert_alias="", secret_alias="sec:acme-oidc"
        )
    )
    validate_sso_config(cfg)


def test_unknown_protocol_rejected(env):
    values = env.default_config(SAML, "acme")
    values["protocol"] = "ws-fed"
    with pytest.raises(SsoConfigError):
        validate_sso_config(TenantSsoConfig(**values))


# --- tenant <-> IdP mapping (one-to-one) --------------------------------------


def test_idp_tenant_maps_to_exactly_one_platform_tenant(env):
    env.register(SAML, "acme", domain="acme.example.com")
    # othercorp tries to claim the same IdP tenant acme already owns.
    with pytest.raises(IdpMappingConflictError):
        env.register(
            SAML, "othercorp", domain="othercorp.example.com",
            idp_tenant_id="idp-acme",
        )


def test_tenant_cannot_be_rebound_to_a_second_idp(env):
    env.register(SAML, "acme", domain="acme.example.com", idp_tenant_id="idp-a")
    with pytest.raises(IdpMappingConflictError):
        env.register(
            SAML, "acme", domain="acme.example.com", idp_tenant_id="idp-b"
        )


def test_registration_is_idempotent_upsert_for_same_tenant(env):
    cfg_a = env.register(SAML, "acme", domain="acme.example.com")
    cfg_b = register_tenant_sso(
        env.store, cfg_a, primary_domain="acme.example.com"
    )
    assert cfg_b.tenant_id == "acme"
    assert env.store.config_for("acme").idp_tenant_id == "idp-acme"
