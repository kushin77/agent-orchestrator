"""ERP tenant access — the in-module auth layer for ERP documents (ERP-08, #653).

ERP-08 of the ERP module epic (#645): tenant-scoped access to ERP data, with
ERPNext's role-based permission model mapped onto the platform's tenant
identity and RBAC, and field-level policy declarations that guardrails can
enforce.

The package is a thin front door over seven modules:

* :mod:`.model` — the closed ERP action and refusal vocabularies, ``Principal``,
  ``Request``, ``Decision`` and ``Refused``;
* :mod:`.contract` — the two contracts this lane *consumes* (``identity/rbac``
  and ``guardrails/policy``) and the seams it reaches them through;
* :mod:`.roles` — the ERPNext role→permission table as data, and its loader;
* :mod:`.policies` — field-level declarations, schema-validated and enforced;
* :mod:`.scope` — the middleware: the tenant gate, the platform's two gates,
  and field projection;
* :mod:`.schemas` — the frozen schemas and the step that enforces them;
* :mod:`.provenance` — the GR-10 harvest record and its enforcement.

Three properties the lane is built on, each with a control that demonstrates it:

1. **The tenant gate is an invariant, not a rule.** ``Request.tenant`` is a
   claim and ``Principal.tenant`` is the authority; a mismatch is refused before
   any role, policy or contract is consulted, and no declaration can express
   "allowed across tenants". The negative control drives it with a principal
   holding *every* role.
2. **This layer cannot escalate past the platform.** ERPNext role names are
   *translated* into ``identity/rbac``'s ``resource:action`` permissions, and
   ``identity/rbac`` decides, in its documented safe order (scope gate, then
   permission gate). There is no allow path that skips the contract, so a role
   map cannot grant what the platform does not grant.
3. **Every refusal is provoked.** :mod:`.negative_control` provokes all of
   ``model.REFUSALS`` and asserts each is refused **by name**;
   :mod:`.cli`'s ``check`` fails when the provoked set and the declared set
   diverge, so a refusal cannot ship without a control that demonstrates it
   refusing.

No secret is carried anywhere (GR-6): a :class:`~.model.Principal` is
reference-based, and the suite asserts that against the dataclass fields
themselves rather than trusting the convention.
"""

from __future__ import annotations

from . import contract, model, policies, provenance, roles, schemas, scope  # noqa: F401
from .model import (  # noqa: F401
    ACTIONS,
    REFUSALS,
    SCHEMA_VERSION,
    Decision,
    Principal,
    Refused,
    Request,
)
from .policies import FieldPolicySet, FieldRule  # noqa: F401
from .roles import RoleGrant, RoleMap  # noqa: F401
from .scope import authorize  # noqa: F401

__all__ = [
    "ACTIONS",
    "Decision",
    "FieldPolicySet",
    "FieldRule",
    "Principal",
    "REFUSALS",
    "Refused",
    "Request",
    "RoleGrant",
    "RoleMap",
    "SCHEMA_VERSION",
    "authorize",
    "contract",
    "model",
    "policies",
    "provenance",
    "roles",
    "schemas",
    "scope",
]
