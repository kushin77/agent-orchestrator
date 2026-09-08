"""No-lockout invariant on grant/revoke (issue #12 criterion 3).

An Org whose last role-admin binding is revoked has no way back through any
API. Revocation must refuse to remove the last binding that grants the Org's
role-admin permission - honoring wildcards (``*:*`` counts) and keyed to the
Org's own declared permission rather than a hardcoded default.
"""

import pytest

from rbac import (
    InMemoryStore,
    LastAdministratorError,
    UnknownRoleError,
    grant_role,
    revoke_role,
    seed_org,
)
from rbac.model import role_grants


def _build(role_admin_permission="roles:manage", tenant_type="platform"):
    store = InMemoryStore()
    org = store.add_org(
        "acme",
        "Acme",
        tenant_type=tenant_type,
        role_admin_permission=role_admin_permission,
    )
    seed_org(store, org)
    return store


def test_revoke_last_owner_is_refused():
    store = _build()
    grant_role(store, "acme", "u_owner", "owner")
    with pytest.raises(LastAdministratorError):
        revoke_role(store, "acme", "u_owner", "owner")
    # The binding is still there: the refusal did not remove it.
    assert len(store.bindings_for_subject("acme", "u_owner")) == 1


def test_revoke_second_admin_is_allowed_then_last_is_refused():
    store = _build()
    grant_role(store, "acme", "u_a", "admin")
    grant_role(store, "acme", "u_b", "admin")

    assert revoke_role(store, "acme", "u_a", "admin") is True
    with pytest.raises(LastAdministratorError):
        revoke_role(store, "acme", "u_b", "admin")


def test_admin_holding_owner_too_can_drop_the_admin_role():
    store = _build()
    grant_role(store, "acme", "u", "owner")
    grant_role(store, "acme", "u", "admin")
    # Dropping admin is fine: u still holds owner, which grants roles:manage.
    assert revoke_role(store, "acme", "u", "admin") is True
    assert len(store.bindings_for_subject("acme", "u")) == 1


def test_wildcard_grants_count_toward_administration():
    store = _build()
    grant_role(store, "acme", "u_owner", "owner")  # owner is "*:*"
    owner_role = store.find_role_by_key("acme", "owner")
    assert owner_role is not None
    assert role_grants(owner_role, "roles:manage")
    with pytest.raises(LastAdministratorError):
        revoke_role(store, "acme", "u_owner", "owner")


def test_revoke_last_binding_when_role_is_shared_by_two_users():
    """Two admins revoking each other: the second is refused (no lockout)."""
    store = _build()
    grant_role(store, "acme", "u_a", "owner")
    grant_role(store, "acme", "u_b", "admin")

    # u_a drops owner; u_b (admin) still grants roles:manage, so this is fine.
    assert revoke_role(store, "acme", "u_a", "owner") is True
    with pytest.raises(LastAdministratorError):
        revoke_role(store, "acme", "u_b", "admin")


def test_custom_role_admin_permission_is_the_keyed_invariant():
    """Invariant keys off the Org's declared permission, not a hardcoded string.

    Mirrors saas-rbac #125: a tenant that names role administration in its own
    vocabulary must be protected by *that* permission, and a tenant's
    roles:manage holder is not a substitute.
    """
    store = InMemoryStore()
    org = store.add_org(
        "acme",
        "Acme",
        tenant_type="platform",
        role_admin_permission="org:govern",
    )
    # The platform roles do not grant org:govern, so add a role that does and
    # bind one subject to each vocabulary.
    seed_org(store, org)
    store.create_role(
        org_id="acme",
        key="custodian",
        name="Custodian",
        permissions=("org:govern", "agent:read"),
    )
    grant_role(store, "acme", "u_adminish", "admin")  # grants roles:manage only
    grant_role(store, "acme", "u_custodian", "custodian")  # grants org:govern

    # The admin-ish holder does NOT protect the org's real admin permission.
    with pytest.raises(LastAdministratorError) as excinfo:
        revoke_role(store, "acme", "u_custodian", "custodian")
    assert excinfo.value.permission == "org:govern"

    # Revoking the roles:manage holder while the custodian remains is allowed:
    # the org's admin permission (org:govern) is still held.
    assert revoke_role(store, "acme", "u_adminish", "admin") is True


def test_grant_unknown_role_raises():
    store = _build()
    with pytest.raises(UnknownRoleError):
        grant_role(store, "acme", "u", "no-such-role")


def test_grant_is_idempotent():
    store = _build()
    first = grant_role(store, "acme", "u", "admin")
    second = grant_role(store, "acme", "u", "admin")
    assert first.id == second.id
    assert len(store.bindings_for_subject("acme", "u")) == 1


def test_revoke_unheld_role_is_a_noop():
    store = _build()
    assert revoke_role(store, "acme", "u", "admin") is False


def test_team_level_role_cannot_be_granted_org_wide():
    store = _build()
    with pytest.raises(ValueError):
        grant_role(store, "acme", "u", "agent-operator")
