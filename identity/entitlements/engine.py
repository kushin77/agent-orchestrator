"""Plan assignment, feature resolution and effective-access evaluation.

This module is where the capability-tiering doctrine lives. Two gates that the
issue (#36) keeps deliberately separate, plus the intersection that ties them:

1. **The active-subscription / plan gate** (``feature_state``,
   ``feature_enabled``, ``evaluate_permission`` first step) is *separate from
   RBAC scope and permissions*: a tenant with no plan, an unknown plan, or an
   inactive subscription grants nothing, no matter what roles or scope a
   subject would otherwise have. Fail-closed: a plan is never implied and an
   unknown plan/feature is never a silent grant.
2. **The entitlement gate** (last step of ``evaluate_permission``): a
   permission that a role grants is only *effective* when the org's entitled
   capabilities unlock it - the org's effective permissions are
   ``f(plan entitlements ∩ org roles)``. Only the org self-administration core
   (``CORE_UNGATED_PERMISSIONS``, model.py) is exempt from the plan.

``evaluate_permission`` composes the subscription gate, then the RBAC scope
gate, then the RBAC permission gate, then the entitlement gate - in that
order, never raising for a deny, always returning an ``AccessDecision`` with
the reason and machine code of the gate that refused.
"""

from __future__ import annotations

from rbac.model import ScopeNode
from rbac.resolve import authorize, resolve_scope

from entitlements.errors import (
    NoPlanError,
    UnknownPlanError,
)
from entitlements.model import (
    ACTION_PLAN_ASSIGN,
    CODE_DENIED,
    CODE_NOT_ENTITLED,
    CODE_NO_PLAN,
    CODE_SUBSCRIPTION_INACTIVE,
    CODE_UNKNOWN_FEATURE,
    CODE_UNKNOWN_PLAN,
    CORE_UNGATED_PERMISSIONS,
    REASON_ENTITLEMENT,
    REASON_PERMISSION,
    REASON_SCOPE,
    REASON_SUBSCRIPTION,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_STATUSES,
    AccessDecision,
    EntitlementAuditEvent,
    EntitlementProfile,
    FeatureState,
    Override,
    is_expired,
    utcnow_iso,
)


def _profile_plan(store, catalog, org_id: str):
    """Resolve ``(profile, plan)`` for an org, or raise on a fail-closed cause.

    A missing profile (no plan assignment) raises ``NoPlanError``; an unknown
    plan key on the profile raises ``UnknownPlanError``.
    """
    profile = store.profile(org_id)
    if profile is None:
        raise NoPlanError(org_id)
    plan = catalog.plan(profile.plan_key)
    if plan is None:
        raise UnknownPlanError(profile.plan_key)
    return profile, plan


def _active_overrides(store, org_id: str, now: str) -> list[Override]:
    """Non-expired overrides for an org (whole-answer, honored at ``now``)."""
    return [
        o for o in store.overrides_for_org(org_id) if not is_expired(o.expires_at, now)
    ]


# --- plan assignment (audited) -----------------------------------------------


def assign_plan(
    store,
    catalog,
    org_id: str,
    plan_key: str,
    *,
    actor: str,
    subscription_status: str = SUBSCRIPTION_ACTIVE,
    note: str = "",
    now: str | None = None,
) -> EntitlementProfile:
    """Assign (or change) an org's plan, recording an audit event.

    Refuses an empty org id, an unknown plan key (fail closed), an unknown
    subscription status, or an empty actor (the audit trail must name who
    acted). Plan changes never touch existing overrides - an override granted
    under a richer plan stays in force until it expires, so a downgrade cannot
    silently rescind a negotiated, time-boxed grant (downgrade-safe).
    """
    if not org_id:
        raise ValueError("org_id must be non-empty")
    if catalog.plan(plan_key) is None:
        raise UnknownPlanError(plan_key)
    if subscription_status not in SUBSCRIPTION_STATUSES:
        raise ValueError(
            f"unknown subscription status {subscription_status!r}; "
            f"expected one of {', '.join(SUBSCRIPTION_STATUSES)}"
        )
    if not actor:
        raise ValueError("actor must be non-empty (audit integrity)")
    ts = now or utcnow_iso()

    existing = store.profile(org_id)
    profile = EntitlementProfile(
        org_id=org_id,
        plan_key=plan_key,
        subscription_status=subscription_status,
        created_at=existing.created_at if existing else ts,
        updated_at=ts,
    )
    store.save_profile(profile)

    detail = f"plan={plan_key} subscription_status={subscription_status}"
    if note:
        detail = f"{detail} note={note}"
    store.record_event(
        EntitlementAuditEvent(
            id=store._new_id("evt"),
            org_id=org_id,
            at=ts,
            actor=actor,
            action=ACTION_PLAN_ASSIGN,
            detail=detail,
        )
    )
    return profile


# --- feature resolution ------------------------------------------------------


def feature_state(
    store,
    catalog,
    org_id: str,
    feature: str,
    *,
    now: str | None = None,
) -> FeatureState:
    """Resolve the state of one feature for an org at ``now`` (never raises).

    Order: org profile/plan (``no_plan`` / ``unknown_plan``), feature
    existence (``unknown_feature``), subscription status
    (``subscription_inactive``), plan toggle, then any non-expired override
    (whole answer replaces the plan value). Unknown anything is a fail-closed
    disabled state with a code, never a silent grant.
    """
    ts = now or utcnow_iso()

    profile = store.profile(org_id)
    if profile is None:
        return FeatureState(feature=feature, known=False, code=CODE_NO_PLAN)
    plan = catalog.plan(profile.plan_key)
    if plan is None:
        return FeatureState(feature=feature, known=False, code=CODE_UNKNOWN_PLAN)
    feature_def = catalog.feature(feature)
    if feature_def is None:
        return FeatureState(feature=feature, known=False, code=CODE_UNKNOWN_FEATURE)
    if profile.subscription_status != SUBSCRIPTION_ACTIVE:
        return FeatureState(
            feature=feature,
            known=True,
            enabled=False,
            limit=None,
            code=CODE_SUBSCRIPTION_INACTIVE,
        )

    entry = plan.entitlement(feature)
    enabled = bool(entry and entry.enabled)
    limit: int | None = entry.limit if entry else None

    for override in _active_overrides(store, org_id, ts):
        if override.feature == feature:
            # Whole answer: the override replaces the plan value entirely.
            enabled = override.enabled
            limit = override.limit
            break

    return FeatureState(
        feature=feature,
        known=True,
        enabled=enabled,
        limit=limit if enabled else None,
    )


def feature_enabled(
    store,
    catalog,
    org_id: str,
    feature: str,
    *,
    now: str | None = None,
) -> bool:
    """Whether ``feature`` is currently entitled for the org (active gate)."""
    return feature_state(store, catalog, org_id, feature, now=now).enabled


def limit_for(
    store,
    catalog,
    org_id: str,
    feature: str,
    *,
    now: str | None = None,
) -> int | None:
    """The numeric allowance of an enabled limit feature (None when disabled)."""
    return feature_state(store, catalog, org_id, feature, now=now).limit


def entitled_permissions(
    store,
    catalog,
    org_id: str,
    *,
    now: str | None = None,
) -> frozenset[str]:
    """The org's entitled permission set (EPS): union of grants of every
    enabled feature, plus the ungated self-administration core.

    Raises when the org has no profile or an unknown plan (configuration
    errors); pure evaluation (``evaluate_permission``) never raises and calls
    the internal total variant instead.
    """
    ts = now or utcnow_iso()
    _profile_plan(store, catalog, org_id)  # raises on no plan / unknown plan
    eps, _code = _entitled_permissions(store, catalog, org_id, ts)
    return eps


def _entitled_permissions(store, catalog, org_id: str, ts: str):
    """Total EPS computation: ``(permissions, fail-closed code | None)``."""
    profile = store.profile(org_id)
    if profile is None:
        return frozenset(), CODE_NO_PLAN
    plan = catalog.plan(profile.plan_key)
    if plan is None:
        return frozenset(), CODE_UNKNOWN_PLAN
    if profile.subscription_status != SUBSCRIPTION_ACTIVE:
        return frozenset(), CODE_SUBSCRIPTION_INACTIVE

    granted: set[str] = set(CORE_UNGATED_PERMISSIONS)
    override_by_feature = {
        o.feature: o for o in _active_overrides(store, org_id, ts)
    }

    # Start from the plan's entitled features, then let any active override
    # replace the whole answer for its feature before collecting grants.
    resolved: dict[str, bool] = {}
    for feature_def in catalog.features:
        entry = plan.entitlement(feature_def.key)
        enabled = bool(entry and entry.enabled)
        override = override_by_feature.get(feature_def.key)
        if override is not None:
            enabled = override.enabled  # whole answer
        resolved[feature_def.key] = enabled

    for feature_def in catalog.features:
        if resolved.get(feature_def.key):
            granted.update(feature_def.grants)

    return frozenset(granted), None


# --- effective-access evaluation ----------------------------------------------


def evaluate_permission(
    store,
    rbac_store,
    catalog,
    org_id: str,
    subject: str,
    node: ScopeNode,
    permission: str,
    *,
    now: str | None = None,
) -> AccessDecision:
    """Effective-access check: plan + overrides + roles, never raising.

    Gate order (each is a distinct, attributable denial):

    1. **subscription** - org profile/plan valid and subscription active
       (``no_plan`` / ``unknown_plan`` / ``subscription_inactive``). This gate
       is separate from RBAC scope and permissions (issue #36 AC 4).
    2. **scope** - the subject can act in ``node`` at all (RBAC scope gate).
    3. **permission** - the subject's roles grant ``permission`` there.
    4. **entitlement** - the permission is either in the ungated core or
       unlocked by an entitled feature (the plan caps even a ``*:*`` Owner).

    A subject whose role grants a plan-gated permission the org is not
    entitled to is denied with ``reason == "entitlement"`` - effective
    permissions are ``f(plan entitlements ∩ org roles)``.
    """
    ts = now or utcnow_iso()

    profile = store.profile(org_id)
    if profile is None:
        return AccessDecision(
            allowed=False, org_id=org_id, subject=subject, node=node,
            permission=permission, reason=REASON_SUBSCRIPTION, code=CODE_NO_PLAN,
        )
    if catalog.plan(profile.plan_key) is None:
        return AccessDecision(
            allowed=False, org_id=org_id, subject=subject, node=node,
            permission=permission, reason=REASON_SUBSCRIPTION, code=CODE_UNKNOWN_PLAN,
        )
    if profile.subscription_status != SUBSCRIPTION_ACTIVE:
        return AccessDecision(
            allowed=False, org_id=org_id, subject=subject, node=node,
            permission=permission, reason=REASON_SUBSCRIPTION,
            code=CODE_SUBSCRIPTION_INACTIVE,
        )

    # Gate 2: scope (never reaches gates 3/4 when the subject cannot act here).
    resolution = resolve_scope(rbac_store, subject, node)
    if not resolution.ok:
        return AccessDecision(
            allowed=False, org_id=org_id, subject=subject, node=node,
            permission=permission, reason=REASON_SCOPE, code=resolution.reason,
        )

    # Gate 3: permission within the resolved scope.
    if not authorize(rbac_store, subject, resolution, permission):
        return AccessDecision(
            allowed=False, org_id=org_id, subject=subject, node=node,
            permission=permission, reason=REASON_PERMISSION, code=CODE_DENIED,
        )

    # Gate 4: entitlement - the plan caps even wildcard role grants.
    eps, _code = _entitled_permissions(store, catalog, org_id, ts)
    if permission in CORE_UNGATED_PERMISSIONS or permission in eps:
        return AccessDecision(
            allowed=True, org_id=org_id, subject=subject, node=node,
            permission=permission,
        )
    return AccessDecision(
        allowed=False, org_id=org_id, subject=subject, node=node,
        permission=permission, reason=REASON_ENTITLEMENT, code=CODE_NOT_ENTITLED,
    )


def is_allowed(
    store,
    rbac_store,
    catalog,
    org_id: str,
    subject: str,
    node: ScopeNode,
    permission: str,
    *,
    now: str | None = None,
) -> bool:
    """Convenience: whether the effective-access check allows ``permission``."""
    return evaluate_permission(
        store, rbac_store, catalog, org_id, subject, node, permission, now=now
    ).allowed
