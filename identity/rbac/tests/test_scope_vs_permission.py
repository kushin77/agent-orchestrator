"""Scope gate vs permission gate - the platform's central RBAC doctrine.

A principal with a permission must never be able to apply it outside its
resolved scope: no cross-tenant fallback and no cross-team fallback (the
cross-tenant read incident doctrine). These tests prove the two gates are
separate: wherever a permission-only check would allow - because the role
string matches - the scope gate still denies first.
"""

from rbac import (
    InMemoryStore,
    ScopeNode,
    authorize,
    effective_permissions,
    grant_role,
    guard,
    resolve_scope,
    seed_org,
)


def _build_two_orgs():
    """Acme (teams eng/data) and Globex (team ops), each seeded with platform roles."""
    store = InMemoryStore()

    acme = store.add_org("acme", "Acme", tenant_type="platform")
    seed_org(store, acme)
    store.add_team("acme", "Engineering", team_id="eng")
    store.add_team("acme", "Data", team_id="data")
    store.add_agent("acme", "eng", "writer-agent", agent_id="agent_eng")
    store.add_agent("acme", "data", "analyst-agent", agent_id="agent_data")

    globex = store.add_org("globex", "Globex", tenant_type="platform")
    seed_org(store, globex)
    store.add_team("globex", "Operations", team_id="ops")
    store.add_agent("globex", "ops", "ops-agent", agent_id="agent_ops")

    return store


# --- cross-tenant: permission present in one org never reaches another --------


def test_cross_tenant_run_is_denied_at_the_scope_gate():
    store = _build_two_orgs()
    # u_admin is org-wide admin in acme; the admin role grants agent:run.
    grant_role(store, "acme", "u_admin", "admin")

    node_acme = ScopeNode("acme", "eng", "agent_eng")
    node_globex = ScopeNode("globex", "ops", "agent_ops")

    # Sanity: inside acme the very same request is allowed.
    assert guard(store, "u_admin", node_acme, "agent:run").allowed

    # Globex also has an "admin" role granting agent:run, but u_admin holds no
    # binding there. The scope gate denies before the permission gate is ever
    # reached - a permission from acme cannot be applied in globex.
    decision = guard(store, "u_admin", node_globex, "agent:run")
    assert not decision.allowed
    assert decision.reason == "scope"
    assert decision.code == "out_of_scope"


def test_matching_role_string_in_other_org_is_not_a_grant():
    """The unsafe pattern this design forbids: matching only on role string."""
    store = _build_two_orgs()
    grant_role(store, "acme", "u", "admin")

    # Both orgs define the same role key with the same permission.
    admin_acme = store.find_role_by_key("acme", "admin")
    admin_globex = store.find_role_by_key("globex", "admin")
    assert admin_acme is not None and admin_globex is not None
    assert "agent:run" in admin_acme.permissions
    assert "agent:run" in admin_globex.permissions

    # The permission gate alone would allow: the resolved acme scope grants it.
    res_acme = resolve_scope(store, "u", ScopeNode("acme", "eng", "agent_eng"))
    assert res_acme.ok
    assert authorize(store, "u", res_acme, "agent:run")

    # But scope resolution for globex fails: no binding there, ever.
    res_globex = resolve_scope(store, "u", ScopeNode("globex", "ops", "agent_ops"))
    assert not res_globex.ok
    assert res_globex.code == "out_of_scope"
    # A scope-less effective-permission read is empty, so nothing can match.
    assert effective_permissions(store, "u", ScopeNode("globex", "ops", "agent_ops")) == frozenset()


# --- cross-team (same org): a team-scoped grant stays inside the team ---------


def test_team_scoped_operator_cannot_run_agents_in_another_team():
    store = _build_two_orgs()
    # agent-operator is a team-level role; grant it inside team eng only.
    grant_role(store, "acme", "u_op", "agent-operator", team_id="eng")

    # In-team: allowed.
    assert guard(store, "u_op", ScopeNode("acme", "eng", "agent_eng"), "agent:run").allowed

    # Same permission string, different team in the SAME org: the scope gate
    # denies - the operator is not in scope for the data team.
    decision = guard(store, "u_op", ScopeNode("acme", "data", "agent_data"), "agent:run")
    assert not decision.allowed
    assert decision.reason == "scope"


def test_team_scoped_grant_does_not_reach_org_level_nodes():
    store = _build_two_orgs()
    grant_role(store, "acme", "u_op", "agent-operator", team_id="eng")

    resolution = resolve_scope(store, "u_op", ScopeNode("acme"))
    assert not resolution.ok
    assert resolution.code == "out_of_scope"


# --- the two gates answer different questions ---------------------------------


def test_scope_and_permission_are_independent_gates():
    store = _build_two_orgs()
    # An org-wide viewer IS in scope across every team of acme ...
    grant_role(store, "acme", "u_view", "viewer")
    node_data = ScopeNode("acme", "data", "agent_data")

    # ... but viewer does not grant agent:run: the permission gate denies,
    # with reason "permission" (not "scope").
    decision = guard(store, "u_view", node_data, "agent:run")
    assert not decision.allowed
    assert decision.reason == "permission"

    # The same subject can read the agent: permission holds inside scope.
    assert guard(store, "u_view", node_data, "agent:read").allowed


def test_out_of_scope_subject_has_no_effective_permissions():
    store = _build_two_orgs()
    assert (
        effective_permissions(store, "nobody", ScopeNode("acme", "eng", "agent_eng"))
        == frozenset()
    )


def test_guard_denies_malformed_permission_without_crashing():
    store = _build_two_orgs()
    grant_role(store, "acme", "u", "admin")
    decision = guard(store, "u", ScopeNode("acme", "eng", "agent_eng"), "not-a-permission")
    assert not decision.allowed
    assert decision.reason == "permission"
