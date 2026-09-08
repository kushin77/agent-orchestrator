"""Per-tenant SSO configuration + registration (issue #35, AC #1).

One platform tenant maps to exactly one identity provider. Registration
consumes the onboarding vocabulary (``IdpTenantMapping``: ``idp_tenant_id``,
one IdP tenant -> one platform tenant) and saas-rbac's ``TenantMapping`` shape
(internal tenant id, IdP tenant id, primary domain + aliases, SSO config).

Validation mirrors the harvested writers:

- capital-underwriting ``coerceSsoConfig``: a config with an unknown or
  mismatched ``protocol`` discriminator is refused;
- saas-rbac ``assertValidIdpConfigName``: an IdP config resource name must
  start ``saml.`` / ``oidc.`` for its protocol;
- saas-rbac ``TenantSsoConfig``/``IdpOnboardingConfig`` field requirements.
"""

from __future__ import annotations

from typing import Optional

from .errors import (
    IdpMappingConflictError,
    SsoConfigError,
)
from .model import (
    IDP_CONFIG_PREFIXES,
    OIDC,
    PROTOCOLS,
    SAML,
    TenantSsoConfig,
)
from .store import InMemoryStore


def validate_sso_config(config: TenantSsoConfig) -> None:
    """Validate a config, raising SsoConfigError on any violation."""
    problems: list[str] = []

    if config.protocol not in PROTOCOLS:
        problems.append(
            f"protocol must be one of {PROTOCOLS}, got {config.protocol!r}"
        )

    prefix = IDP_CONFIG_PREFIXES.get(config.protocol, "")
    if not config.idp_config_name.startswith(prefix):
        problems.append(
            f"idp_config_name for {config.protocol} must start "
            f"{prefix!r} (got {config.idp_config_name!r})"
        )

    if not config.tenant_id:
        problems.append("tenant_id is required")

    if not config.issuer:
        problems.append("issuer (IdP entity id / OIDC issuer) is required")

    if config.protocol == SAML:
        required = {
            "entity_id": "SAML SP entity id",
            "acs_url": "ACS (callback) url",
            "idp_sso_url": "IdP SSO url",
            "cert_alias": "IdP signing cert alias",
        }
        for field_name, label in required.items():
            if not getattr(config, field_name):
                problems.append(f"{label} ({field_name}) is required for SAML")
    elif config.protocol == OIDC:
        for field_name, label in {
            "entity_id": "OIDC client id (audience)",
            "acs_url": "OIDC redirect/callback url",
            "idp_sso_url": "OIDC authorization url",
        }.items():
            if not getattr(config, field_name):
                problems.append(f"{label} ({field_name}) is required for OIDC")
        if not config.cert_alias and not config.secret_alias:
            problems.append(
                "OIDC requires a cert_alias (RS256 JWKS/cert) or a "
                "secret_alias (HS256 client secret) to verify id_tokens"
            )
        if config.cert_alias and config.secret_alias:
            problems.append(
                "OIDC cert_alias and secret_alias are mutually exclusive "
                "(RS256 via cert, or HS256 via client secret)"
            )

    for restriction in config.domain_restrictions:
        if "@" in restriction or "/" in restriction or not restriction:
            problems.append(f"invalid email domain restriction {restriction!r}")

    if config.clock_tolerance_s < 0:
        problems.append("clock_tolerance_s cannot be negative")
    if config.token_ttl_seconds <= 0:
        problems.append("token_ttl_seconds must be positive")

    if problems:
        joined = "; ".join(problems)
        raise SsoConfigError(f"invalid SSO config for {config.tenant_id!r}: {joined}")


def register_tenant_sso(
    store: InMemoryStore,
    config: TenantSsoConfig,
    *,
    primary_domain: str = "",
    aliases: tuple[str, ...] = (),
) -> TenantSsoConfig:
    """Validate + persist a tenant's SSO config and domain route.

    IdP-tenant uniqueness is enforced here (AC #1): an IdP tenant already
    bound to a *different* platform tenant is refused - a user from tenant A
    can never authenticate into tenant B through the mapping. Re-registering
    the same tenant is an idempotent upsert (mirrors saas-rbac
    ``upsertTenantMapping``).
    """
    validate_sso_config(config)

    existing = store.config_for(config.tenant_id)
    if existing is not None:
        # Same tenant re-registering: its IdP tenant may not silently change
        # to a second provider binding.
        if (
            config.idp_tenant_id
            and existing.idp_tenant_id
            and config.idp_tenant_id != existing.idp_tenant_id
        ):
            raise IdpMappingConflictError(
                f"tenant {config.tenant_id!r} is already bound to IdP tenant "
                f"{existing.idp_tenant_id!r} and cannot be re-bound to "
                f"{config.idp_tenant_id!r}"
            )

    if config.idp_tenant_id:
        owner = store.idp_owner(config.idp_tenant_id)
        if owner is not None and owner != config.tenant_id:
            raise IdpMappingConflictError(
                f"IdP tenant {config.idp_tenant_id!r} is already mapped to "
                f"platform tenant {owner!r}; one IdP tenant maps to exactly "
                "one platform tenant"
            )

    store.put_config(config)
    if primary_domain:
        store.put_route(config.tenant_id, primary_domain, aliases)
    return config


def require_sso_config(
    store: InMemoryStore, tenant_id: str, *, enabled_only: bool = True
) -> TenantSsoConfig:
    """Fetch a tenant's config or raise a fail-closed TenantNotConfiguredError.

    ``enabled_only`` models the saas-rbac distinction between a tenant with an
    SSO config that is disabled (falls through to another auth path) and one
    with no SSO configured at all.
    """
    from .errors import TenantNotConfiguredError

    config = store.config_for(tenant_id)
    if config is None:
        raise TenantNotConfiguredError(f"tenant {tenant_id!r} has no SSO config")
    if enabled_only and not config.enabled:
        raise TenantNotConfiguredError(
            f"tenant {tenant_id!r} SSO is disabled"
        )
    return config


def sso_config_for_host(store: InMemoryStore, host_header: str) -> Optional[TenantSsoConfig]:
    """Resolve a host to its tenant's SSO config (fail closed: no host -> no
    config; no config -> None so the caller can 404/fall through)."""
    from .domains import normalize_hostname

    host = normalize_hostname(host_header)
    if host is None:
        return None
    tenant_id = store.route_owner(host)
    if tenant_id is None:
        return None
    config = store.config_for(tenant_id)
    if config is None or not config.enabled:
        return None
    return config
