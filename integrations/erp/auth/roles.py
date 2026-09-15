"""The ERPNext role→permission map, as data, loaded through one seam.

ERPNext's permission model is a table: ``(role, doctype, permission type,
permlevel)``. This module keeps that shape — it is the *data* half of ERP-08,
and the acceptance criterion it answers is "role capabilities gate document
reads/writes".

**The permission language is not defined here.** It belongs to
``identity/rbac`` (``resource:action`` with a reserved wildcard segment), and
this module *derives* its strings into that language and asks the contract
whether one grants another (``rbac.model.permission_granted``). Re-checking a
wildcard locally would be a second implementation of the contract that could
disagree with it; the local code only decides *what to ask*.

**What the contract cannot express, this loader refuses.** ``*`` is a whole
*segment* wildcard in that language, so ``erp.*:read`` is the literal resource
``erp.*`` and grants nothing on ``erp.sales-invoice``. A declaration asking to
cover every kind with ``"kind": "*"`` is therefore refused by name
(``unknown-kind``) rather than accepted: it would produce a grant that reads as
broader than it is and never matches anything — an inert control, which is a
worse outcome than a refusal. Roles that legitimately cover many kinds say so by
listing them, which is also how the upstream data says it.

**Coverage runs both ways** (the invariant ERP-02 applies to its schema/harvest
pairs). Every kind the declaration *covers* must be granted by at least one
role, and every kind a role *grants* must be declared as covered. A kind that
drifts either way is a declaration defect and is refused by name, so the
declaration cannot quietly grow a hole.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

from . import contract
from .model import ACTIONS, SCHEMA_VERSION, Refused

#: The resource namespace ERP documents live under. A dot-suffixed resource per
#: kind (`erp.sales-invoice`) keeps ERP resources from colliding with the
#: platform's own resource names while remaining one concrete segment.
RESOURCE_PREFIX = "erp."

#: A grant covering every action on its resource.
ACTION_WILDCARD = "*"


def resource_for(kind: str) -> str:
    """The ``resource`` segment for an ERP document kind."""
    return f"{RESOURCE_PREFIX}{kind}"


def permission_for(kind: str, action: str) -> str:
    """The ``resource:action`` string a request asks the contract to authorize."""
    return f"{resource_for(kind)}:{action}"


@dataclass(frozen=True)
class RoleGrant:
    """One role's grant on one kind — ERPNext's ``(role, doctype, type, level)``."""

    role: str
    kind: str
    action: str
    permlevel: int = 0

    @property
    def permission(self) -> str:
        """The grant expressed in the consumed contract's language."""
        return permission_for(self.kind, self.action)


@dataclass(frozen=True)
class RoleMap:
    """A validated role→permission map."""

    version: int
    kinds: Tuple[str, ...]
    roles: Mapping[str, Tuple[RoleGrant, ...]]

    @property
    def role_names(self) -> Tuple[str, ...]:
        return tuple(sorted(self.roles))

    def unknown_roles(self, roles: Iterable[str]) -> Tuple[str, ...]:
        """The roles of ``roles`` this map does not declare, sorted.

        Returned rather than dropped: a role name that matches nothing is how a
        typo becomes a silent least-privilege grant.
        """
        return tuple(sorted({r for r in roles if r not in self.roles}))

    def permission_for(self, kind: str, action: str) -> str:
        """The platform permission a request for ``kind``/``action`` requires."""
        return permission_for(kind, action)

    def grants(self, role: str) -> Tuple[RoleGrant, ...]:
        if role not in self.roles:
            raise Refused("unknown-role", role)
        return self.roles[role]

    def permissions(self, role: str) -> Tuple[str, ...]:
        """Every ``resource:action`` string ``role`` grants, sorted and unique."""
        return tuple(sorted({g.permission for g in self.grants(role)}))

    def granted(self, roles: Iterable[str], kind: str, action: str) -> bool:
        """Does the union of ``roles`` grant ``action`` on ``kind``?

        An undeclared kind or action is refused by name, and an undeclared role
        is refused by name too — this never returns False for a name the map
        does not know, because "unknown" and "not granted" are different
        answers and only one of them is a permission decision.
        """
        if kind not in self.kinds:
            raise Refused("unknown-kind", kind)
        if action not in ACTIONS:
            raise Refused("unknown-action", action)
        unknown = self.unknown_roles(roles)
        if unknown:
            raise Refused(
                "unknown-role",
                f"{', '.join(unknown)} (declared roles: {', '.join(self.role_names)})",
            )
        rbac_model = contract.rbac().model
        requested = permission_for(kind, action)
        for role in roles:
            for grant in self.roles[role]:
                if rbac_model.permission_granted(grant.permission, requested):
                    return True
        return False

    def to_json(self) -> Dict[str, Any]:
        """The canonical form, so ``check`` can compare a load against its source."""
        return {
            "version": self.version,
            "kinds": list(self.kinds),
            "roles": {
                role: [
                    {"kind": g.kind, "action": g.action, "permlevel": g.permlevel}
                    for g in sorted(self.roles[role], key=lambda g: (g.kind, g.action, g.permlevel))
                ]
                for role in sorted(self.roles)
            },
        }


def _as_mapping(source: Any, what: str) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise Refused("declaration-invalid", f"{what}: no such file {path}")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refused("declaration-invalid", f"{what}: unreadable ({exc})") from exc
        if not isinstance(loaded, Mapping):
            raise Refused("declaration-invalid", f"{what}: top level is not an object")
        return loaded
    raise Refused("declaration-invalid", f"{what}: expected a mapping or a path")


def load(source: Any) -> RoleMap:
    """Load and fully validate a role map from a path *or* an in-memory mapping.

    One seam, like ``definitions.load`` in the CRM lane: a test can hand this
    function a mapping and exercise the same validation the shipped catalogue
    gets, so the refusals are provable without writing fixture files.
    """
    document = _as_mapping(source, "role map")

    version = document.get("version")
    if version != SCHEMA_VERSION:
        raise Refused("declaration-invalid", f"version must be {SCHEMA_VERSION}, got {version!r}")

    kinds = document.get("kinds")
    if not isinstance(kinds, list) or not kinds:
        raise Refused("empty-role-map", "the declaration covers no kinds")
    for kind in kinds:
        if not isinstance(kind, str) or not kind:
            raise Refused("declaration-invalid", f"kind {kind!r} is not a name")
        if ACTION_WILDCARD in kind:
            raise Refused(
                "unknown-kind",
                f"{kind!r}: a kind wildcard cannot be honoured — the contract's '*' is a "
                f"whole-segment wildcard, so {resource_for(kind)!r} would match no resource",
            )
    declared_kinds = tuple(sorted(set(kinds)))
    if len(declared_kinds) != len(kinds):
        raise Refused("declaration-invalid", "the kinds list repeats a kind")

    roles_doc = document.get("roles")
    if not isinstance(roles_doc, Mapping) or not roles_doc:
        raise Refused("empty-role-map", "no roles are declared")

    roles: Dict[str, Tuple[RoleGrant, ...]] = {}
    covered: set[str] = set()
    for role_name in sorted(roles_doc):
        if not isinstance(role_name, str) or not role_name:
            raise Refused("declaration-invalid", f"role name {role_name!r} is not a name")
        entries = roles_doc[role_name]
        if not isinstance(entries, list) or not entries:
            raise Refused(
                "declaration-invalid", f"role {role_name!r} declares no permissions"
            )
        seen: set[tuple[str, str, int]] = set()
        grants = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise Refused("declaration-invalid", f"role {role_name!r}: {entry!r} is not an object")
            kind = entry.get("kind")
            action = entry.get("action")
            permlevel = entry.get("permlevel", 0)
            if not isinstance(kind, str) or not kind:
                raise Refused("declaration-invalid", f"role {role_name!r}: kind {kind!r} is not a name")
            if kind not in declared_kinds:
                raise Refused(
                    "unknown-kind",
                    f"role {role_name!r} grants {kind!r}, which the declaration does not cover "
                    f"(covered: {', '.join(declared_kinds)})",
                )
            if action != ACTION_WILDCARD and action not in ACTIONS:
                raise Refused(
                    "unknown-action",
                    f"role {role_name!r} grants {action!r} on {kind!r} "
                    f"(declared actions: {', '.join(ACTIONS)})",
                )
            if not isinstance(permlevel, int) or isinstance(permlevel, bool) or permlevel < 0:
                raise Refused(
                    "declaration-invalid",
                    f"role {role_name!r} on {kind!r}: permlevel {permlevel!r} is not a level",
                )
            key = (kind, action, permlevel)
            if key in seen:
                raise Refused(
                    "declaration-invalid",
                    f"role {role_name!r} grants {action!r} on {kind!r} at level {permlevel} twice",
                )
            seen.add(key)
            grant = RoleGrant(role=role_name, kind=kind, action=action, permlevel=permlevel)
            # The derived string must be legal in the contract's language. If
            # this lane names a resource the contract will not parse, every
            # request against it would be refused for the wrong reason.
            if not contract.rbac().is_permission(grant.permission):
                raise Refused(
                    "declaration-invalid",
                    f"role {role_name!r}: {grant.permission!r} is not a permission "
                    f"in the identity/rbac language",
                )
            grants.append(grant)
            covered.add(kind)
        roles[role_name] = tuple(grants)

    missing = tuple(k for k in declared_kinds if k not in covered)
    if missing:
        raise Refused(
            "declaration-invalid",
            f"kind(s) covered by no role: {', '.join(missing)} "
            f"(a kind no role may touch is a hole in the declaration, not a policy)",
        )

    return RoleMap(version=version, kinds=declared_kinds, roles=roles)


def load_default() -> RoleMap:
    """The shipped role map, from ``catalog/roles.json``."""
    return load(Path(__file__).resolve().parent / "catalog" / "roles.json")
