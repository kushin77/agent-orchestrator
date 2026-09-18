"""Agent Org RBAC / role model - the platform's authorization contract (issue #12).

Self-contained package under ``identity/rbac/``. Intra-package imports are
**relative** (issue #1078), so the package is importable under either of the two
names this repo uses for it - ``rbac``, with ``identity/`` on ``sys.path`` (what
``tests/conftest.py`` and ``scripts/check-rbac-head-binding.sh`` arrange), and
``identity.rbac`` - and neither form needs a caller to have mutated ``sys.path``
first.

The earlier absolute ``from rbac.model import ...`` made the package importable
*only* when ``identity/`` happened to be on ``sys.path``. An importer that
reached the package by its qualified name - pytest's ``--import-mode=importlib``,
which imports a conftest's parent package chain *before* the conftest body (and
so before its ``sys.path`` bootstrap) runs - therefore executed this module
first and died with ``ModuleNotFoundError: No module named 'rbac'``.

Public surface
--------------

- ``model`` - Org/Team/Agent hierarchy, Role, Binding, Session, ScopeNode and
  the ``resource:action`` permission language (wildcards).
- ``store`` - the in-memory persistence seam (later phases may back it with a
  database adapter).
- ``resolve`` - the two gates: ``resolve_scope`` (scope) and ``authorize``
  (permission), plus ``effective_permissions``.
- ``bindings`` - ``grant_role`` / ``revoke_role`` with the no-lockout invariant.
- ``guard`` - the enforcement core: ``guard``, ``guard_required``,
  ``guard_session``, ``start_agent_session`` and the denial-log contract.
- ``presets`` - YAML role packs per tenant type + the custom-pack seam, plus
  the named C-suite boundary pack (``load_csuite_pack``).
- ``boundaries`` - the C-suite role boundaries (issue #638): ``RoleBoundary``
  derived read-only from the persona cards, and ``guard_boundary`` which
  refuses an out-of-boundary action on the lane/tool/capability/budget axis.
- ``skills`` - org-wide skill sharing semantics (issue #638):
  ``SkillShareRegistry`` with platform read-only sharing and cross-tenant
  invisibility.
- ``head_bindings`` - tenant/RBAC binding for the head-of-org personas (issue
  #952): the ``head-agents`` preset pack (``hermes-head`` / ``paperclip-head``)
  plus ``bind_persona_to_tenant`` / ``unbind_persona_from_tenant`` /
  ``is_persona_bound``, opt-in only per tenant (GR-28: no default binding).
"""

from .model import (
    DEFAULT_ROLE_ADMIN_PERMISSION,
    PERMISSIONS,
    ROLE_LEVEL_ORG,
    ROLE_LEVEL_TEAM,
    SCOPE_LEVEL_AGENT,
    SCOPE_LEVEL_ORG,
    SCOPE_LEVEL_TEAM,
    SUBJECT_AGENT,
    SUBJECT_USER,
    WILDCARD,
    Agent,
    Binding,
    Org,
    Role,
    ScopeNode,
    Session,
    Team,
    format_permission,
    is_permission,
    permission_granted,
    role_grants,
    split_permission,
)

from .resolve import (
    ScopeResolution,
    authorize,
    effective_permissions,
    resolve_scope,
)

from .bindings import (
    LastAdministratorError,
    UnknownRoleError,
    grant_role,
    revoke_role,
)

from .guard import (
    AUTHORIZATION_DENIED_EVENT,
    SCOPE_DENIAL_PERMISSION,
    Decision,
    PermissionDeniedError,
    ScopeDeniedError,
    UnknownAgentError,
    authorization_denied_payload,
    guard,
    guard_required,
    guard_session,
    start_agent_session,
)

from .store import InMemoryStore
from .presets import (
    BUILTIN_TENANT_TYPES,
    CSUITE_PACK_KEY,
    RolePack,
    RolePreset,
    available_packs,
    load_csuite_pack,
    load_pack,
    parse_pack,
    register_pack,
    resolve_pack,
    seed_org,
)

from .boundaries import (
    BOUNDARY_BUDGET,
    BOUNDARY_CAPABILITIES,
    BOUNDARY_LANES,
    BOUNDARY_TOOLS,
    CSUITE_ROLE_IDS,
    BoundaryAction,
    BoundaryDecision,
    BoundaryError,
    BoundaryPack,
    BoundaryViolation,
    MissingCardError,
    RoleBoundary,
    UnknownRoleBoundaryError,
    boundary_from_card,
    guard_boundary,
    load_csuite_boundaries,
)

from .skills import (
    PLATFORM_ORG,
    VISIBILITIES,
    VISIBILITY_PLATFORM,
    VISIBILITY_TENANT,
    WILDCARD_TARGET,
    CrossTenantShareError,
    PlatformSkillImmutableError,
    Skill,
    SkillOwnershipError,
    SkillScopeError,
    SkillShareRegistry,
    UnknownSkillError,
    partition_by_visibility,
)

from .head_bindings import (
    FORBIDDEN_PERMISSIONS,
    HEAD_AGENTS_PACK_KEY,
    HERMES_PERSONA_ID,
    KNOWN_PERSONAS,
    PAPERCLIP_PERSONA_ID,
    ROLE_KEY_FOR_PERSONA,
    UnknownPersonaError,
    bind_persona_to_tenant,
    guard_persona,
    is_persona_bound,
    load_head_agents_pack,
    persona_subject,
    unbind_persona_from_tenant,
)

__all__ = [
    # model
    "DEFAULT_ROLE_ADMIN_PERMISSION",
    "PERMISSIONS",
    "ROLE_LEVEL_ORG",
    "ROLE_LEVEL_TEAM",
    "SCOPE_LEVEL_AGENT",
    "SCOPE_LEVEL_ORG",
    "SCOPE_LEVEL_TEAM",
    "SUBJECT_AGENT",
    "SUBJECT_USER",
    "WILDCARD",
    "Agent",
    "Binding",
    "Org",
    "Role",
    "ScopeNode",
    "Session",
    "Team",
    "format_permission",
    "is_permission",
    "permission_granted",
    "role_grants",
    "split_permission",
    # resolve
    "ScopeResolution",
    "authorize",
    "effective_permissions",
    "resolve_scope",
    # bindings
    "LastAdministratorError",
    "UnknownRoleError",
    "grant_role",
    "revoke_role",
    # guard
    "AUTHORIZATION_DENIED_EVENT",
    "SCOPE_DENIAL_PERMISSION",
    "Decision",
    "PermissionDeniedError",
    "ScopeDeniedError",
    "UnknownAgentError",
    "authorization_denied_payload",
    "guard",
    "guard_required",
    "guard_session",
    "start_agent_session",
    # store + presets
    "InMemoryStore",
    "BUILTIN_TENANT_TYPES",
    "CSUITE_PACK_KEY",
    "RolePack",
    "RolePreset",
    "available_packs",
    "load_csuite_pack",
    "load_pack",
    "parse_pack",
    "register_pack",
    "resolve_pack",
    "seed_org",
    # boundaries (issue #638)
    "BOUNDARY_BUDGET",
    "BOUNDARY_CAPABILITIES",
    "BOUNDARY_LANES",
    "BOUNDARY_TOOLS",
    "CSUITE_ROLE_IDS",
    "BoundaryAction",
    "BoundaryDecision",
    "BoundaryError",
    "BoundaryPack",
    "BoundaryViolation",
    "MissingCardError",
    "RoleBoundary",
    "UnknownRoleBoundaryError",
    "boundary_from_card",
    "guard_boundary",
    "load_csuite_boundaries",
    # skills (issue #638)
    "PLATFORM_ORG",
    "VISIBILITIES",
    "VISIBILITY_PLATFORM",
    "VISIBILITY_TENANT",
    "WILDCARD_TARGET",
    "CrossTenantShareError",
    "PlatformSkillImmutableError",
    "Skill",
    "SkillOwnershipError",
    "SkillScopeError",
    "SkillShareRegistry",
    "UnknownSkillError",
    "partition_by_visibility",
    # head_bindings (issue #952)
    "FORBIDDEN_PERMISSIONS",
    "HEAD_AGENTS_PACK_KEY",
    "HERMES_PERSONA_ID",
    "KNOWN_PERSONAS",
    "PAPERCLIP_PERSONA_ID",
    "ROLE_KEY_FOR_PERSONA",
    "UnknownPersonaError",
    "bind_persona_to_tenant",
    "guard_persona",
    "is_persona_bound",
    "load_head_agents_pack",
    "persona_subject",
    "unbind_persona_from_tenant",
]
