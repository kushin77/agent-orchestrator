"""The role map: what it covers, and every way a declaration can be refused."""

from __future__ import annotations

import pytest

from integrations.erp.auth import roles
from integrations.erp.auth.model import Refused


@pytest.fixture(scope="module")
def role_map():
    return roles.load_default()


def test_the_shipped_map_covers_kinds_and_roles(role_map):
    assert len(role_map.kinds) == 12
    assert "System Manager" in role_map.role_names
    assert "Sales User" in role_map.role_names


def test_coverage_runs_both_ways(role_map):
    # Every declared kind is granted by at least one role — the invariant that
    # keeps a kind from being declared but unreachable.
    for kind in role_map.kinds:
        assert any(
            g.kind == kind for grants in role_map.roles.values() for g in grants
        ), f"{kind} is covered by no role"


def test_the_permission_string_is_derived_into_the_contract_language(role_map):
    assert role_map.permission_for("sales-invoice", "read") == "erp.sales-invoice:read"
    # ...and the contract agrees it is a permission it can parse.
    assert roles.contract.rbac().is_permission("erp.sales-invoice:read")


def test_an_action_wildcard_is_honoured_by_the_contract(role_map):
    assert role_map.granted(("System Manager",), "sales-invoice", "cancel") is True
    assert role_map.granted(("System Manager",), "warehouse", "delete") is True


def test_a_read_only_role_cannot_write(role_map):
    assert role_map.granted(("Auditor",), "sales-invoice", "read") is True
    assert role_map.granted(("Auditor",), "sales-invoice", "write") is False
    assert role_map.granted(("Auditor",), "sales-invoice", "submit") is False


def test_roles_union(role_map):
    assert role_map.granted(("Auditor",), "sales-order", "submit") is False
    assert role_map.granted(("Auditor", "Sales Manager"), "sales-order", "submit") is True


def test_an_unknown_role_is_refused_by_name_with_the_declared_ones_named(role_map):
    with pytest.raises(Refused) as caught:
        role_map.granted(("No Such Role",), "sales-invoice", "read")
    assert caught.value.code == "unknown-role"
    assert "System Manager" in caught.value.detail


def test_an_unknown_kind_is_refused_by_name(role_map):
    with pytest.raises(Refused) as caught:
        role_map.granted(("Sales User",), "no-such-kind", "read")
    assert caught.value.code == "unknown-kind"


def test_an_unknown_action_is_refused_by_name(role_map):
    with pytest.raises(Refused) as caught:
        role_map.granted(("Sales User",), "sales-invoice", "teleport")
    assert caught.value.code == "unknown-action"


def test_unknown_roles_are_listed_not_dropped(role_map):
    assert role_map.unknown_roles(("Sales User", "Typo Role")) == ("Typo Role",)
    assert role_map.unknown_roles(("Sales User",)) == ()
    # Asking for the grants of an undeclared role is refused rather than
    # answered with an empty tuple: "no such role" and "no grants" are
    # different answers, and only one of them is a permission decision.
    with pytest.raises(Refused) as caught:
        role_map.grants("No Such Role")
    assert caught.value.code == "unknown-role"


def test_a_kind_wildcard_is_refused_because_the_contract_cannot_honour_it():
    # `erp.*` is the literal resource `erp.*` in the contract's language, so a
    # declaration claiming to cover every kind would produce a grant that reads
    # as broader than it is and never matches. Refused by name, not accepted.
    with pytest.raises(Refused) as caught:
        roles.load({"version": 1, "kinds": ["*"], "roles": {"R": [{"kind": "*", "action": "read"}]}})
    assert caught.value.code == "unknown-kind"


def test_a_kind_covered_by_no_role_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load(
            {
                "version": 1,
                "kinds": ["a", "b"],
                "roles": {"R": [{"kind": "a", "action": "read"}]},
            }
        )
    assert caught.value.code == "declaration-invalid"
    assert "b" in caught.value.detail


def test_a_grant_on_an_undeclared_kind_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load({"version": 1, "kinds": ["a"], "roles": {"R": [{"kind": "b", "action": "read"}]}})
    assert caught.value.code == "unknown-kind"


def test_a_duplicate_grant_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load(
            {
                "version": 1,
                "kinds": ["a"],
                "roles": {"R": [{"kind": "a", "action": "read"}, {"kind": "a", "action": "read"}]},
            }
        )
    assert caught.value.code == "declaration-invalid"


def test_a_role_with_no_permissions_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load({"version": 1, "kinds": ["a"], "roles": {"R": []}})
    assert caught.value.code == "declaration-invalid"


def test_an_empty_role_map_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load({"version": 1, "kinds": ["a"], "roles": {}})
    assert caught.value.code == "empty-role-map"


def test_a_wrong_version_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load({"version": 99, "kinds": ["a"], "roles": {"R": [{"kind": "a", "action": "read"}]}})
    assert caught.value.code == "declaration-invalid"


def test_a_negative_permlevel_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load(
            {
                "version": 1,
                "kinds": ["a"],
                "roles": {"R": [{"kind": "a", "action": "read", "permlevel": -1}]},
            }
        )
    assert caught.value.code == "declaration-invalid"


def test_a_missing_file_is_refused():
    with pytest.raises(Refused) as caught:
        roles.load("/nonexistent/roles.json")
    assert caught.value.code == "declaration-invalid"


def test_the_shipped_map_round_trips_through_its_canonical_form(role_map):
    again = roles.load(role_map.to_json())
    assert again.to_json() == role_map.to_json()
