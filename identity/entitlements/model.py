"""Plan -> entitlement -> override tiering - pure data types and vocabularies.

The capability-tiering contract for the platform (issue #36, work item 32):
a tenant's SUBSCRIPTION PLAN maps to a set of ENTITLEMENTS (feature flags and
numeric limit allowances), each entitlement maps to a set of RBAC permission
grants, and an OVERRIDE grants an entitlement above the plan within governance
(audited, time-boxed, requires authority).

This module is the contract's data spine: closed vocabularies plus the pure
dataclasses ``catalog.py`` parses into, ``store.py`` persists, and
``engine.py`` / ``overrides.py`` evaluate. It consumes the RBAC contract
(``identity/rbac``, issue #12) - ``resource:action`` permissions, an Org *is*
the tenant, preset packs per tenant type - and never redefines it.

The layering (mirrors the cannibalized saas-rbac ``billing`` modules)

- A **plan** is a commercial tier (``free`` / ``pro`` / ``enterprise``). It
  toggles each catalog **feature** on or off and, for limit-kind features,
  sets the numeric cap (``None`` = unlimited).
- A **feature** is product configuration: it declares whether it is a plain
  on/off capability or a metered capacity allowance, and which concrete
  ``resource:action`` permissions it unlocks. The catalog lives with the code
  (reviewed, versioned, deployed with what enforces it) - see
  ``plans/catalog.yaml``.
- An **override** is a per-org, time-boxed departure from the plan. It states
  the WHOLE answer for its feature (not a patch): ``enabled: false`` is a real
  revocation, and it does not inherit from the plan. An override is only
  honored while unexpired; granting one requires a principal holding the
  override-authority permission, and every grant/revoke is audited.
- The **effective permissions** of a tenant's subject are the intersection of
  what its RBAC roles grant and what its entitled capabilities unlock (plus
  the org self-administration core a plan can never strip):
  ``effective = f(plan entitlements ∩ org roles)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from rbac.model import ScopeNode

# --- subscription status (the active-subscription gate) ---------------------

SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_INACTIVE = "inactive"
SUBSCRIPTION_STATUSES: tuple[str, ...] = (SUBSCRIPTION_ACTIVE, SUBSCRIPTION_INACTIVE)

# --- feature kinds -----------------------------------------------------------

# A plain on/off capability toggle (e.g. ``sso``, ``audit_log``).
FEATURE_KIND_FEATURE = "feature"
# A metered capacity allowance: an on/off flag plus a numeric cap (``limit``).
FEATURE_KIND_LIMIT = "limit"
FEATURE_KINDS: tuple[str, ...] = (FEATURE_KIND_FEATURE, FEATURE_KIND_LIMIT)

# --- audit actions -----------------------------------------------------------

ACTION_PLAN_ASSIGN = "plan.assign"
ACTION_OVERRIDE_GRANT = "override.grant"
ACTION_OVERRIDE_REVOKE = "override.revoke"
AUDIT_ACTIONS: tuple[str, ...] = (
    ACTION_PLAN_ASSIGN,
    ACTION_OVERRIDE_GRANT,
    ACTION_OVERRIDE_REVOKE,
)

# --- access-decision reason / code vocabularies ------------------------------

# AccessDecision.reason values: which gate denied.
REASON_SUBSCRIPTION = "subscription"
REASON_SCOPE = "scope"
REASON_PERMISSION = "permission"
REASON_ENTITLEMENT = "entitlement"

# AccessDecision.code / FeatureState.code values (fail-closed causes).
CODE_UNKNOWN_ORG = "unknown_org"
CODE_NO_PLAN = "no_plan"
CODE_UNKNOWN_PLAN = "unknown_plan"
CODE_UNKNOWN_FEATURE = "unknown_feature"
CODE_SUBSCRIPTION_INACTIVE = "subscription_inactive"
CODE_NOT_ENTITLED = "not_entitled"
CODE_DENIED = "denied"

# --- authority ---------------------------------------------------------------

# The canonical permission that authorizes granting/revoking an entitlement
# override for an org. The RBAC catalog is open (identity/rbac README): the
# built-in preset packs confer it on ``owner`` through ``*:*``, and a tenant
# that brings its own preset pack names role administration - and override
# authority - in its own vocabulary (the custom-pack seam, issue #12). It is
# deliberately part of the ungated core: a tenant must always be able to
# negotiate a governed, time-boxed raise above its plan, independent of tier.
OVERRIDE_AUTHORITY_PERMISSION = "entitlement:override"

# --- the ungated core (self-administration floor) ----------------------------

# Permissions that administer and operate the org itself and that a plan can
# never strip. Without this floor a plan downgrade could lock a tenant out of
# its own org (no-lockout doctrine, identity/rbac): role administration
# (``roles:manage``), reading/operating its own org, teams, members, agents
# and sessions, invoking tools during agent runs, reading prompts, and the
# override-authority permission itself. Everything beyond this floor is a
# plan-gated capability unlocked by an entitled feature.
CORE_UNGATED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "org:read",
        "org:manage",
        "roles:read",
        "roles:manage",
        "member:read",
        "team:read",
        "agent:read",
        "agent:run",
        "session:read",
        "session:start",
        "session:stop",
        "session:manage",
        "tool:call",
        "prompt:read",
        OVERRIDE_AUTHORITY_PERMISSION,
    }
)


# --- time helpers ------------------------------------------------------------


def utcnow_iso() -> str:
    """ISO-8601 UTC timestamp for audit/expiry fields (deterministic format)."""
    return datetime.now(timezone.utc).isoformat()


def to_iso(value: "str | datetime") -> str:
    """Normalize a datetime or ISO string to a UTC ISO-8601 string."""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected an ISO-8601 string or datetime, got {value!r}")
    parse_iso(value)  # validate early (raises ValueError on garbage)
    return value


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string to an aware UTC datetime (naive = UTC)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_expired(expires_at: str, now: str) -> bool:
    """True when ``expires_at`` is strictly in the past relative to ``now``."""
    return parse_iso(expires_at) <= parse_iso(now)


# --- catalog data types ------------------------------------------------------


@dataclass(frozen=True)
class FeatureDef:
    """One catalog feature: kind, description, and the RBAC grants it unlocks.

    ``grants`` are concrete ``resource:action`` strings (no wildcards): the
    ``entitlement -> RBAC mapping`` half of the tiering contract. Empty for a
    pure surface flag (e.g. ``api_access``, ``sso``) that gates nothing in
    RBAC but is reported by the active-subscription feature gate.
    """

    key: str
    kind: str
    description: str = ""
    grants: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanEntitlement:
    """A plan's toggle for one feature: enabled, and a cap for limit features.

    ``limit`` is only meaningful when the referenced feature is limit-kind
    (catalog.py enforces this). ``None`` with ``enabled`` means unlimited.
    """

    feature: str
    enabled: bool
    limit: int | None = None


@dataclass(frozen=True)
class Plan:
    """A commercial tier: a named set of toggles over the feature registry."""

    key: str
    name: str
    description: str = ""
    entitlements: tuple[PlanEntitlement, ...] = ()

    def entitlement(self, feature: str) -> PlanEntitlement | None:
        for entry in self.entitlements:
            if entry.feature == feature:
                return entry
        return None


@dataclass(frozen=True)
class PlanCatalog:
    """A validated, immutable plan catalog (features registry + plans)."""

    version: str = "1"
    features: tuple[FeatureDef, ...] = ()
    plans: tuple[Plan, ...] = ()

    def feature(self, key: str) -> FeatureDef | None:
        for feature in self.features:
            if feature.key == key:
                return feature
        return None

    def plan(self, key: str) -> Plan | None:
        for plan in self.plans:
            if plan.key == key:
                return plan
        return None

    @property
    def plan_keys(self) -> tuple[str, ...]:
        return tuple(plan.key for plan in self.plans)

    @property
    def feature_keys(self) -> tuple[str, ...]:
        return tuple(feature.key for feature in self.features)


# --- tenant state ------------------------------------------------------------


@dataclass
class EntitlementProfile:
    """A tenant's plan assignment + subscription state (Org.id == tenant id).

    A tenant with no profile has no plan and is fail-closed (``no_plan``): a
    plan is never implied. ``subscription_status`` drives the active-
    subscription gate, which is deliberately separate from RBAC scope and
    permissions (issue #36 AC 4): an inactive subscription grants nothing no
    matter what roles or scope a subject would otherwise have.
    """

    org_id: str
    plan_key: str = ""
    subscription_status: str = SUBSCRIPTION_ACTIVE
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class Override:
    """A time-boxed, authority-granted departure from the plan for one feature.

    Whole-answer semantics (saas-rbac ``overrides.ts``): it states the entire
    answer for its feature - ``enabled: false`` is a real revocation, not an
    absence, and it never inherits from the plan, so a plan change underneath
    it cannot silently shift a negotiated agreement (downgrade-safe). Only
    honored while unexpired (``expires_at > now``).
    """

    id: str
    org_id: str
    feature: str
    enabled: bool = True
    limit: int | None = None
    expires_at: str = ""
    note: str = ""
    granted_by: str = ""
    granted_at: str = ""


@dataclass(frozen=True)
class FeatureState:
    """Resolved state of one feature for an org at a point in time.

    ``known`` is False when the org/profile/plan/feature could not be resolved;
    ``code`` then names the fail-closed cause (never a silent grant).
    """

    feature: str
    known: bool = False
    enabled: bool = False
    limit: int | None = None
    code: str | None = None


@dataclass(frozen=True)
class AccessDecision:
    """Outcome of one effective-access check (plan + overrides + roles).

    ``reason`` names the denying gate: ``"subscription"`` (no plan / unknown
    plan / inactive subscription - separate from RBAC), ``"scope"``,
    ``"permission"``, or ``"entitlement"`` (in scope and role-held, but the
    plan does not unlock it). ``None`` when allowed.
    """

    allowed: bool
    org_id: str
    subject: str
    node: ScopeNode
    permission: str
    reason: str | None = None
    code: str | None = None

    @property
    def denied(self) -> bool:
        return not self.allowed


@dataclass(frozen=True)
class EntitlementAuditEvent:
    """One immutable audit record of a plan-assignment or override change."""

    id: str
    org_id: str
    at: str
    actor: str
    action: str  # AUDIT_ACTIONS
    detail: str = ""
