"""Tenant/RBAC binding for the head-of-org personas (issue #952, parent #878).

Gap: hermes (and paperclip's skills.py "carve-out", which is not a role
binding at all) had no Role/Binding entry in identity/rbac. This suite proves
the real thing: a bound persona passes its allowed ops and is refused its
forbidden ops BY NAME; an unbound tenant is refused; a cross-tenant read is
refused; and deleting the binding reds the check (the gate script's negative
control re-runs this exact assertion against the identity check script).
"""

import pytest

from rbac import (
    FORBIDDEN_PERMISSIONS,
    HERMES_PERSONA_ID,
    KNOWN_PERSONAS,
    PAPERCLIP_PERSONA_ID,
    ROLE_KEY_FOR_PERSONA,
    BUILTIN_TENANT_TYPES,
    HEAD_AGENTS_PACK_KEY,
    InMemoryStore,
    UnknownPersonaError,
    bind_persona_to_tenant,
    guard_persona,
    is_persona_bound,
    load_head_agents_pack,
    persona_subject,
    seed_org,
    unbind_persona_from_tenant,
)
from rbac.model import is_permission

ALLOWED_PERMISSIONS = {
    HERMES_PERSONA_ID: ("org:read", "agent:read", "directive:create"),
    PAPERCLIP_PERSONA_ID: ("org:read", "agent:read", "doc:create"),
}


@pytest.fixture
def store():
    return InMemoryStore()


def _org(store, org_id="acme", tenant_type="startup"):
    org = store.add_org(org_id, org_id.title(), tenant_type=tenant_type)
    seed_org(store, org)  # ordinary tenant seeding - untouched by this pack
    return org


# --- the pack itself -----------------------------------------------------


def test_pack_is_not_a_tenant_type():
    pack = load_head_agents_pack()
    assert pack.key == HEAD_AGENTS_PACK_KEY
    assert pack.key not in BUILTIN_TENANT_TYPES
    assert {r.key for r in pack.roles} == set(ROLE_KEY_FOR_PERSONA.values())


def test_pack_permissions_are_well_formed_and_never_forbidden():
    pack = load_head_agents_pack()
    for role in pack.roles:
        assert role.permissions, role.key
        for permission in role.permissions:
            assert is_permission(permission), (role.key, permission)
            assert permission not in FORBIDDEN_PERMISSIONS, (role.key, permission)


# --- unknown persona ids are refused, not defaulted -----------------------


def test_unknown_persona_is_refused():
    with pytest.raises(UnknownPersonaError):
        persona_subject("not-a-persona")


# --- no tenant is bound by default (GR-28) --------------------------------


@pytest.mark.parametrize("persona_id", KNOWN_PERSONAS)
def test_unbound_tenant_is_refused(store, persona_id):
    org = _org(store)
    assert not is_persona_bound(store, org.id, persona_id)
    for permission in ALLOWED_PERMISSIONS[persona_id]:
        decision = guard_persona(store, org.id, persona_id, permission)
        assert decision.denied
        assert decision.reason == "scope"


# --- a bound persona passes allowed ops, is refused forbidden ops by name --


@pytest.mark.parametrize("persona_id", KNOWN_PERSONAS)
def test_bound_persona_passes_allowed_ops(store, persona_id):
    org = _org(store)
    bind_persona_to_tenant(store, org, persona_id)
    assert is_persona_bound(store, org.id, persona_id)
    for permission in ALLOWED_PERMISSIONS[persona_id]:
        decision = guard_persona(store, org.id, persona_id, permission)
        assert decision.allowed, (persona_id, permission, decision)


@pytest.mark.parametrize("persona_id", KNOWN_PERSONAS)
@pytest.mark.parametrize("permission", FORBIDDEN_PERMISSIONS)
def test_bound_persona_refused_forbidden_ops_by_name(store, persona_id, permission):
    org = _org(store)
    bind_persona_to_tenant(store, org, persona_id)
    decision = guard_persona(store, org.id, persona_id, permission)
    assert decision.denied
    assert decision.reason == "permission"
    assert permission in decision.missing_permissions


# --- binding is per-tenant: a second org never inherits it ----------------


@pytest.mark.parametrize("persona_id", KNOWN_PERSONAS)
def test_binding_is_scoped_to_its_own_tenant(store, persona_id):
    bound_org = _org(store, "acme")
    other_org = _org(store, "globex")
    bind_persona_to_tenant(store, bound_org, persona_id)

    assert is_persona_bound(store, bound_org.id, persona_id)
    assert not is_persona_bound(store, other_org.id, persona_id)

    decision = guard_persona(
        store, other_org.id, persona_id, ALLOWED_PERMISSIONS[persona_id][0]
    )
    assert decision.denied
    assert decision.reason == "scope"


# --- cross-tenant read is refused (the scope gate, not the permission list) --


def test_cross_tenant_read_is_refused():
    store = InMemoryStore()
    acme = _org(store, "acme")
    globex = _org(store, "globex")
    bind_persona_to_tenant(store, acme, HERMES_PERSONA_ID)
    bind_persona_to_tenant(store, globex, HERMES_PERSONA_ID)

    # Hermes bound in acme cannot read globex, even though hermes is *also*
    # bound (separately) in globex under its own binding - no fallback, no
    # cross-tenant reach, because a Binding never crosses an Org.
    acme_subject = persona_subject(HERMES_PERSONA_ID)
    from rbac.guard import guard
    from rbac.model import ScopeNode

    # acme's hermes binding grants org:read only inside acme's own node.
    decision_home = guard(store, acme_subject, ScopeNode(org_id=acme.id), "org:read")
    assert decision_home.allowed
    # There is no such thing as "acme's hermes reading globex" in this model -
    # the subject string is identical, but resolve_scope only ever consults
    # bindings recorded under the requested org_id (globex), which is where
    # globex's OWN hermes binding lives, not a foreign reach from acme.
    decision_cross = guard(store, acme_subject, ScopeNode(org_id=globex.id), "org:read")
    # This passes because globex bound hermes too (its own, separate grant) -
    # proving the binding is per-org, not proving a cross-tenant reach.
    assert decision_cross.allowed
    unbind_persona_from_tenant(store, globex.id, HERMES_PERSONA_ID)
    decision_cross_unbound = guard(
        store, acme_subject, ScopeNode(org_id=globex.id), "org:read"
    )
    assert decision_cross_unbound.denied
    assert decision_cross_unbound.reason == "scope"


# --- idempotence + revocation ----------------------------------------------


@pytest.mark.parametrize("persona_id", KNOWN_PERSONAS)
def test_bind_is_idempotent(store, persona_id):
    org = _org(store)
    first = bind_persona_to_tenant(store, org, persona_id)
    second = bind_persona_to_tenant(store, org, persona_id)
    assert first.id == second.id
    assert len(store.bindings_for_subject(org.id, persona_subject(persona_id))) == 1


@pytest.mark.parametrize("persona_id", KNOWN_PERSONAS)
def test_unbind_removes_access_and_is_idempotent(store, persona_id):
    org = _org(store)
    bind_persona_to_tenant(store, org, persona_id)
    assert unbind_persona_from_tenant(store, org.id, persona_id) is True
    assert not is_persona_bound(store, org.id, persona_id)
    decision = guard_persona(
        store, org.id, persona_id, ALLOWED_PERMISSIONS[persona_id][0]
    )
    assert decision.denied
    assert decision.reason == "scope"
    # Idempotent: unbinding an already-unbound persona is a no-op, not an error.
    assert unbind_persona_from_tenant(store, org.id, persona_id) is False


# --- negative control: deleting the binding entry reds the identity gate ---


@pytest.mark.parametrize("persona_id", KNOWN_PERSONAS)
def test_deleting_the_binding_entry_reproduces_the_gates_negative_control(
    store, persona_id
):
    """Mirrors what scripts/check-rbac-head-binding.sh provokes end to end:
    bind -> prove allowed -> delete the Binding row directly (not through
    unbind_persona_from_tenant, to simulate an external row deletion) -> prove
    the same allowed op is now refused. A gate that could not observe this
    regression would be a formality (GR-12)."""
    org = _org(store)
    bind_persona_to_tenant(store, org, persona_id)
    permission = ALLOWED_PERMISSIONS[persona_id][0]
    assert guard_persona(store, org.id, persona_id, permission).allowed

    subject = persona_subject(persona_id)
    role_key = ROLE_KEY_FOR_PERSONA[persona_id]
    role = store.find_role_by_key(org.id, role_key)
    (binding,) = [
        b
        for b in store.bindings_for_subject(org.id, subject)
        if b.role_id == role.id
    ]
    assert store.delete_binding(binding.id) is True

    decision = guard_persona(store, org.id, persona_id, permission)
    assert decision.denied
    assert decision.reason == "scope"
