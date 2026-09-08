"""Tenant onboarding domain model (issue #14, work item 10).

Pure data types for the provisioning controller core: the tenant row, its
identity-provider (IdP) tenant mapping, the installed seed packs, the
per-tenant customization overlay, the provisioning-job record (retry + audit)
and the result of a run. This module has no I/O and imports nothing outside
the standard library; ``store.py`` persists these entities and
``provisioning.py`` / ``jobs.py`` / ``customization.py`` / ``ready.py`` drive
them.

Relationship to the RBAC contract (issue #12): the platform treats an Org *as*
the tenant. Provisioning therefore creates BOTH an onboarding ``Tenant`` row
(the identity/control-plane record, persisted here) and an ``Org`` in the RBAC
store with ``Org.id == Tenant.id`` (the authorization record). The RBAC Org is
seeded from the tenant-type preset pack; later identity phases (#35-#38) wire
this to HTTP - this lane ships the operator-only controller core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- tenant lifecycle --------------------------------------------------------

TENANT_STATUS_PROVISIONING = "provisioning"
TENANT_STATUS_ACTIVE = "active"
TENANT_STATUS_SUSPENDED = "suspended"
TENANT_STATUSES: tuple[str, ...] = (
    TENANT_STATUS_PROVISIONING,
    TENANT_STATUS_ACTIVE,
    TENANT_STATUS_SUSPENDED,
)

# Tenant types served by the built-in RBAC preset packs (identity/rbac/presets).
# A tenant type outside this tuple requires a registered custom pack (the
# custom-pack seam in identity/rbac/presets/__init__.py).
BUILTIN_TENANT_TYPES: tuple[str, ...] = ("platform", "startup", "smb", "enterprise")

# --- IdP tenant mapping ------------------------------------------------------

IDP_MAPPING_STATUS_LINKED = "linked"
IDP_MAPPING_STATUSES: tuple[str, ...] = (IDP_MAPPING_STATUS_LINKED,)

# --- seed packs --------------------------------------------------------------

SEED_KIND_PROFILE = "profile"
SEED_KIND_PERSONA = "persona"
SEED_KIND_PROMPT = "prompt"
SEED_KINDS: tuple[str, ...] = (SEED_KIND_PROFILE, SEED_KIND_PERSONA, SEED_KIND_PROMPT)

# A seed is "installed" when the platform registry asset was found and recorded
# for the tenant; "deferred" when the asset's owning registry has not landed on
# master yet (persona cards, issue #11) - the seed is recorded against the
# contract so it installs automatically once the registry is present.
SEED_STATUS_INSTALLED = "installed"
SEED_STATUS_DEFERRED = "deferred"
SEED_STATUSES: tuple[str, ...] = (SEED_STATUS_INSTALLED, SEED_STATUS_DEFERRED)

# --- provisioning job --------------------------------------------------------
# Status vocabulary mirrors the harvested TenantProvisioningJob model
# (capital-underwriting prisma/schema.prisma).

JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUSES: tuple[str, ...] = (
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
)

JOB_DEFAULT_MAX_ATTEMPTS = 3

# --- customization overlay ---------------------------------------------------
# The customization overlay is bounded to the platform's closed vocabularies
# (consumed field names; do not redefine). defaultModelTier and memoryScope are
# the AgentProfile contract enums (registry/profiles); instructionLayers refs
# resolve against the versioned prompt-module library (registry/prompts).

MODEL_TIERS: tuple[str, ...] = ("LOW", "MED", "HIGH", "MAX")
MEMORY_SCOPES: tuple[str, ...] = ("user", "session", "repository")
INSTRUCTION_LAYER_KINDS: tuple[str, ...] = ("system", "tenant")

# --- tenant identifiers ------------------------------------------------------

# Mirrors saas-rbac's slug rule: lowercase alphanumeric with single hyphens
# between segments. The slug is the public-ish identifier that ends up in
# operator commands and (later) URLs.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Default role the first owner/admin is bound to. Mirrors the value used by the
# RBAC preset packs (every pack ships an "owner" system role).
DEFAULT_OWNER_ROLE_KEY = "owner"

# Default role-admin permission mirror; provisioning passes it to the RBAC Org
# so the Org's no-lockout invariant (identity/rbac) keys off the right string.
DEFAULT_ROLE_ADMIN_PERMISSION = "roles:manage"

# Required keys of a tenant's base defaults (set at provision time).
TENANT_DEFAULT_KEYS: tuple[str, ...] = (
    "defaultModelTier",
    "memoryScope",
    "guardrailPolicyRef",
)


def utcnow_iso() -> str:
    """ISO-8601 UTC timestamp for audit/job fields (deterministic format)."""
    return datetime.now(timezone.utc).isoformat()


def default_tenant_defaults() -> dict[str, Any]:
    """Base defaults every tenant is born with (bounded to closed vocabularies)."""
    return {
        "defaultModelTier": "MED",
        "memoryScope": ["user", "session", "repository"],
        "guardrailPolicyRef": "worker-bundle",
    }


def is_valid_email(value: str) -> bool:
    """Cheap structural email check for owner records (no network)."""
    return "@" in value and value.index("@") not in (0, len(value) - 1)


# --- errors ------------------------------------------------------------------


class ProvisionError(RuntimeError):
    """A provisioning step failed; nothing partial may remain."""


class ProvisionValidationError(ValueError):
    """The provisioning request is invalid (failed before any write)."""


# --- entities ----------------------------------------------------------------


@dataclass
class Tenant:
    """The onboarding/identity row for one tenant (Org.id == Tenant.id)."""

    id: str  # slug
    name: str
    tenant_type: str = "platform"
    status: str = TENANT_STATUS_PROVISIONING
    idp_tenant_id: str | None = None
    domain: str | None = None
    owner_email: str | None = None
    owner_name: str | None = None
    owner_role: str = DEFAULT_OWNER_ROLE_KEY
    role_admin_permission: str = DEFAULT_ROLE_ADMIN_PERMISSION
    defaults: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    provisioned_at: str | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class IdpTenantMapping:
    """Maps the platform tenant to its identity-provider tenant."""

    tenant_id: str
    idp_tenant_id: str
    status: str = IDP_MAPPING_STATUS_LINKED
    issuer: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class SeedItem:
    """One installed/deferred starter seed for a tenant."""

    tenant_id: str
    kind: str  # SEED_KIND_*
    ref: str  # e.g. orchestrator@1.0.0, classify-route@v2, security-sme
    version: str = ""
    status: str = SEED_STATUS_INSTALLED
    source: str = ""  # registry-relative path when installed
    detail: str = ""


@dataclass
class TenantCustomization:
    """The tenant's instruction-customization overlay (validated allowlist)."""

    tenant_id: str
    overlay: dict[str, Any] = field(default_factory=dict)
    applied_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class AuditEvent:
    """One step-level audit entry of a provisioning job."""

    at: str
    step: str
    status: str
    detail: str = ""


@dataclass
class ProvisioningJob:
    """A provisioning attempt record: status, attempt counter, step + audit."""

    id: str
    tenant_id: str
    status: str = JOB_STATUS_PENDING
    attempt: int = 0
    max_attempts: int = JOB_DEFAULT_MAX_ATTEMPTS
    step_name: str | None = None
    step_status: str | None = None
    started_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    audit_log: list[AuditEvent] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @property
    def retryable(self) -> bool:
        """A failed job may be retried while attempts remain."""
        return (
            self.status == JOB_STATUS_FAILED
            and self.attempt < self.max_attempts
        )


@dataclass
class ProvisionResult:
    """What a provision run did (returned whether or not it was a dry run)."""

    tenant: Tenant
    roles: list[str] = field(default_factory=list)
    owner_role: str | None = None
    seeds: list[SeedItem] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    converged: bool = False
