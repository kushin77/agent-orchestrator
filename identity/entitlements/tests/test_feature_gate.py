"""The active-subscription feature gate - separate from RBAC scope/permission.

Issue #36 AC 4: the active-subscription gate is a gate of its own. A tenant
with no plan, an unknown plan, or an inactive subscription grants nothing no
matter what roles or scope a subject would otherwise have; pure feature flags
(``api_access``, ``sso``) gate surfaces, not RBAC permissions.
"""

import pytest

import entitlements as E
from rbac import ScopeNode

from helpers import iso, provision_org

ORG = "acme"
OWNER = "owner@acme.test"


def _org_node() -> ScopeNode:
    return ScopeNode(org_id=ORG)


def test_api_access_is_a_pure_surface_gate(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    assert not E.feature_enabled(estore, catalog, ORG, "api_access", now=iso())
    assert not E.feature_enabled(estore, catalog, ORG, "sso", now=iso())
    E.assign_plan(estore, catalog, ORG, "pro", actor=OWNER, now=iso())
    assert E.feature_enabled(estore, catalog, ORG, "api_access", now=iso())
    assert not E.feature_enabled(estore, catalog, ORG, "sso", now=iso())
    E.assign_plan(estore, catalog, ORG, "enterprise", actor=OWNER, now=iso())
    assert E.feature_enabled(estore, catalog, ORG, "sso", now=iso())


def test_limit_for_reflects_plan_and_disabled_state(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    assert E.limit_for(estore, catalog, ORG, "managed_agents", now=iso()) == 3
    # A disabled limit feature reports no allowance.
    assert E.limit_for(estore, catalog, ORG, "audit_log", now=iso()) is None
    E.assign_plan(estore, catalog, ORG, "enterprise", actor=OWNER, now=iso())
    assert E.limit_for(estore, catalog, ORG, "managed_agents", now=iso()) is None


def test_no_plan_and_unknown_feature_fail_closed(rbac_store, estore, catalog):
    # No profile yet: every feature resolves fail-closed to no_plan.
    state = E.feature_state(estore, catalog, "nobody", "audit_log", now=iso())
    assert not state.enabled and state.code == "no_plan"
    provision_org(rbac_store, estore, catalog, plan="free")
    # A feature outside the catalog is fail-closed, never silently granted.
    unknown = E.feature_state(estore, catalog, ORG, "teleport", now=iso())
    assert not unknown.enabled and unknown.code == "unknown_feature"
    assert not E.feature_enabled(estore, catalog, ORG, "teleport", now=iso())


def test_inactive_subscription_grants_nothing(rbac_store, estore, catalog):
    """The active-subscription gate is separate from RBAC scope/permission."""
    provision_org(rbac_store, estore, catalog, plan="pro")
    E.assign_plan(
        estore, catalog, ORG, "pro", actor=OWNER,
        subscription_status=E.SUBSCRIPTION_INACTIVE, now=iso(),
    )
    node = _org_node()
    # feature_enabled is False regardless of what the pro plan would grant.
    assert not E.feature_enabled(estore, catalog, ORG, "budgets", now=iso())
    # Even a *core* permission (roles:manage) is denied: roles and scope are
    # fine, but the subscription gate refuses first.
    decision = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, OWNER, node, "roles:manage", now=iso()
    )
    assert decision.denied
    assert decision.reason == "subscription"
    assert decision.code == "subscription_inactive"


def test_entitled_permissions_are_plan_bounded(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    eps = E.entitled_permissions(estore, catalog, ORG, now=iso())
    # Core + the free plan's managed_agents grants.
    assert "roles:manage" in eps
    assert "agent:create" in eps
    assert "budget:manage" not in eps
    assert "audit:read" not in eps
    E.assign_plan(estore, catalog, ORG, "enterprise", actor=OWNER, now=iso())
    eps2 = E.entitled_permissions(estore, catalog, ORG, now=iso())
    assert "budget:manage" in eps2
    assert "audit:read" in eps2
    assert "model:manage" in eps2
