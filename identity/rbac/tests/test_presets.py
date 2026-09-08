"""Preset role packs per tenant type + the custom-pack seam (issue #12 criterion 4).

Every Org seeds from exactly one pack; a tenant that brings its own RBAC model
supplies a custom pack through parse_pack / register_pack (the custom-pack
seam) and is protected by its own declared role-admin permission.
"""

import pytest

from rbac import (
    BUILTIN_TENANT_TYPES,
    InMemoryStore,
    RolePack,
    load_pack,
    parse_pack,
    register_pack,
    resolve_pack,
    seed_org,
)
from rbac.model import is_permission, role_grants

CUSTOM_PACK_YAML = """\
key: governed
name: Governed Enterprise
description: Custom pack that names role administration in its own vocabulary.
role_admin_permission: org:govern
roles:
  - key: custodian
    name: Custodian
    description: Holds the org's declared role-admin permission.
    level: org
    permissions: ["org:govern", "agent:read", "member:read"]
    is_system: true
  - key: member
    name: Member
    description: Read-only member.
    level: org
    permissions: ["org:read", "agent:read"]
    is_system: true
"""


def _fresh_store_for(key: str, role_admin: str | None = None):
    store = InMemoryStore()
    pack = load_pack(key)
    org = store.add_org(
        f"org_{key}",
        key.title(),
        tenant_type=key,
        role_admin_permission=role_admin or pack.role_admin_permission,
    )
    return store, org, pack


# --- built-in packs -----------------------------------------------------------


def test_all_builtin_packs_load_and_are_well_formed():
    for key in BUILTIN_TENANT_TYPES:
        pack = load_pack(key)
        assert isinstance(pack, RolePack)
        assert pack.key == key
        role_keys = list(pack.role_keys())
        assert len(role_keys) == len(set(role_keys)), f"{key}: duplicate role keys"
        # Every pack can administer itself: an owner/admin that grants the
        # pack's declared role-admin permission (wildcards count).
        assert "owner" in role_keys and "admin" in role_keys
        assert pack.grants_permission(pack.role_admin_permission)
        for role in pack.roles:
            assert role.name
            assert role.level in ("org", "team")
            assert role.permissions, f"{key}/{role.key}: no permissions"
            for permission in role.permissions:
                assert is_permission(permission), f"{key}/{role.key}: {permission!r}"


def test_platform_pack_is_the_default_and_owner_is_full_wildcard():
    pack = load_pack("platform")
    owner = pack.find("owner")
    assert owner is not None
    assert "*:*" in owner.permissions
    assert pack.role_admin_permission == "roles:manage"
    assert pack.find("admin") is not None
    assert pack.find("agent-operator") is not None


def test_pack_mix_matches_tenant_types():
    assert load_pack("startup").role_keys() == ("owner", "admin", "member")
    assert set(load_pack("smb").role_keys()) == {"owner", "admin", "team-admin", "member"}
    assert "auditor" in load_pack("enterprise").role_keys()
    # team-level roles only in packs that have teams to scope them to
    assert load_pack("startup").find("agent-operator") is None


def test_every_builtin_pack_seeds_an_org():
    for key in BUILTIN_TENANT_TYPES:
        store, org, pack = _fresh_store_for(key)
        roles = seed_org(store, org)
        assert len(roles) == len(pack.roles)
        assert {r.key for r in roles} == set(pack.role_keys())
        # Roles are created as system roles owned by the org.
        assert all(r.org_id == org.id and r.is_system for r in roles)
        # Seeding again conflicts on the role key.
        with pytest.raises(ValueError):
            seed_org(store, org)


def test_resolve_pack_unknown_tenant_type_raises():
    with pytest.raises(KeyError):
        resolve_pack("no-such-type")


# --- custom-pack seam ----------------------------------------------------------


def test_parse_pack_and_register_custom_pack():
    pack = parse_pack(CUSTOM_PACK_YAML)
    assert pack.key == "governed"
    assert pack.role_admin_permission == "org:govern"
    assert pack.role_keys() == ("custodian", "member")

    register_pack(pack)
    assert resolve_pack("governed") is pack

    store = InMemoryStore()
    org = store.add_org(
        "acme",
        "Acme",
        tenant_type="governed",
        role_admin_permission="org:govern",
    )
    roles = seed_org(store, org)
    assert {r.key for r in roles} == {"custodian", "member"}
    custodian = store.find_role_by_key("acme", "custodian")
    assert custodian is not None
    # The org can administer itself in its own vocabulary.
    assert role_grants(custodian, "org:govern")
    # And the platform's hardcoded default is NOT what governs it.
    assert not role_grants(custodian, "roles:manage")


def test_register_pack_duplicate_key_is_rejected():
    pack = parse_pack(
        """\
key: governed_dup
name: Dup
role_admin_permission: roles:manage
roles:
  - key: owner
    name: Owner
    permissions: ["*:*"]
"""
    )
    register_pack(pack)
    with pytest.raises(ValueError):
        register_pack(pack)


def test_seed_refuses_a_pack_that_cannot_administer_the_org():
    powerless = """\
key: powerless
name: Powerless
role_admin_permission: roles:manage
roles:
  - key: viewer
    name: Viewer
    level: org
    permissions: ["org:read"]
"""
    store = InMemoryStore()
    org = store.add_org("acme", "Acme", tenant_type="platform")
    with pytest.raises(ValueError):
        seed_org(store, org, parse_pack(powerless))
    # Nothing was created: seeding is all-or-nothing.
    assert store.roles_in_org("acme") == []


def test_parse_rejects_invalid_permission_and_bad_structure():
    with pytest.raises(ValueError):
        parse_pack(
            "key: bad\nroles:\n  - key: x\n    name: X\n    permissions: [\"no-colon\"]\n"
        )
    with pytest.raises(ValueError):
        parse_pack("not: [even, a, pack]")
    with pytest.raises(ValueError):
        parse_pack("key: empty\nname: Empty\nroles: []\n")
    with pytest.raises(ValueError):
        parse_pack(
            "key: dup\nroles:\n  - key: a\n    name: A\n    permissions: [\"agent:read\"]\n"
            "  - key: a\n    name: A2\n    permissions: [\"agent:read\"]\n"
        )
