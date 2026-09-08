"""Pure data types for tenant identity + SSO (issue #35).

Field names are **consumed**, not redefined: every vocabulary below is frozen
by an earlier lane and this subtree imports the names, never the files (see
``README.md`` "Consumed contracts").

- IdP-tenant mapping: ``idp_tenant_id`` / ``issuer`` mirror onboarding's
  ``IdpTenantMapping`` (one IdP tenant maps to exactly one platform tenant).
- Subject kinds: ``subject_type`` in {``user``, ``agent``} mirror
  ``identity/rbac`` ``SUBJECT_USER`` / ``SUBJECT_AGENT``.
- Token claims mirror the registry (issue #10) ``AgentSession`` scoped-claims
  shape (``tenantId``/``sub``/``role``) and the shared-governance
  ``agent-identity-jwt.schema.json`` (``iss``/``sub``/``aud``/``iat``/``exp``/
  ``jti``).
- Per-tenant SSO config mirrors capital-underwriting ``StoredSsoConfig``
  (protocol-discriminated single column) + saas-rbac ``TenantSsoConfig`` /
  ``IdpOnboardingConfig`` (``idpConfigName`` ``saml.``/``oidc.`` prefix rule).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

# --- protocols --------------------------------------------------------------

SAML = "saml"
OIDC = "oidc"
PROTOCOLS: tuple[str, ...] = (SAML, OIDC)

# saas-rbac: Identity Platform requires IdP config resource names to be
# prefixed by protocol ("saml." / "oidc.").
IDP_CONFIG_PREFIXES: dict[str, str] = {SAML: "saml.", OIDC: "oidc."}

# --- subjects (mirror identity/rbac SUBJECT_*) ------------------------------

SUBJECT_USER = "user"
SUBJECT_AGENT = "agent"
SUBJECT_TYPES: tuple[str, ...] = (SUBJECT_USER, SUBJECT_AGENT)

DEFAULT_SUBJECT_TYPE = SUBJECT_USER
DEFAULT_AGENT_ROLE = "agent"  # mirror registry/service DEFAULT_ROLE

# --- token vocabulary -------------------------------------------------------

SSO_ISSUER = "urn:agent-orchestrator:sso"
SESSION_AUDIENCE = ("urn:agent-orchestrator:api",)
CONSOLE_AUDIENCE = ("urn:agent-orchestrator:console",)

# shared-frontend purpose string for the RS256 console handoff token.
CONSOLE_TOKEN_PURPOSE = "os-session-token"
API_SESSION_PURPOSE = "api-session"

# shared-governance agent-oidc-config token issuance policy defaults.
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 86400
RELAY_STATE_TTL_SECONDS = 300  # shared-frontend auth-gate 5-minute state JWT
CLOCK_TOLERANCE_SECONDS = 60

# SAML attribute carrying the identity email (attribute mapping email->user).
DEFAULT_EMAIL_ATTRIBUTE = "email"
DEFAULT_NAME_ATTRIBUTE = ""

ALG_HS256 = "HS256"
ALG_RS256 = "RS256"
SUPPORTED_ID_TOKEN_ALGS: tuple[str, ...] = (ALG_RS256, ALG_HS256)

# --- audit event kinds ------------------------------------------------------

EVENT_LOGIN = "sso.login"
EVENT_LOGOUT = "sso.logout"
EVENT_REVOKE = "sso.session.revoked"
EVENT_IMP_GRANT = "impersonation.grant"
EVENT_IMP_REVOKE = "impersonation.revoke"
EVENT_IMP_LOGIN = "impersonation.login"


def utcnow_iso() -> str:
    """RFC3339 UTC timestamp string (naive clock for offline determinism)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def new_id(prefix: str) -> str:
    """A fresh lowercase id (uuid4 hex) - stable jti/grant ids offline."""
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class TenantSsoConfig:
    """One platform tenant's SSO configuration (protocol-discriminated).

    Mirrors capital-underwriting ``StoredSsoConfig`` (single opaque column,
    ``protocol`` discriminator, protocol-specific optional fields) and
    saas-rbac ``IdpOnboardingConfig`` (``idpConfigName`` ``saml.``/``oidc.``
    prefix rule). Secrets never live here: certificates/secrets are held in
    the keystore and referenced by alias (``cert_alias`` / ``secret_alias``).
    """

    protocol: str  # "saml" | "oidc"
    idp_config_name: str  # must start "saml." or "oidc."
    tenant_id: str = ""  # platform tenant id (internalTenantId / Account.id)
    enabled: bool = True
    # The IdP tenant this SaaS tenant is bound to (onboarding IdpTenantMapping
    # vocabulary: one IdP tenant maps to exactly one platform tenant).
    idp_tenant_id: str = ""
    # --- SP side (us) ---
    entity_id: str = ""  # SAML: our SP entity id; OIDC: our client_id
    acs_url: str = ""  # SAML: ACS endpoint; OIDC: redirect/callback endpoint
    # --- IdP side (them) ---
    issuer: str = ""  # SAML: IdP entity id; OIDC: issuer URL
    idp_sso_url: str = ""  # SAML: SSO sign-in URL; OIDC: authorization URL
    cert_alias: str = ""  # keystore: SAML IdP x509 cert / OIDC RS256 public
    secret_alias: str = ""  # keystore: OIDC client secret (HS256 only)
    jwks_uri: str = ""  # OIDC discovery hint (modeled, not fetched offline)
    # --- attribute mapping email->user (+ optional name/role) ---
    email_attribute: str = DEFAULT_EMAIL_ATTRIBUTE
    name_attribute: str = DEFAULT_NAME_ATTRIBUTE
    role_attribute: str = ""
    # --- policy ---
    domain_restrictions: tuple[str, ...] = ()
    jit_provisioning: bool = True
    clock_tolerance_s: int = CLOCK_TOLERANCE_SECONDS
    token_ttl_seconds: int = DEFAULT_TTL_SECONDS

    @property
    def client_id(self) -> str:
        """OIDC client id (== entity_id for OIDC configs)."""
        return self.entity_id

    @property
    def signing_kind(self) -> str:
        """Which keystore alias verifies upstream signatures for this config."""
        if self.protocol == SAML:
            return "cert"
        # OIDC: cert (RS256) XOR secret (HS256)
        return "secret" if (self.cert_alias == "" and self.secret_alias) else "cert"


@dataclass(frozen=True)
class ResolvedPrincipal:
    """The identity an IdP assertion/response maps to inside one tenant.

    ``subject_id`` is the platform user/agent key this tenant keys its data
    on (email for human principals via the email->user attribute mapping).
    ``subject_type`` is ``user`` or ``agent`` (rbac vocabulary).
    """

    tenant_id: str
    idp_tenant_id: str
    provider_protocol: str
    subject_id: str
    subject_type: str = SUBJECT_USER
    email: str = ""
    name: str = ""
    role: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    idp_subject: str = ""  # upstream NameID / OIDC sub (for audit)
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class SsoSession:
    """An issued session credential, scoped to exactly one tenant.

    Mirrors registry/service ``AgentSession`` (claims scoped to one tenant,
    no cross-tenant fallback) extended for the human/console path: the token
    carries ``tenantId`` + ``subjectType`` + ``role`` (+ operator stamp when
    the session is an impersonated one).
    """

    token: str
    tenant_id: str
    subject_id: str
    subject_type: str
    role: str
    email: str
    session_id: str  # == token jti
    purpose: str
    issued_at: int
    expires_at: int
    # Impersonation context - populated only for impersonated sessions.
    operator_user_id: str | None = None
    grant_id: str | None = None


@dataclass(frozen=True)
class ImpersonationGrant:
    """A durable, addressable explicit impersonation grant.

    Harnessed from capital-underwriting ``ImpersonationGrant`` (id/jti/
    operatorUserId/actingAsUserId/actingAsRole/expiresAt/revokedAt/
    revokedByUserId/reason). The ``jti`` is bound to the impersonated session
    token so revoking the grant kills the token (jti-keyed).
    """

    grant_id: str
    tenant_id: str
    operator_user_id: str
    target_user_id: str
    target_role: str
    jti: str
    reason: str
    expires_at: int
    created_at: int = 0
    revoked_at: int | None = None
    revoked_by_user_id: str | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


@dataclass(frozen=True)
class SsoAuditEvent:
    """An append-only audit stamp for SSO lifecycle events.

    ``operator_user_id`` is the audit stamp required for impersonation
    (mirrors capital ``AuditLog.impersonatedByUserId``): when an event is
    emitted from inside an impersonated session the real operator is recorded
    alongside the impersonated subject.
    """

    event: str
    tenant_id: str
    subject_id: str
    at: str
    session_id: str = ""
    operator_user_id: str | None = None
    grant_id: str | None = None
    detail: str = ""
