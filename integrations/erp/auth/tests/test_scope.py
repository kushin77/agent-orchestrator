"""The middleware: the tenant gate, the platform's two gates, and field policy.

The controls in this file are the ones the acceptance criteria name: a tenant
sees only its own ERP data, role capabilities gate reads and writes, and a deny
really denies.
"""

from __future__ import annotations

import pytest

from integrations.erp.auth import platform_fixture as fx, policies, roles, scope
from integrations.erp.auth.model import Principal, Request

TENANT = fx.DEFAULT_TENANT
SUBJECT = fx.DEFAULT_SUBJECT
TEAM = fx.DEFAULT_TEAM


@pytest.fixture(scope="module")
def role_map():
    return roles.load_default()


@pytest.fixture(scope="module")
def policy_set(role_map):
    return policies.load_default(kinds=role_map.kinds)


def _principal(roles_=("Sales User",)) -> Principal:
    return Principal(tenant=TENANT, subject=SUBJECT, roles=roles_)


def _request(kind, action, **kwargs) -> Request:
    kwargs.setdefault("team", TEAM)
    return Request(tenant=TENANT, kind=kind, action=action, **kwargs)


def _store_with(role_map, kind, action):
    return fx.build(permissions=[role_map.permission_for(kind, action)])[0]


# --- the tenant gate --------------------------------------------------------


def test_a_read_is_allowed_when_scope_role_and_field_policy_all_agree(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(),
        _request("sales-invoice", "read", fields={"total": 100}),
    )
    assert decision.allowed is True
    assert dict(decision.projection) == {"total": 100}


def test_a_request_for_another_tenant_is_refused_even_holding_every_role(role_map, policy_set):
    """The invariant control: the privileged principal is the one that must fail."""
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(("System Manager",)),
        Request(tenant="some-other-tenant", kind="sales-invoice", action="read", team=TEAM),
    )
    assert decision.allowed is False
    assert decision.reason == "cross-tenant"
    assert TENANT in decision.detail and "some-other-tenant" in decision.detail


def test_the_refusal_names_the_claim_and_the_authority(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(),
        Request(tenant="elsewhere", kind="sales-invoice", action="read", team=TEAM),
    )
    assert "cannot widen" in decision.detail or "no role or policy" in decision.detail


def test_a_request_naming_no_tenant_is_refused(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(),
        Request(tenant="", kind="sales-invoice", action="read", team=TEAM),
    )
    assert decision.reason == "tenant-missing"


# --- the platform's two gates ----------------------------------------------


def test_an_out_of_scope_subject_is_refused_by_the_scope_gate(role_map, policy_set):
    """The tenant matches and the ERP role grants — and the platform says out of scope."""
    rbac = roles.contract.rbac()
    store = rbac.InMemoryStore()
    store.add_org(TENANT, "Acme", tenant_type="platform")
    store.add_team(TENANT, "ERP", team_id=TEAM)
    store.add_agent(TENANT, TEAM, SUBJECT, agent_id=SUBJECT)  # present, but no binding
    decision = scope.authorize(
        role_map, policy_set, store, _principal(), _request("sales-invoice", "read")
    )
    assert decision.reason == "scope-denied"


def test_an_in_scope_subject_without_the_platform_permission_is_refused(role_map, policy_set):
    """The ERP role map cannot grant what the platform does not: no back door."""
    store, _ = fx.build(permissions=[])
    decision = scope.authorize(
        role_map, policy_set, store, _principal(), _request("sales-invoice", "read")
    )
    assert decision.reason == "permission-denied"
    assert "identity/rbac refused" in decision.detail


def test_the_erp_role_layer_refuses_a_write_the_role_does_not_grant(role_map, policy_set):
    store = _store_with(role_map, "sales-invoice", "write")
    decision = scope.authorize(
        role_map, policy_set, store, _principal(("Auditor",)), _request("sales-invoice", "write")
    )
    assert decision.reason == "permission-denied"


# --- field-level policy -----------------------------------------------------


def test_a_denied_field_is_absent_from_the_projection_not_blanked(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(),
        _request("sales-invoice", "read", fields={"total": 100, "gross-margin": 42}),
    )
    assert decision.allowed is True
    # Omitting and blanking are different claims, and the difference is exactly
    # what a caller can observe here.
    assert "gross-margin" not in decision.projection
    assert decision.redacted == ("gross-margin",)
    assert dict(decision.projection) == {"total": 100}


def test_a_field_the_policy_does_not_govern_is_untouched(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(),
        _request("sales-invoice", "read", fields={"total": 100, "customer": "ACME"}),
    )
    assert dict(decision.projection) == {"total": 100, "customer": "ACME"}
    assert decision.redacted == ()


def test_an_advisory_that_fires_is_reported_rather_than_passing_silently(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(),
        _request("sales-invoice", "read", fields={"discount-percent": 5}),
    )
    assert decision.allowed is True
    assert decision.advisories == ("discount-needs-finance-review",)
    assert dict(decision.projection) == {"discount-percent": 5}


def test_a_write_to_a_read_only_field_is_refused_and_names_the_rule(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-order", "write"),
        _principal(),
        _request("sales-order", "write", fields={"credit-limit": 5000}),
    )
    assert decision.allowed is False
    assert decision.reason == "field-write-denied"
    assert "credit-limit" in decision.detail and "credit-limit-read-only" in decision.detail


def test_a_write_only_field_may_be_set_but_is_not_readable(role_map, policy_set):
    store = _store_with(role_map, "purchase-order", "write")
    principal = _principal(("Purchase User",))
    written = scope.authorize(
        role_map,
        policy_set,
        store,
        principal,
        _request("purchase-order", "write", fields={"supplier-bank-reference": "REF-1"}),
    )
    assert written.allowed is True

    read_store = _store_with(role_map, "purchase-order", "read")
    read = scope.authorize(
        role_map,
        policy_set,
        read_store,
        principal,
        _request("purchase-order", "read", fields={"supplier-bank-reference": "REF-1"}),
    )
    # It may be supplied and must not be read back by the role that supplied it.
    assert read.allowed is True
    assert "supplier-bank-reference" not in read.projection
    assert read.redacted == ("supplier-bank-reference",)


def test_a_role_scoped_field_rule_leaves_other_roles_alone(role_map, policy_set):
    decision = scope.authorize(
        role_map,
        policy_set,
        _store_with(role_map, "sales-invoice", "read"),
        _principal(("Accounts Manager",)),
        _request("sales-invoice", "read", fields={"gross-margin": 42}),
    )
    assert dict(decision.projection) == {"gross-margin": 42}
    assert decision.redacted == ()


# --- request-shaped errors are decided, not raised --------------------------


def test_an_unknown_kind_is_decided_with_a_reason(role_map, policy_set):
    decision = scope.authorize(
        role_map, policy_set, _store_with(role_map, "sales-invoice", "read"), _principal(),
        _request("no-such-kind", "read"),
    )
    assert decision.allowed is False
    assert decision.reason == "unknown-kind"


def test_an_unknown_role_is_decided_with_a_reason(role_map, policy_set):
    decision = scope.authorize(
        role_map, policy_set, _store_with(role_map, "sales-invoice", "read"),
        _principal(("Typo Role",)), _request("sales-invoice", "read"),
    )
    assert decision.reason == "unknown-role"
