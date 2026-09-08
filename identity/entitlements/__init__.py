"""Plan -> entitlement -> override tiering - the capability-tiering contract.

Self-contained package under ``identity/entitlements/`` (issue #36, work item
32). Importable as ``entitlements`` when ``identity/`` is on ``sys.path`` (the
tests arrange this in ``tests/conftest.py``) and as ``identity.entitlements``
once a later identity-phase lane adds an ``identity/__init__.py``.

Public surface
--------------

- ``model`` - closed vocabularies and pure data types (``PlanCatalog``,
  ``EntitlementProfile``, ``Override``, ``FeatureState``, ``AccessDecision``,
  ``EntitlementAuditEvent``) plus the ungated core permission set.
- ``catalog`` - parse/validate the plan catalog (``plans/catalog.yaml``).
- ``store`` - the in-memory persistence seam (profiles, overrides, audit log).
- ``engine`` - plan assignment and evaluation: ``assign_plan``,
  ``feature_state`` / ``feature_enabled`` / ``limit_for``,
  ``entitled_permissions`` and ``evaluate_permission`` (the effective-access
  check: subscription gate, then RBAC scope, then RBAC permission, then the
  entitlement intersection).
- ``overrides`` - ``grant_override`` / ``revoke_override``: time-boxed,
  authority-gated (``entitlement:override``), audited override changes.

Consumed, never redefined: the RBAC contract (``identity/rbac``, issue #12) -
``resource:action`` permissions, ``ScopeNode``, the scope/permission gates,
preset packs per tenant type, Org == tenant.
"""

from entitlements.model import (
    ACTION_OVERRIDE_GRANT,
    ACTION_OVERRIDE_REVOKE,
    ACTION_PLAN_ASSIGN,
    AUDIT_ACTIONS,
    CORE_UNGATED_PERMISSIONS,
    FEATURE_KIND_FEATURE,
    FEATURE_KIND_LIMIT,
    FEATURE_KINDS,
    OVERRIDE_AUTHORITY_PERMISSION,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_INACTIVE,
    SUBSCRIPTION_STATUSES,
    AccessDecision,
    EntitlementAuditEvent,
    EntitlementProfile,
    FeatureDef,
    FeatureState,
    Override,
    Plan,
    PlanCatalog,
    PlanEntitlement,
)

from entitlements.catalog import load_catalog, load_default_catalog, parse_catalog
from entitlements.store import InMemoryStore
from entitlements.engine import (
    assign_plan,
    entitled_permissions,
    evaluate_permission,
    feature_enabled,
    feature_state,
    is_allowed,
    limit_for,
)
from entitlements.overrides import grant_override, revoke_override
from entitlements.errors import (
    EntitlementError,
    NoPlanError,
    OverrideAuthorityError,
    OverrideExpiryError,
    UnknownFeatureError,
    UnknownOrgError,
    UnknownPlanError,
)

__all__ = [
    # model
    "ACTION_OVERRIDE_GRANT",
    "ACTION_OVERRIDE_REVOKE",
    "ACTION_PLAN_ASSIGN",
    "AUDIT_ACTIONS",
    "CORE_UNGATED_PERMISSIONS",
    "FEATURE_KIND_FEATURE",
    "FEATURE_KIND_LIMIT",
    "FEATURE_KINDS",
    "OVERRIDE_AUTHORITY_PERMISSION",
    "SUBSCRIPTION_ACTIVE",
    "SUBSCRIPTION_INACTIVE",
    "SUBSCRIPTION_STATUSES",
    "AccessDecision",
    "EntitlementAuditEvent",
    "EntitlementProfile",
    "FeatureDef",
    "FeatureState",
    "Override",
    "Plan",
    "PlanCatalog",
    "PlanEntitlement",
    # catalog
    "load_catalog",
    "load_default_catalog",
    "parse_catalog",
    # store
    "InMemoryStore",
    # engine
    "assign_plan",
    "entitled_permissions",
    "evaluate_permission",
    "feature_enabled",
    "feature_state",
    "is_allowed",
    "limit_for",
    # overrides
    "grant_override",
    "revoke_override",
    # errors
    "EntitlementError",
    "NoPlanError",
    "OverrideAuthorityError",
    "OverrideExpiryError",
    "UnknownFeatureError",
    "UnknownOrgError",
    "UnknownPlanError",
]
