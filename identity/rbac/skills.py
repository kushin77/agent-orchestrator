"""Org-wide skill sharing semantics and cross-tenant isolation (issue #638).

The skills adapter (``integrations/paperclip/adapters/skills/``) declares
*which* ``SKILL.md`` files the platform may load and validates their provenance
and requirements. It deliberately carries **no tenancy**: a declaration has no
owner and no visibility. This module supplies that missing half - the workbook's
Pillar 3 question - so a skill's *audience* is a first-class, enforced property:

- A **platform** skill is owned by the platform itself and is shared read-only:
  every tenant sees it, no tenant may modify it, and no tenant may shadow it
  with a tenant-pack declaration of its own id.
- A **tenant** skill is owned by exactly one Org. It is **never** visible to
  another tenant - not by a wildcard, not by an org-scoped share, not by a
  share whose audience is the wrong Org. Cross-tenant invisibility is the
  module's central invariant.
- An **org-scoped share** (``share_org_scope``) publishes a tenant's own skill
  to a *named set of orgs* (an explicit allowlist) - and only the owner may
  create it, and it may never name an org the skill's owner did not authorise
  by being asked for it. There is no implicit widening: an org-scoped share
  starts empty and every grant is explicit.

Visibility is computed, never stored as a mutable flag on the skill itself:
``visible_skills(registry, viewer_org)`` folds the declarations and the shares
into the one list a viewer may see. That is deliberate - the dangerous failure
mode of a stored visibility field is that it silently drifts from the shares
that justify it (the same reasoning as the RBAC guard re-resolving live
bindings instead of trusting a session snapshot).

Scope identifiers
-----------------

An Org is the tenant (``model.Org``), so a skill's ``owner_org`` is an Org id
and ``PLATFORM_ORG`` is the reserved platform owner - mirroring the persona
registry's ``tenant: platform`` convention, and never a real Org id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

#: The reserved owner of a platform skill. Mirrors ``tenant: platform`` in the
#: persona registry: it is an owner, never a tenant, and never a real Org id.
PLATFORM_ORG = "platform"

#: A skill's visibility class. ``platform`` is shared read-only to everyone;
#: ``tenant`` is private to its owner unless an org-scoped share widens it.
VISIBILITY_PLATFORM = "platform"
VISIBILITY_TENANT = "tenant"

VISIBILITIES: tuple[str, ...] = (VISIBILITY_PLATFORM, VISIBILITY_TENANT)

#: The pseudo-org a share must never name. ``share_org_scope`` accepts only
#: concrete org ids, so a caller asking to "share with everyone" is refused
#: rather than silently widened to every tenant (``CrossTenantShareError``).
WILDCARD_TARGET = "*"


class SkillScopeError(Exception):
    """Base class for every error this module raises."""


class UnknownSkillError(SkillScopeError):
    """No skill with the requested id is registered."""

    def __init__(self, skill_id: str) -> None:
        super().__init__(f"no skill with id {skill_id!r} is registered")
        self.skill_id = skill_id


class SkillOwnershipError(SkillScopeError):
    """An operation was attempted by a principal that does not own the skill."""

    def __init__(self, skill_id: str, actor_org: str, owner_org: str) -> None:
        super().__init__(
            f"org {actor_org!r} does not own skill {skill_id!r} "
            f"(owned by {owner_org!r})"
        )
        self.skill_id = skill_id
        self.actor_org = actor_org
        self.owner_org = owner_org


class PlatformSkillImmutableError(SkillScopeError):
    """A tenant attempted to modify or shadow a platform skill (read-only)."""

    def __init__(self, skill_id: str, actor_org: str) -> None:
        super().__init__(
            f"platform skill {skill_id!r} is shared read-only; org {actor_org!r} "
            "cannot modify, re-own or shadow it"
        )
        self.skill_id = skill_id
        self.actor_org = actor_org


class CrossTenantShareError(SkillScopeError):
    """A share would make a tenant skill visible outside its owner's tenant."""

    def __init__(self, skill_id: str, owner_org: str, target_orgs: tuple[str, ...]) -> None:
        super().__init__(
            f"refusing to share tenant skill {skill_id!r} (owned by {owner_org!r}) "
            f"with {', '.join(target_orgs)}: a tenant skill is never visible "
            "across tenants; declare it platform-owned instead"
        )
        self.skill_id = skill_id
        self.owner_org = owner_org
        self.target_orgs = target_orgs


@dataclass(frozen=True)
class Skill:
    """One registered skill and the tenant that owns it.

    ``owner_org`` is ``PLATFORM_ORG`` for a platform skill. ``visibility`` is
    derived from the owner - a platform-owned skill is ``platform``, anything
    else is ``tenant`` - so the two can never disagree.
    """

    id: str
    owner_org: str
    name: str = ""
    description: str = ""
    #: The orgs this skill is explicitly shared to. Empty for a private tenant
    #: skill; populated only by ``share_org_scope``. Meaningless for a platform
    #: skill (already visible to every org) and rejected if set on one.
    shared_with: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.id:
            raise SkillScopeError("skill id must be non-empty")
        if not self.owner_org:
            raise SkillScopeError(f"skill {self.id!r}: owner_org must be non-empty")
        if self.owner_org == PLATFORM_ORG and self.shared_with:
            raise SkillScopeError(
                f"skill {self.id!r}: a platform skill is already visible to every "
                "org and must not carry an org-scoped share"
            )

    @property
    def visibility(self) -> str:
        return (
            VISIBILITY_PLATFORM
            if self.owner_org == PLATFORM_ORG
            else VISIBILITY_TENANT
        )

    @property
    def is_platform(self) -> bool:
        return self.owner_org == PLATFORM_ORG

    def visible_to(self, viewer_org: str) -> bool:
        """Whether ``viewer_org`` may see this skill.

        Platform skills: always. A tenant skill: only its owner, or an org the
        owner explicitly shared to. Nothing else - no wildcard, no fallback.
        """
        if self.is_platform:
            return True
        if viewer_org == self.owner_org:
            return True
        return viewer_org in self.shared_with


@dataclass
class SkillShareRegistry:
    """The skill audience registry: declarations plus explicit org-scoped shares.

    Registration is keyed by skill id and **refuses a duplicate id**, so a
    tenant cannot shadow a platform skill by registering one of the same id -
    the collision is a loud error, never a silent override. All mutations go
    through the methods here so the no-shadowing and no-cross-tenant rules are
    the only way to add a skill.
    """

    _skills: dict[str, Skill] = field(default_factory=dict)

    # --- registration --------------------------------------------------------

    def register(
        self,
        skill_id: str,
        *,
        owner_org: str = PLATFORM_ORG,
        name: str = "",
        description: str = "",
    ) -> Skill:
        """Register a skill owned by ``owner_org``.

        Refuses a duplicate id: a platform skill cannot be shadowed by a tenant
        declaring the same id, and a tenant skill cannot be re-registered by
        another tenant. The refusal names both owners so the collision is
        attributable.
        """
        existing = self._skills.get(skill_id)
        if existing is not None:
            raise SkillOwnershipError(skill_id, owner_org, existing.owner_org)
        skill = Skill(
            id=skill_id,
            owner_org=owner_org,
            name=name or skill_id,
            description=description,
        )
        self._skills[skill_id] = skill
        return skill

    def register_platform(self, skill_id: str, **kwargs: str) -> Skill:
        """Register a platform skill (shared read-only with every org)."""
        return self.register(skill_id, owner_org=PLATFORM_ORG, **kwargs)

    def register_tenant(self, skill_id: str, owner_org: str, **kwargs: str) -> Skill:
        """Register a skill owned by one tenant (private until shared)."""
        if owner_org == PLATFORM_ORG:
            raise SkillScopeError(
                f"register_tenant({skill_id!r}): {PLATFORM_ORG!r} is the reserved "
                "platform owner; use register_platform() instead"
            )
        return self.register(skill_id, owner_org=owner_org, **kwargs)

    # --- explicit org-scoped sharing -----------------------------------------

    def share_org_scope(
        self, skill_id: str, *, actor_org: str, target_orgs: Iterable[str]
    ) -> Skill:
        """Publish a tenant skill to a named set of orgs (an explicit allowlist).

        Only the owner may share, and every target is named explicitly -
        there is no "share with all" and no wildcard. A platform skill is
        already visible to everyone, so sharing one is refused rather than
        silently accepted. Sharing a *tenant* skill to the platform owner is
        allowed (the owner is publishing it upward) but it still never becomes
        visible to a different tenant.
        """
        skill = self.get(skill_id)
        if skill.is_platform:
            raise PlatformSkillImmutableError(skill_id, actor_org)
        if actor_org != skill.owner_org:
            raise SkillOwnershipError(skill_id, actor_org, skill.owner_org)

        targets = frozenset(str(t) for t in target_orgs)
        if not targets:
            raise SkillScopeError(
                f"share_org_scope({skill_id!r}): no target orgs given; an "
                "org-scoped share must name at least one org explicitly"
            )
        if WILDCARD_TARGET in targets:
            # A wildcard share would publish a tenant skill to *every* tenant -
            # the exact cross-tenant widening this module exists to refuse.
            raise CrossTenantShareError(skill_id, skill.owner_org, tuple(sorted(targets)))
        updated = Skill(
            id=skill.id,
            owner_org=skill.owner_org,
            name=skill.name,
            description=skill.description,
            shared_with=skill.shared_with | targets,
        )
        self._skills[skill_id] = updated
        return updated

    # --- reads ---------------------------------------------------------------

    def get(self, skill_id: str) -> Skill:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise UnknownSkillError(skill_id)
        return skill

    def all_skills(self) -> tuple[Skill, ...]:
        return tuple(self._skills[k] for k in sorted(self._skills))

    def visible_skills(self, viewer_org: str) -> tuple[Skill, ...]:
        """Every skill ``viewer_org`` may see, ordered by id.

        The single audience query: platform skills plus the viewer's own skills
        plus any tenant skill explicitly shared to it. A skill owned by another
        tenant that was *not* shared to the viewer never appears.
        """
        return tuple(s for s in self.all_skills() if s.visible_to(viewer_org))

    def invisible_skills(self, viewer_org: str) -> tuple[Skill, ...]:
        """Every registered skill ``viewer_org`` may **not** see.

        The complement of ``visible_skills`` - useful for an audit that must
        prove what a tenant was *not* shown, not only what it was.
        """
        return tuple(s for s in self.all_skills() if not s.visible_to(viewer_org))


def partition_by_visibility(
    registry: SkillShareRegistry, viewer_org: str
) -> dict[str, tuple[Skill, ...]]:
    """Split what ``viewer_org`` sees into ``platform`` and ``tenant`` groups.

    A convenience for a UI/API that must label a skill's provenance: "shared by
    the platform" versus "owned/shared within your org". Keys are the
    ``VISIBILITY_*`` constants; a group is absent when empty.
    """
    visible = registry.visible_skills(viewer_org)
    groups: dict[str, list[Skill]] = {}
    for skill in visible:
        groups.setdefault(skill.visibility, []).append(skill)
    return {key: tuple(value) for key, value in groups.items()}
