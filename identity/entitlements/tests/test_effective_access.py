"""Effective-access evaluation: effective = f(plan entitlements ∩ org roles).

Covers the capability tiering (a plan caps even a ``*:*`` Owner), the ungated
self-administration core, the separate scope gate, fail-closed on unknown/no
plan, and that agent subjects ride the same evaluation path as console users.
"""

import pytest

import entitlements as E
from entitlements import UnknownPlanError
from rbac import ScopeNode
from rbac.bindings import grant_role
from rbac.presets import seed_org

from helpers import iso, provision_org

ORG = "acme"
OWNER = "owner@acme.test"
ADMIN = "admin@acme.test"
MEMBER = "member@acme.test"


def _org_node(org_id: str = ORG) -> ScopeNode:
    return ScopeNode(org_id=org_id)


def _extra_role(rbac_store, org_id: str, key: str, name: str, permissions):
    """Create a non-system custom role in the org."""
    return rbac_store.create_role(
        org_id=org_id, key=key, name=name, permissions=tuple(permissions)
    )


def test_owner_on_free_is_capped_by_plan(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    node = _org_node()
    # Core self-administration floor: never plan-stripped.
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "roles:manage", now=iso())
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "org:manage", now=iso())
    # managed_agents is enabled on free -> lifecycle allowed.
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "agent:create", now=iso())
    # Premium capabilities are off on free -> even *:* Owner is capped.
    denied = E.evaluate_permission(estore, rbac_store, catalog, ORG, OWNER, node, "budget:manage", now=iso())
    assert denied.denied and denied.reason == "entitlement" and denied.code == "not_entitled"
    assert not E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "audit:read", now=iso())
    assert not E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "model:manage", now=iso())


def test_upgrade_to_pro_unlocks_plan_gated_permissions(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="free")
    node = _org_node()
    assert not E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "budget:manage", now=iso())
    # Upgrading the plan raises the tenant's capabilities; overrides untouched.
    E.assign_plan(estore, catalog, ORG, "pro", actor=OWNER, now=iso())
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "budget:manage", now=iso())
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "audit:read", now=iso())
    assert E.is_allowed(estore, rbac_store, catalog, ORG, OWNER, node, "model:manage", now=iso())


def test_intersection_requires_role_and_plan(rbac_store, estore, catalog):
    """A permission is effective only when BOTH the role and the plan grant it."""
    provision_org(rbac_store, estore, catalog, plan="free")
    node = _org_node()
    # Free admin holds budget:manage by role but the plan does not unlock it.
    grant_role(rbac_store, ORG, ADMIN, "admin")
    free_admin = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, ADMIN, node, "budget:manage", now=iso()
    )
    assert free_admin.denied and free_admin.reason == "entitlement"
    # Pro admin: plan unlocks it -> allowed.
    E.assign_plan(estore, catalog, ORG, "pro", actor=OWNER, now=iso())
    assert E.is_allowed(estore, rbac_store, catalog, ORG, ADMIN, node, "budget:manage", now=iso())
    # Pro member: plan unlocks it but the role does not hold it -> permission denial.
    grant_role(rbac_store, ORG, MEMBER, "member")
    pro_member = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, MEMBER, node, "budget:manage", now=iso()
    )
    assert pro_member.denied and pro_member.reason == "permission"


def test_out_of_scope_subject_is_denied_before_entitlement(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="enterprise")
    node = _org_node()
    decision = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, "stranger@elsewhere.test", node, "org:read", now=iso()
    )
    assert decision.denied and decision.reason == "scope"


def test_unknown_org_and_no_plan_fail_closed(rbac_store, estore, catalog):
    node = _org_node("ghost")
    # No org at all (RBAC scope would also fail, but the plan gate is first).
    assert not E.is_allowed(estore, rbac_store, catalog, "ghost", OWNER, node, "org:read", now=iso())
    # A real org with no plan assignment grants nothing.
    ghost = rbac_store.add_org("ghost", "Ghost", tenant_type="enterprise")
    seed_org(rbac_store, ghost)
    grant_role(rbac_store, "ghost", OWNER, "owner")
    decision = E.evaluate_permission(
        estore, rbac_store, catalog, "ghost", OWNER, node, "roles:manage", now=iso()
    )
    assert decision.denied and decision.reason == "subscription" and decision.code == "no_plan"


def test_unknown_plan_on_profile_fails_closed(rbac_store, estore, catalog):
    """A corrupted profile naming an unknown plan grants nothing (never a fallback)."""
    from entitlements import EntitlementProfile

    provision_org(rbac_store, estore, catalog, plan="pro")
    estore.save_profile(
        EntitlementProfile(
            org_id=ORG,
            plan_key="no-such-plan",
            subscription_status=E.SUBSCRIPTION_ACTIVE,
            created_at=iso(),
            updated_at=iso(),
        )
    )
    node = _org_node()
    decision = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, OWNER, node, "org:read", now=iso()
    )
    assert decision.denied and decision.reason == "subscription" and decision.code == "unknown_plan"
    assert E.feature_state(estore, catalog, ORG, "budgets", now=iso()).code == "unknown_plan"


def test_assign_plan_refuses_unknown_plan(rbac_store, estore, catalog):
    provision_org(rbac_store, estore, catalog, plan="pro")
    with pytest.raises(UnknownPlanError):
        E.assign_plan(estore, catalog, ORG, "no-such-plan", actor=OWNER, now=iso())


def test_wildcard_grant_is_capped_by_entitled_set(rbac_store, estore, catalog):
    """A role holding agent:* cannot act beyond what the plan unlocks."""
    provision_org(rbac_store, estore, catalog, plan="free")
    _extra_role(rbac_store, ORG, "ops", "Operator", ["agent:*", "session:read"])
    grant_role(rbac_store, ORG, MEMBER, "ops")
    node = _org_node()
    # agent:run is core; agent:create is unlocked by managed_agents (free on).
    assert E.is_allowed(estore, rbac_store, catalog, ORG, MEMBER, node, "agent:run", now=iso())
    assert E.is_allowed(estore, rbac_store, catalog, ORG, MEMBER, node, "agent:create", now=iso())
    # agent:terminate is not in the core nor unlocked by any entitled feature.
    decision = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, MEMBER, node, "agent:terminate", now=iso()
    )
    assert decision.denied and decision.reason == "entitlement"


def test_agent_subject_rides_the_same_evaluation_path(rbac_store, estore, catalog):
    """The same effective-access check gates agent tool calls (issue #36 AC 5)."""
    provision_org(rbac_store, estore, catalog, plan="free")
    team = rbac_store.add_team(ORG, "Platform")
    agent = rbac_store.add_agent(ORG, team.id, "classify-agent")
    node = ScopeNode(org_id=ORG, team_id=team.id, agent_id=agent.id)
    # agent-operator is a team-level role holding tool:call.
    grant_role(rbac_store, ORG, "svc@acme.test", "agent-operator", team_id=team.id)
    # In scope, tool:call is core -> the agent's tool call is allowed.
    assert E.is_allowed(estore, rbac_store, catalog, ORG, "svc@acme.test", node, "tool:call", now=iso())
    # The same call from a subject with no binding in that team is out of scope.
    decision = E.evaluate_permission(
        estore, rbac_store, catalog, ORG, "outsider@acme.test", node, "tool:call", now=iso()
    )
    assert decision.denied and decision.reason == "scope"
