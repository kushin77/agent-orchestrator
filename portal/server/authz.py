"""portal.server.authz — console RBAC (super-admin vs tenant-admin).

The permission vocabulary is *consumed* from the merged identity/rbac
platform role pack (issue #12, ``identity/rbac/presets/platform.yaml``):
roles ``owner`` (``*:*``), ``admin``, ``team-admin``, ``agent-operator``,
``member``, ``viewer``. Console-scoped control-plane additions
(``policy:read/manage``, ``approval:*``, ``event:read``) are the documented
additions a tenant grants via the owner preset (cpapi, issue #38). The SSO
token's ``root_admin`` claim (issue #35 allowlist) makes a principal a
super-admin who can act in every tenant.

Authorization follows the two-gate shape of the control plane (issue #38):
a scope gate (can this subject act in this tenant at all?) runs before the
permission gate (does its role grant ``resource:action``?). There is no
cross-tenant fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

#: rbac platform pack role -> permissions (frozen upstream, issue #12) plus
#: the documented control-plane additions for policy/approval/event surfaces.
RBAC_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "owner": ("*:*",),
    "admin": (
        "org:read", "org:manage", "team:read", "team:manage",
        "member:read", "member:invite", "member:remove",
        "roles:read", "roles:manage",
        "agent:read", "agent:create", "agent:write", "agent:delete", "agent:run",
        "session:read", "session:manage",
        "prompt:read", "prompt:write",
        "model:read", "model:manage",
        "budget:read", "budget:manage",
        "tool:manage", "tool:call",
        "audit:read",
        # control-plane additions (issue #38 admin surface): read-only on
        # policy controls (only owner/super-admin may flip them)
        "policy:read",
        "approval:read", "approval:approve",
        "event:read",
    ),
    "team-admin": (
        "team:read", "member:read", "member:invite", "member:remove",
        "agent:read", "agent:create", "agent:write", "agent:delete", "agent:run",
        "session:read", "session:manage",
        "tool:call",
        "prompt:read", "policy:read", "approval:read",
    ),
    "agent-operator": (
        "agent:read", "agent:run", "session:read", "session:start", "session:stop",
        "tool:call", "prompt:read", "policy:read", "approval:read",
    ),
    "member": (
        "org:read", "team:read", "agent:read", "session:read", "prompt:read",
        "policy:read", "approval:read",
    ),
    "viewer": ("org:read", "team:read", "agent:read", "policy:read"),
}

#: Endpoint permission map (permission required to reach each surface).
SURFACE_PERMISSIONS: dict[str, str] = {
    "overview": "org:read",
    "agents": "agent:read",
    "personas": "prompt:read",
    "prompts": "prompt:read",
    "policies": "policy:read",
    "budgets": "budget:read",
    "usage": "budget:read",
    "audit": "audit:read",
    "approvals": "approval:read",
}


@dataclass
class Principal:
    """An authenticated console principal."""

    email: str
    role: str  # root_admin (super-admin) or the #35 user claim
    super_admin: bool = False
    bindings: list = field(default_factory=list)  # [(tenant_id, org_role)]

    def org_role_in(self, tenant_id: str) -> str:
        for bound_tenant, bound_role in self.bindings:
            if bound_tenant == tenant_id:
                return bound_role
        return ""


class Authorizer:
    """Scope + permission gates over a console principal."""

    def scope_tenants(self, principal: Principal, all_tenants: Iterable[str]) -> list[str]:
        if principal.super_admin:
            return list(all_tenants)
        return sorted({tenant for tenant, _ in principal.bindings})

    def in_scope(self, principal: Principal, tenant_id: str) -> bool:
        if principal.super_admin:
            return True
        return any(bound == tenant_id for bound, _ in principal.bindings)

    def role_for(self, principal: Principal, tenant_id: str) -> str:
        """The org role that governs a tenant (owner for super-admin)."""
        if principal.super_admin:
            return "owner"
        return principal.org_role_in(tenant_id)

    def allow(self, principal: Principal, tenant_id: str, permission: str) -> bool:
        """Two-gate: scope first, then the role's permission set."""
        if not self.in_scope(principal, tenant_id):
            return False
        role = self.role_for(principal, tenant_id)
        if not role:
            return False
        perms = RBAC_ROLE_PERMISSIONS.get(role, ())
        if "*:*" in perms:
            return True
        resource, _, action = permission.partition(":")
        for granted in perms:
            granted_resource, _, granted_action = granted.partition(":")
            if granted_resource == "*" or granted_resource == resource:
                if granted_action == "*" or granted_action == action:
                    return True
        return False
