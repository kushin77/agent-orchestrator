"""C-suite role boundaries: presets bind each seat; the guard refuses exits.

Issue #638 criterion 1: RBAC presets for ceo/cto/coo/cfo/cmo bind each role to
its scoped boundaries (allowlist + owned lanes + budget-cap ref) and an
out-of-boundary action is **refused** by the guard, never silently allowed.
"""

import pytest

from rbac import (
    BOUNDARY_BUDGET,
    BOUNDARY_CAPABILITIES,
    BOUNDARY_LANES,
    BOUNDARY_TOOLS,
    CSUITE_PACK_KEY,
    CSUITE_ROLE_IDS,
    BoundaryAction,
    BoundaryError,
    InMemoryStore,
    load_csuite_boundaries,
    load_csuite_pack,
    load_pack,
    seed_org,
)
from rbac.boundaries import boundary_from_card, guard_boundary
from rbac.model import is_permission


@pytest.fixture(scope="module")
def pack():
    return load_csuite_boundaries()


# --- the pack is seeded for all five seats ------------------------------------


def test_csuite_pack_declares_exactly_the_five_seats():
    role_pack = load_csuite_pack()
    assert role_pack.key == CSUITE_PACK_KEY
    assert role_pack.role_keys() == CSUITE_ROLE_IDS
    # Every declared role can administer the org in the pack's own vocabulary.
    assert role_pack.grants_permission(role_pack.role_admin_permission)


def test_csuite_pack_permissions_are_well_formed():
    role_pack = load_csuite_pack()
    for role in role_pack.roles:
        assert role.permissions, f"{role.key}: no permissions"
        for permission in role.permissions:
            assert is_permission(permission), f"{role.key}: {permission!r}"
    # Only the root (CEO) administers roles - the executive authority the
    # no-lockout invariant protects.
    holders = [
        r.key
        for r in role_pack.roles
        if any(p == "roles:manage" for p in r.permissions)
    ]
    assert holders == ["ceo"]


def test_csuite_pack_seeds_an_org():
    role_pack = load_csuite_pack()
    store = InMemoryStore()
    org = store.add_org(
        "acme",
        "Acme",
        tenant_type=CSUITE_PACK_KEY,
        role_admin_permission=role_pack.role_admin_permission,
    )
    roles = seed_org(store, org, role_pack)
    assert {r.key for r in roles} == set(CSUITE_ROLE_IDS)
    assert all(r.org_id == org.id for r in roles)


def test_csuite_pack_is_not_a_tenant_type():
    """The four built-in tenant packs keep their exact role mixes (no drift)."""
    role_pack = load_csuite_pack()
    assert role_pack.key not in __import__("rbac").BUILTIN_TENANT_TYPES
    assert "csuite" not in __import__("rbac").BUILTIN_TENANT_TYPES
    # And the built-ins are untouched by the named pack's presence.
    assert load_pack("startup").role_keys() == ("owner", "admin", "member")


# --- the boundaries are derived from the persona cards (read-only) ------------


def test_boundaries_cover_the_five_seats_with_card_values(pack):
    assert pack.root == "ceo"
    assert pack.role_ids() == CSUITE_ROLE_IDS
    expected = {
        "ceo": (300.0, "worker-bundle", "board"),
        "cto": (250.0, "worker-bundle", "ceo"),
        "coo": (100.0, "worker-bundle", "ceo"),
        "cfo": (50.0, "budget-finops-cap", "ceo"),
        "cmo": (200.0, "worker-bundle", "ceo"),
    }
    for role_id, (cap, policy, reports_to) in expected.items():
        boundary = pack.get(role_id)
        assert boundary.tenant == "platform"
        assert boundary.budget_cap_usd == cap, role_id
        assert boundary.budget_policy_ref == policy, role_id
        assert boundary.reports_to == reports_to, role_id
        assert boundary.owned_lanes, role_id
        assert boundary.tool_allowlist, role_id


def test_each_seat_owns_its_declared_lanes(pack):
    assert pack.get("ceo").owns_lane("strategy")
    assert pack.get("cto").owns_lane("architecture")
    assert pack.get("coo").owns_lane("pacing")
    assert pack.get("cfo").owns_lane("finops")
    assert pack.get("cmo").owns_lane("marketing")
    # And not another seat's.
    assert not pack.get("cfo").owns_lane("marketing")
    assert not pack.get("cmo").owns_lane("finops")


def test_cmo_is_the_only_seat_carrying_outbound_tools(pack):
    for tool in ("mail_send", "sms_send", "crm_contact"):
        assert pack.get("cmo").allows_tool(tool), tool
        for other in CSUITE_ROLE_IDS:
            if other == "cmo":
                continue
            assert not pack.get(other).allows_tool(tool), (other, tool)


# --- out-of-boundary actions are refused ---------------------------------------


def test_out_of_lane_action_is_refused_and_names_the_axis(pack):
    decision = guard_boundary(pack, "cfo", BoundaryAction(lane="marketing"))
    assert decision.refused
    assert decision.allowed is False
    assert decision.axis == BOUNDARY_LANES
    assert decision.violation.offender == "marketing"
    assert decision.violation.allowed == pack.get("cfo").owned_lanes


def test_out_of_allowlist_tool_is_refused(pack):
    # The CEO card does not carry sql_query; the CFO card does.
    decision = guard_boundary(pack, "ceo", BoundaryAction(tool="sql_query"))
    assert decision.refused
    assert decision.axis == BOUNDARY_TOOLS
    assert decision.violation.offender == "sql_query"


def test_unknown_capability_is_refused(pack):
    decision = guard_boundary(
        pack, "coo", BoundaryAction(capability="finops-meter")
    )
    assert decision.refused
    assert decision.axis == BOUNDARY_CAPABILITIES
    assert decision.violation.offender == "finops-meter"


def test_over_cap_spend_is_refused(pack):
    # CFO cap is 50; 50.01 is over, 50.0 is exactly at the ceiling (allowed).
    over = guard_boundary(pack, "cfo", BoundaryAction(spend_usd=50.01))
    assert over.refused
    assert over.axis == BOUNDARY_BUDGET
    assert over.violation.offender == 50.01
    assert over.violation.allowed == 50.0

    at_cap = guard_boundary(pack, "cfo", BoundaryAction(spend_usd=50.0))
    assert at_cap.allowed


@pytest.mark.parametrize("role_id", CSUITE_ROLE_IDS)
def test_in_boundary_action_is_allowed_for_every_seat(pack, role_id):
    boundary = pack.get(role_id)
    decision = guard_boundary(
        pack,
        role_id,
        BoundaryAction(
            lane=boundary.owned_lanes[0],
            tool=boundary.tool_allowlist[0],
            capability=boundary.capability_set[0],
            spend_usd=boundary.budget_cap_usd,
        ),
    )
    assert decision.allowed
    assert decision.axis is None
    assert decision.violation is None


def test_unknown_role_is_refused_not_defaulted(pack):
    with pytest.raises(BoundaryError):
        pack.get("cmo2")
    with pytest.raises(BoundaryError):
        guard_boundary(pack, "intern", BoundaryAction(lane="strategy"))


def test_boundary_from_card_refuses_a_card_missing_a_boundary_field():
    with pytest.raises(BoundaryError):
        boundary_from_card({"id": "ghost", "monthlyBudgetCapUsd": 1})
