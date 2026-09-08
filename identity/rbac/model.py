"""RBAC domain model for agent orgs (Org -> Team -> Agent).

The role-model contract for the platform (issue #12): pure data types and the
``resource:action`` permission language. This module has no I/O; the store
(`store.py`) persists these entities and `resolve.py`, `bindings.py` and
`guard.py` enforce them. Later phases (#35-#38 identity, every guardrail
middleware) consume this contract.

Hierarchy
---------

::

    Org (the tenant) -> Team -> Agent

In this product an Org *is* the tenant - the "agent org" a tenant defines
(EPIC-00). Every Agent belongs to exactly one Team and every Team to exactly
one Org, so the tree is strict and rooted at the Org. A Role is owned by an
Org and is granted either org-wide (a Binding without a team) or inside a
single team (a Binding carrying that team).

The permission language
-----------------------

A permission is a ``resource:action`` string with one reserved wildcard
segment ``*``:

- ``agent:*``  every action on the ``agent`` resource
- ``*:run``    the ``run`` action on every resource
- ``*:*``      every action on every resource (Owner preset only)

Only the *granted* side of a comparison may carry a wildcard; a request is
always a concrete ``resource:action`` pair. A permission is only ever
evaluated inside a single Org - no role, permission or binding crosses an Org
boundary (see `resolve.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

# The one reserved wildcard segment of a permission.
WILDCARD = "*"

# Role ``level`` values - the intended grant width of a role. Enforcement keys
# off a binding's team id (None = org-wide), never off this label alone; the
# label documents intent and drives preset validation.
ROLE_LEVEL_ORG = "org"
ROLE_LEVEL_TEAM = "team"

# Scope-node levels produced by ScopeNode.level.
SCOPE_LEVEL_ORG = "org"
SCOPE_LEVEL_TEAM = "team"
SCOPE_LEVEL_AGENT = "agent"

# Subject types a binding may name.
SUBJECT_USER = "user"
SUBJECT_AGENT = "agent"

# The permission that confers role administration within an Org. Default for
# every Org; a tenant that brings its own preset pack may name its own
# equivalent, stored per-Org as ``Org.role_admin_permission`` (the saas-rbac
# #125 lesson: an invariant keyed to a hardcoded string silently never runs
# for a tenant whose pack speaks a different vocabulary).
DEFAULT_ROLE_ADMIN_PERMISSION = "roles:manage"

# Canonical permissions the engine itself reasons about. The catalog is open:
# presets and custom roles may introduce further ``resource:action`` strings.
PERMISSIONS = {
    "ROLE_MANAGE": DEFAULT_ROLE_ADMIN_PERMISSION,
    "AGENT_RUN": "agent:run",
    "AGENT_READ": "agent:read",
    "SESSION_READ": "session:read",
    "SESSION_MANAGE": "session:manage",
    "TOOL_CALL": "tool:call",
    "ORG_READ": "org:read",
}


@dataclass(frozen=True)
class Org:
    """The tenant - the root of one agent-org hierarchy."""

    id: str
    name: str
    tenant_type: str = "platform"
    role_admin_permission: str = DEFAULT_ROLE_ADMIN_PERMISSION

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Org id must be non-empty")
        if not self.name:
            raise ValueError("Org name must be non-empty")
        if not is_permission(self.role_admin_permission):
            raise ValueError(
                "Org.role_admin_permission must be a resource:action string: "
                f"{self.role_admin_permission!r}"
            )


@dataclass(frozen=True)
class Team:
    """A team inside an Org; the unit team-scoped roles are granted to."""

    id: str
    org_id: str
    name: str


@dataclass(frozen=True)
class Agent:
    """A managed commercial agent (Claude / DeepSeek / ...), always under a Team."""

    id: str
    org_id: str
    team_id: str
    name: str


@dataclass(frozen=True)
class ScopeNode:
    """A node in an org tree that a request targets.

    Levels: org-level (no team/agent), team-level (a team id), agent-level (a
    team id and an agent id under it). An agent-level node must name its team
    because an Agent always sits under exactly one Team.
    """

    org_id: str
    team_id: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if self.agent_id is not None and self.team_id is None:
            raise ValueError("an agent-level scope node must name its team")

    @property
    def level(self) -> str:
        if self.agent_id is not None:
            return SCOPE_LEVEL_AGENT
        if self.team_id is not None:
            return SCOPE_LEVEL_TEAM
        return SCOPE_LEVEL_ORG

    def __str__(self) -> str:
        parts = [self.org_id]
        if self.team_id is not None:
            parts.append(self.team_id)
        if self.agent_id is not None:
            parts.append(self.agent_id)
        return "/".join(parts)


@dataclass(frozen=True)
class Role:
    """A named, Org-owned bundle of permissions.

    ``key`` is the stable machine-readable identifier code matches on (e.g.
    ``admin``); ``name`` is what a human sees. ``permissions`` are
    ``resource:action`` strings, wildcards allowed on the granted side.
    """

    id: str
    org_id: str
    key: str
    name: str
    description: str = ""
    permissions: tuple[str, ...] = ()
    level: str = ROLE_LEVEL_ORG
    is_system: bool = False

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("role key must be non-empty")
        if self.level not in (ROLE_LEVEL_ORG, ROLE_LEVEL_TEAM):
            raise ValueError(f"unknown role level: {self.level!r}")
        for permission in self.permissions:
            if not is_permission(permission):
                raise ValueError(f"invalid permission string on role {self.key}: {permission!r}")


@dataclass(frozen=True)
class Binding:
    """Grants one subject one role at one scope node of an Org.

    ``team_id`` is None for an org-wide grant and the team id for a
    team-scoped grant. A binding never crosses an Org: ``org_id`` pins the
    grant, and resolution only ever consults bindings for the Org asked about.
    """

    id: str
    org_id: str
    subject: str
    subject_type: str = SUBJECT_USER
    role_id: str = ""
    team_id: str | None = None

    @property
    def is_org_wide(self) -> bool:
        return self.team_id is None


@dataclass(frozen=True)
class Session:
    """An agent session.

    Mints only after an authorized principal ran an agent (see guard.py).
    ``roles`` carries the role keys resolved at start time - a snapshot for
    audit and context. Enforcement never trusts the snapshot: it re-resolves
    live bindings from the store on every call, so a revocation takes effect
    immediately (see guard.guard_session).
    """

    id: str
    org_id: str
    team_id: str
    agent_id: str
    subject_id: str
    subject_type: str = SUBJECT_USER
    roles: tuple[str, ...] = ()
    started_at: str = ""


# --- Permission language ----------------------------------------------------


def format_permission(resource: str, action: str) -> str:
    """Combine a resource and action into a well-formed permission string."""
    if not resource or not action:
        raise ValueError("permission resource and action must be non-empty")
    if ":" in resource or ":" in action:
        raise ValueError(
            "permission segments must not contain ':': "
            f"{resource!r}, {action!r}"
        )
    return f"{resource}:{action}"


def split_permission(permission: str) -> tuple[str, str] | None:
    """Split ``resource:action`` into its two segments, or None if malformed."""
    if not isinstance(permission, str):
        return None
    idx = permission.find(":")
    if idx <= 0 or idx != permission.rfind(":") or idx == len(permission) - 1:
        return None
    return permission[:idx], permission[idx + 1 :]


def is_permission(permission: str) -> bool:
    """True when the value is a well-formed ``resource:action`` string.

    Wildcards are legal segments, so ``agent:*`` and ``*:*`` are well formed.
    Anything malformed is simply unrecognized - callers deny by default rather
    than crash.
    """
    return split_permission(permission) is not None


def permission_granted(granted: str, requested: str) -> bool:
    """Does ``granted`` (on a role) satisfy ``requested`` (the check)?

    Wildcards are honored on the granted side only; the requested permission
    is always a concrete ``resource:action`` pair. A malformed string on
    either side resolves to False - never raises.
    """
    g = split_permission(granted)
    r = split_permission(requested)
    if g is None or r is None:
        return False
    resource_ok = g[0] == WILDCARD or g[0] == r[0]
    action_ok = g[1] == WILDCARD or g[1] == r[1]
    return resource_ok and action_ok


def role_grants(role: Role, permission: str) -> bool:
    """Whether a role's permissions include ``permission``, honoring wildcards.

    Used by the no-lockout invariant so that ``*:*`` counts as conferring role
    administration - an Owner-only tenant is the common case.
    """
    return any(permission_granted(p, permission) for p in role.permissions)
