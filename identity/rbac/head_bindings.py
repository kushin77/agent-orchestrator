"""Tenant/RBAC binding for the head-of-org personas (issue #952, parent #878).

Gap this module closes: ``identity/`` had real RBAC entries for the C-suite
personas (`boundaries.py` / `presets/csuite.yaml`) but nothing bound the
platform's `hermes` persona (`registry/personas/cards/hermes.yaml`) - or its
`paperclip` counterpart - to a Role and a Binding. The existing `paperclip`
"carve-out" in `skills.py` is not a role binding at all: it scopes which
`SKILL.md` declarations an org may see, grants no `resource:action`
permission, and creates no `Role` or `Binding` row. This module supplies the
real thing, for both personas, on the same pattern.

Head-of-org authority, scoped
------------------------------

Both personas get an **org-wide** role (`rbac.presets.head-agents` pack: keys
`hermes-head` / `paperclip-head`) granting registry reads plus their own
create action (`directive:create` for hermes, `doc:create` for paperclip) -
and explicitly withholding tenant administration (`org:manage`), secret
rotation (`secret:rotate`), rollout approval (`rollout:approve`) and role
administration (`roles:manage`). Those are refused the ordinary way: the
permission gate (`rbac.guard.guard`) denies them because the role never grants
them - see `tests/test_head_bindings.py::test_forbidden_ops_refused_by_name`.

A cross-tenant read is refused by a *different* gate - the scope gate
(`rbac.resolve.resolve_scope`) - and by construction, not by luck: the subject
a persona binds as is **tenant-qualified** (`persona_subject` returns
``persona:<id>@<org_id>``, never a bare ``persona:<id>``). Even when the same
persona is bound in two tenants at once - the expected steady state for a
platform persona like hermes - "org A's hermes" and "org B's hermes" are two
entirely different subject strings with two entirely separate `Binding` rows
(`Binding.org_id` pins each - see `model.py`), so `store.bindings_for_subject`
for org B never returns org A's row and `resolve_scope` denies org A's subject
at org B's node with `code="out_of_scope"` - exactly like every other subject
in this package (`tests/test_scope_vs_permission.py`).

Opt-in only (GR-28)
--------------------

**No tenant is bound to either persona by default.** `bind_persona_to_tenant`
is the only thing that ever creates the binding, and nothing in this repo
calls it automatically - not `seed_org`, not org creation, nothing. The
`head-agents` pack is deliberately never resolved by `org.tenant_type`
(`presets.BUILTIN_TENANT_TYPES` does not name it), so an Org seeded from any
of the four built-in packs, or from `csuite`, carries no head-of-org binding
until a tenant explicitly enables one persona at a time - the same
declared-default-off discipline as GR-28 elsewhere in this repo (rollout flags
default OFF; nothing here defaults ON either).


---knowledge---
module_id: identity.rbac.head_bindings
system: identity
app: rbac
solution_class: enterprise
patterns: [real-binding, declared-authority, fail-closed]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [bind_persona_to_tenant, unbind_persona_from_tenant, is_persona_bound, persona_subject]
invariants: "both head-of-org personas get a real Role and Binding, not the skills carve-out which grants no permission and creates no binding"
gotchas: "the paperclip carve-out in skills.py is not a role binding"
related: ["#952", "#878"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .model import SUBJECT_AGENT, Binding, Org, Role, ScopeNode
from .guard import Decision, guard
from .presets import RolePack, load_pack

#: Pack key for `presets/head-agents.yaml` - not a tenant type (see module
#: docstring): loaded by name, exactly like `CSUITE_PACK_KEY`.
HEAD_AGENTS_PACK_KEY = "head-agents"

HERMES_PERSONA_ID = "hermes"
PAPERCLIP_PERSONA_ID = "paperclip"

#: persona id -> the role key it is bound to in the head-agents pack.
ROLE_KEY_FOR_PERSONA: dict[str, str] = {
    HERMES_PERSONA_ID: "hermes-head",
    PAPERCLIP_PERSONA_ID: "paperclip-head",
}

KNOWN_PERSONAS: tuple[str, ...] = tuple(ROLE_KEY_FOR_PERSONA)

#: Permissions the head-agent roles must never carry - the negative half of
#: the contract, asserted directly in tests/test_head_bindings.py so a future
#: edit to the pack YAML that widens a role trips a test by name.
FORBIDDEN_PERMISSIONS: tuple[str, ...] = (
    "org:manage",
    "secret:rotate",
    "rollout:approve",
    "roles:manage",
)


class UnknownPersonaError(ValueError):
    """The persona id is not one this module binds."""

    def __init__(self, persona_id: str) -> None:
        super().__init__(
            f"no head-of-org binding for persona {persona_id!r}; known: "
            f"{', '.join(KNOWN_PERSONAS)}"
        )
        self.persona_id = persona_id


def _require_known(persona_id: str) -> str:
    role_key = ROLE_KEY_FOR_PERSONA.get(persona_id)
    if role_key is None:
        raise UnknownPersonaError(persona_id)
    return role_key


def persona_subject(persona_id: str, org_id: str) -> str:
    """The RBAC subject id a persona binds as *inside one Org*.

    Tenant-qualified (``persona:<persona_id>@<org_id>``) rather than a global
    id - a platform persona like hermes is expected to opt in for many
    tenants at once, and a global ``persona:hermes`` subject would resolve
    against the bindings of *every* org it is bound in, defeating the scope
    gate's cross-tenant isolation the moment a second tenant opts in. Scoping
    the subject to the org keeps each tenant's grant a completely separate
    row - `store.bindings_for_subject(org_id, subject)` for org A can never
    see org B's binding, because the subject strings themselves differ.
    """
    _require_known(persona_id)
    if not org_id:
        raise ValueError("persona_subject: org_id must be non-empty")
    return f"persona:{persona_id}@{org_id}"


def load_head_agents_pack() -> RolePack:
    """Load the head-of-org agent pack (issue #952) from its YAML on disk."""
    return load_pack(HEAD_AGENTS_PACK_KEY)


def _ensure_role(store, org: Org, persona_id: str) -> Role:
    """Idempotently materialize the persona's role in ``org`` from the pack.

    Creating the role does not bind anyone to it - `bind_persona_to_tenant` is
    still the only opt-in seam that grants the binding.
    """
    role_key = _require_known(persona_id)
    existing = store.find_role_by_key(org.id, role_key)
    if existing is not None:
        return existing
    pack = load_head_agents_pack()
    preset = pack.find(role_key)
    if preset is None:  # pragma: no cover - pack/module drift, not reachable normally
        raise UnknownPersonaError(persona_id)
    return store.create_role(
        org_id=org.id,
        key=preset.key,
        name=preset.name,
        description=preset.description,
        permissions=preset.permissions,
        level=preset.level,
        is_system=preset.is_system,
    )


def is_persona_bound(store, org_id: str, persona_id: str) -> bool:
    """Whether ``persona_id`` currently holds its head-of-org role in ``org_id``.

    False for an org that never opted in, an org whose binding was deleted (the
    negative control the gate script provokes), and any org other than the one
    the binding names - a binding never crosses an Org (see `model.Binding`).
    """
    role_key = _require_known(persona_id)
    role = store.find_role_by_key(org_id, role_key)
    if role is None:
        return False
    subject = persona_subject(persona_id, org_id)
    return any(
        b.role_id == role.id and b.team_id is None
        for b in store.bindings_for_subject(org_id, subject)
    )


def bind_persona_to_tenant(store, org: Org, persona_id: str) -> Binding:
    """Opt a tenant into a head-of-org persona (GR-28: explicit, never default).

    Idempotent: re-binding an already-bound persona returns the existing
    binding rather than creating a duplicate. Materializes the role from the
    ``head-agents`` pack on first use (the role never auto-seeds with the Org).
    """
    role = _ensure_role(store, org, persona_id)
    subject = persona_subject(persona_id, org.id)
    for binding in store.bindings_for_subject(org.id, subject):
        if binding.role_id == role.id and binding.team_id is None:
            return binding
    return store.add_binding(
        org_id=org.id,
        subject=subject,
        subject_type=SUBJECT_AGENT,
        role_id=role.id,
        team_id=None,
    )


def unbind_persona_from_tenant(store, org_id: str, persona_id: str) -> bool:
    """Revoke a persona's head-of-org binding. Returns False if it held none.

    This is the operation the gate script's negative control performs to
    prove the identity gate goes red without the binding.
    """
    role_key = _require_known(persona_id)
    role = store.find_role_by_key(org_id, role_key)
    if role is None:
        return False
    subject = persona_subject(persona_id, org_id)
    for binding in store.bindings_for_subject(org_id, subject):
        if binding.role_id == role.id and binding.team_id is None:
            store.delete_binding(binding.id)
            return True
    return False


def guard_persona(store, org_id: str, persona_id: str, permission: str) -> Decision:
    """Guard one ``resource:action`` call by ``persona_id`` at the org node.

    A thin, persona-aware wrapper over `rbac.guard.guard`: resolves the same
    tenant-qualified subject `bind_persona_to_tenant` grants, at the org-level
    `ScopeNode`, so callers never have to know the `persona:<id>@<org_id>`
    subject convention.
    """
    subject = persona_subject(persona_id, org_id)
    node = ScopeNode(org_id=org_id)
    return guard(store, subject, node, permission)
