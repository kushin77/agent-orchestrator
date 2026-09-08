"""Preset role packs per tenant type, with a custom-pack seam.

Every Org is seeded from exactly one pack (mirrors saas-rbac ``presets.ts``).
Built-in packs live as YAML beside this module - ``platform``, ``startup``,
``smb``, ``enterprise`` - and each maps to a tenant type. A tenant that brings
its own RBAC model supplies a custom pack through ``parse_pack`` /
``register_pack`` (the custom-pack seam): the saas-rbac consumer precedent is a
client-defined pack of seven roles over its own 32-permission catalog, seeded
through the exact same path as the platform pack.

A pack may name role administration in its own vocabulary via
``role_admin_permission`` (the saas-rbac #125 lesson). Seeding refuses a pack
whose roles do not grant the Org's role-admin permission, so an Org can never
be born with no way to administer itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from rbac.model import (
    DEFAULT_ROLE_ADMIN_PERMISSION,
    ROLE_LEVEL_ORG,
    ROLE_LEVEL_TEAM,
    Role,
    is_permission,
    permission_granted,
)

_PRESETS_DIR = Path(__file__).resolve().parent

# Tenant types served by the built-in packs on disk.
BUILTIN_TENANT_TYPES: tuple[str, ...] = ("platform", "startup", "smb", "enterprise")

_custom_packs: dict[str, "RolePack"] = {}


@dataclass(frozen=True)
class RolePreset:
    """One role of a pack - plain data, independent of any Org."""

    key: str
    name: str
    description: str
    permissions: tuple[str, ...]
    level: str = ROLE_LEVEL_ORG
    is_system: bool = True


@dataclass(frozen=True)
class RolePack:
    """A named set of role presets plus its role-admin permission."""

    key: str
    name: str
    description: str = ""
    role_admin_permission: str = DEFAULT_ROLE_ADMIN_PERMISSION
    roles: tuple[RolePreset, ...] = ()

    def role_keys(self) -> tuple[str, ...]:
        return tuple(role.key for role in self.roles)

    def find(self, key: str) -> RolePreset | None:
        for role in self.roles:
            if role.key == key:
                return role
        return None

    def grants_permission(self, permission: str) -> bool:
        """Whether any role in the pack grants ``permission`` (wildcards count)."""
        return any(
            permission_granted(granted, permission)
            for role in self.roles
            for granted in role.permissions
        )


def parse_pack(yaml_text: str, *, fallback_key: str | None = None) -> RolePack:
    """Parse and validate a pack from YAML text (the custom-pack seam).

    Accepts any YAML document matching the pack schema documented in
    README.md, so a tenant can define its own roles, permission catalog and
    role-admin permission without touching this module or the built-in packs.
    """
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError("pack YAML must be a mapping with 'key', 'name' and 'roles'")

    key = data.get("key") or fallback_key
    if not isinstance(key, str) or not key:
        raise ValueError("pack YAML must declare a non-empty string 'key'")
    name = data.get("name") or key
    description = data.get("description") or ""
    role_admin = data.get("role_admin_permission") or DEFAULT_ROLE_ADMIN_PERMISSION
    if not is_permission(role_admin):
        raise ValueError(f"invalid role_admin_permission in pack {key!r}: {role_admin!r}")

    raw_roles = data.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        raise ValueError(f"pack {key!r} must declare a non-empty 'roles' list")

    presets: list[RolePreset] = []
    seen: set[str] = set()
    for raw in raw_roles:
        if not isinstance(raw, dict):
            raise ValueError(f"pack {key!r}: each role must be a mapping")
        role_key = raw.get("key")
        if not isinstance(role_key, str) or not role_key:
            raise ValueError(f"pack {key!r}: each role needs a non-empty string 'key'")
        if role_key in seen:
            raise ValueError(f"pack {key!r}: duplicate role key {role_key!r}")
        seen.add(role_key)
        name_r = raw.get("name") or role_key
        level = raw.get("level") or ROLE_LEVEL_ORG
        if level not in (ROLE_LEVEL_ORG, ROLE_LEVEL_TEAM):
            raise ValueError(
                f"pack {key!r} role {role_key!r}: level must be 'org' or 'team'"
            )
        raw_permissions = raw.get("permissions") or []
        if not isinstance(raw_permissions, list):
            raise ValueError(
                f"pack {key!r} role {role_key!r}: 'permissions' must be a list"
            )
        permissions: list[str] = []
        for p in raw_permissions:
            if not isinstance(p, str) or not is_permission(p):
                raise ValueError(
                    f"pack {key!r} role {role_key!r}: invalid permission {p!r}"
                )
            permissions.append(p)
        presets.append(
            RolePreset(
                key=role_key,
                name=str(name_r),
                description=str(raw.get("description") or ""),
                permissions=tuple(permissions),
                level=level,
                is_system=bool(raw.get("is_system", True)),
            )
        )

    return RolePack(
        key=key,
        name=str(name),
        description=str(description),
        role_admin_permission=role_admin,
        roles=tuple(presets),
    )


def load_pack(key: str) -> RolePack:
    """Load a built-in pack from the on-disk YAML beside this module."""
    path = _PRESETS_DIR / f"{key}.yaml"
    if not path.is_file():
        raise KeyError(
            f"unknown built-in pack {key!r}; registered built-ins: "
            f"{', '.join(BUILTIN_TENANT_TYPES)}"
        )
    return parse_pack(path.read_text(encoding="utf-8"), fallback_key=key)


def register_pack(pack: RolePack) -> None:
    """Register a custom pack under its key (the custom-pack seam)."""
    if pack.key in _custom_packs:
        raise ValueError(f"custom pack already registered: {pack.key}")
    _custom_packs[pack.key] = pack


def resolve_pack(tenant_type: str) -> RolePack:
    """Pick the pack for a tenant type: a built-in, a registered custom, else error."""
    if tenant_type in BUILTIN_TENANT_TYPES:
        return load_pack(tenant_type)
    if tenant_type in _custom_packs:
        return _custom_packs[tenant_type]
    raise KeyError(
        f"no pack for tenant type {tenant_type!r}; use a built-in "
        f"({', '.join(BUILTIN_TENANT_TYPES)}) or register a custom pack"
    )


def seed_org(store, org, pack: RolePack | None = None) -> list[Role]:
    """Seed an Org's system roles from a pack, in pack order.

    ``pack`` defaults to the pack resolved for ``org.tenant_type``. The Org's
    declared ``role_admin_permission`` must be granted by the pack - otherwise
    the Org would be born with no way to administer itself, so seeding refuses
    and creates nothing.
    """
    if pack is None:
        pack = resolve_pack(org.tenant_type)
    if not pack.grants_permission(org.role_admin_permission):
        raise ValueError(
            f"pack {pack.key!r} does not grant the org's role-admin permission "
            f"{org.role_admin_permission!r}; refusing to seed an unadministrable org"
        )

    created: list[Role] = []
    for preset in pack.roles:
        created.append(
            store.create_role(
                org_id=org.id,
                key=preset.key,
                name=preset.name,
                description=preset.description,
                permissions=preset.permissions,
                level=preset.level,
                is_system=preset.is_system,
            )
        )
    return created
