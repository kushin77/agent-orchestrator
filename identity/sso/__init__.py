"""Tenant identity + SSO - tenant-to-IdP mapping, SAML/OIDC, sessions
(issue #35, work item 31, phase 6).

Owner lane: **identity** - this subtree is ``identity/sso/**`` only. Doctrine:
``AGENTS.md``, ``docs/EXECUTION-PLAN.md``, ``docs/ARCHITECTURE.md``,
``docs/GOLDEN-RULES.md``.

Import this package as ``identity.sso`` (``identity/`` has no ``__init__.py``
and acts as a PEP-420 namespace package, mirroring ``identity/onboarding`` and
``identity/rbac``). Public surface:

- ``model`` - tenant SSO config, principal, session, impersonation grant,
  audit-event data types (frozen vocabularies, consumed not redefined).
- ``errors`` - the fail-closed error taxonomy + consumed auth wire codes.
- ``jose`` - offline JOSE: HS256/RS256 JWT, JWKS, RFC 7638 thumbprints.
- ``keystore`` - alias -> key-material store (certificates, RSA keys,
  secrets injected at runtime; nothing persisted to the repo).
- ``config`` - per-tenant SSO config validation + registration (one IdP
  tenant per platform tenant) + host -> config resolution.
- ``domains`` - fail-closed Host -> tenant resolution (custom domains; no
  default tenant; ``X-Forwarded-Host`` ignored).
- ``store`` - ``InMemoryStore`` (persistence seam) + ``FileStore`` (JSON).
- ``saml`` - SAML 2.0 SP flows: AuthnRequest build + assertion
  parse/verify (signature, issuer, audience, conditions, email mapping).
- ``oidc`` - OIDC RP flows: authorization URL + id_token verify + discovery.
- ``tokens`` - HS256 session tokens (scoped tenant claims, AC #2), RS256
  console ``os-session-token`` + JWKS + relay-state JWT + allowlist (AC #3).
- ``sessions`` - ``SsoService`` orchestrator: login flows, email-domain
  policy, session issue/verify/logout, tenant isolation, console SSO model.
- ``impersonation`` - explicit impersonation grants + audit stamp (AC #4).
"""

from identity.sso import (
    config,
    domains,
    errors,
    impersonation,
    jose,
    keystore,
    model,
    oidc,
    saml,
    sessions,
    store,
    tokens,
)

__all__ = [
    "config",
    "domains",
    "errors",
    "impersonation",
    "jose",
    "keystore",
    "model",
    "oidc",
    "saml",
    "sessions",
    "store",
    "tokens",
]
