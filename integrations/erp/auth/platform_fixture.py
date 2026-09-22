"""A deterministic, offline platform fixture — the identity side of a decision.

Every ERP-08 decision consults ``identity/rbac``, so exercising one needs a
platform store with a tenant, a team, a subject and a role that carries the
*translated* permission. This module builds exactly that, from nothing, with no
network and no wall-clock dependence, so the golden path is reproducible and a
negative control can be provoked anywhere.

**Why it lives in the package and not in ``tests/``.** Three consumers need the
same seed: the suite, ``negative_control.py`` and ``cli.check``'s golden path.
A fixture under ``tests/`` would have to be imported by two non-test modules,
which is how a test helper quietly becomes production code with no docstring;
here it is production code that admits it is a fixture.

**What it proves by existing.** The permissions a store is seeded with are the
*translated* ones (``erp.<kind>:<action>``), not the ERP role names. Seeding a
role with nothing grants a subject in scope nothing — which is what makes the
platform's permission gate observable rather than assumed (see
``negative_control``'s ``permission-denied``).

---knowledge---
module_id: integrations.erp.auth.platform_fixture
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [scope_node, build]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any, Sequence, Tuple

from . import contract

DEFAULT_TENANT = "acme"
DEFAULT_TEAM = "erp"
DEFAULT_SUBJECT = "erp-user"
DEFAULT_ROLE_KEY = "erp-access"


def scope_node(tenant: str, team: str | None, subject: str) -> Any:
    """A scope node for ``subject`` inside ``tenant`` (optionally a team)."""
    model = contract.rbac().model
    return model.ScopeNode(org_id=tenant, team_id=team, agent_id=subject)


def build(
    *,
    tenant: str = DEFAULT_TENANT,
    team: str = DEFAULT_TEAM,
    subject: str = DEFAULT_SUBJECT,
    permissions: Sequence[str] = (),
    role_key: str = DEFAULT_ROLE_KEY,
) -> Tuple[Any, Any]:
    """Build a platform store, the subject's scope node, and the role.

    Returns ``(store, node)``. The role is org-level, so its binding reaches
    every node of the tenant — which is what lets a request name a team and
    still resolve, without this helper having to guess which team a document
    belongs to.
    """
    rbac = contract.rbac()

    store = rbac.InMemoryStore()
    store.add_org(tenant, tenant.capitalize(), tenant_type="platform")
    store.add_team(tenant, "ERP", team_id=team)
    store.add_agent(tenant, team, subject, agent_id=subject)

    role = store.create_role(
        tenant,
        key=role_key,
        name=role_key,
        description="the ERP permissions this fixture's subject holds",
        permissions=tuple(sorted(set(permissions))),
        level="org",
    )
    store.add_binding(
        tenant,
        subject,
        rbac.SUBJECT_AGENT,
        role.id,
        team_id=None,
    )
    return store, scope_node(tenant, team, subject)
