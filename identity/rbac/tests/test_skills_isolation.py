"""Org-wide skill sharing semantics + cross-tenant invisibility (issue #638).

Issue #638 criterion 2: tenant skills are never visible across tenants;
platform skills are shared read-only; an org-scoped share is declared
explicitly. Criterion 3: the tests prove cross-tenant invisibility.
"""

import pytest

from rbac import (
    PLATFORM_ORG,
    VISIBILITY_PLATFORM,
    VISIBILITY_TENANT,
    CrossTenantShareError,
    PlatformSkillImmutableError,
    SkillOwnershipError,
    SkillScopeError,
    SkillShareRegistry,
    UnknownSkillError,
)
from rbac.skills import partition_by_visibility


@pytest.fixture()
def registry():
    reg = SkillShareRegistry()
    # One platform skill, shared read-only with everyone.
    reg.register_platform("mcp-tool-projection", name="MCP tool projection")
    # Two tenants, each with a private skill of the same *shape* (different ids).
    reg.register_tenant("acme-onboarding", owner_org="acme", name="Acme onboarding")
    reg.register_tenant("globex-funnel", owner_org="globex", name="Globex funnel")
    return reg


# --- platform skills are shared read-only -------------------------------------


def test_platform_skill_visible_to_every_tenant(registry):
    for viewer in ("acme", "globex", "initech"):
        visible = {s.id for s in registry.visible_skills(viewer)}
        assert "mcp-tool-projection" in visible, viewer
    assert registry.get("mcp-tool-projection").visibility == VISIBILITY_PLATFORM


def test_platform_skill_cannot_be_modified_or_reowned_by_a_tenant(registry):
    with pytest.raises(PlatformSkillImmutableError):
        registry.share_org_scope(
            "mcp-tool-projection", actor_org="acme", target_orgs=["globex"]
        )


def test_platform_skill_cannot_be_shadowed_by_a_tenant_of_the_same_id(registry):
    with pytest.raises(SkillOwnershipError):
        registry.register_tenant(
            "mcp-tool-projection", owner_org="acme", name="shadow"
        )
    # The platform declaration is intact: no silent override happened.
    assert registry.get("mcp-tool-projection").owner_org == PLATFORM_ORG


def test_a_platform_skill_may_not_carry_an_org_scoped_share():
    """A platform skill is already everyone's; a share on it is refused."""
    from rbac.skills import Skill

    with pytest.raises(SkillScopeError):
        Skill(id="x", owner_org=PLATFORM_ORG, shared_with=frozenset({"acme"}))


# --- tenant skills are never visible across tenants ---------------------------


def test_tenant_skill_is_invisible_to_another_tenant(registry):
    acme = {s.id for s in registry.visible_skills("acme")}
    globex = {s.id for s in registry.visible_skills("globex")}
    assert "acme-onboarding" in acme
    assert "acme-onboarding" not in globex
    assert "globex-funnel" in globex
    assert "globex-funnel" not in acme


def test_cross_tenant_invisibility_is_explicit_in_the_complement(registry):
    invisible = {s.id for s in registry.invisible_skills("acme")}
    assert invisible == {"globex-funnel"}


def test_an_unrelated_tenant_sees_only_platform_skills(registry):
    visible = {s.id for s in registry.visible_skills("initech")}
    assert visible == {"mcp-tool-projection"}


def test_tenant_skill_is_private_to_its_owner_by_default(registry):
    skill = registry.get("acme-onboarding")
    assert skill.visibility == VISIBILITY_TENANT
    assert skill.visible_to("acme")
    assert not skill.visible_to("globex")
    assert not skill.visible_to(PLATFORM_ORG)
    assert skill.shared_with == frozenset()


# --- org-scoped shares are explicit -------------------------------------------


def test_org_scoped_share_grants_exactly_the_named_org(registry):
    registry.share_org_scope(
        "acme-onboarding", actor_org="acme", target_orgs=["globex"]
    )
    assert "acme-onboarding" in {s.id for s in registry.visible_skills("globex")}
    # A third org that was not named still does not see it.
    assert "acme-onboarding" not in {s.id for s in registry.visible_skills("initech")}


def test_share_must_name_at_least_one_org(registry):
    with pytest.raises(SkillScopeError):
        registry.share_org_scope("acme-onboarding", actor_org="acme", target_orgs=[])


def test_only_the_owner_may_share(registry):
    with pytest.raises(SkillOwnershipError):
        registry.share_org_scope(
            "acme-onboarding", actor_org="globex", target_orgs=["initech"]
        )


def test_sharing_another_tenants_skill_to_yourself_is_refused(registry):
    """The dangerous case: tenant B trying to pull tenant A's skill in."""
    with pytest.raises(SkillOwnershipError):
        registry.share_org_scope(
            "acme-onboarding", actor_org="globex", target_orgs=["globex"]
        )
    # Nothing leaked: globex still cannot see acme's skill.
    assert "acme-onboarding" not in {s.id for s in registry.visible_skills("globex")}


def test_unknown_skill_share_is_refused(registry):
    with pytest.raises(UnknownSkillError):
        registry.share_org_scope("ghost", actor_org="acme", target_orgs=["globex"])


def test_a_wildcard_share_is_refused_as_cross_tenant(registry):
    """"Share with everyone" is the widening this module exists to refuse."""
    with pytest.raises(CrossTenantShareError):
        registry.share_org_scope(
            "acme-onboarding", actor_org="acme", target_orgs=["*"]
        )
    # Nothing leaked: the tenant skill is still private to acme.
    assert "acme-onboarding" not in {s.id for s in registry.visible_skills("globex")}
    assert registry.get("acme-onboarding").shared_with == frozenset()


# --- the partition a UI/API labels provenance with ----------------------------


def test_visibility_partition_labels_platform_vs_tenant(registry):
    registry.share_org_scope(
        "acme-onboarding", actor_org="acme", target_orgs=["globex"]
    )
    groups = partition_by_visibility(registry, "globex")
    assert {s.id for s in groups[VISIBILITY_PLATFORM]} == {"mcp-tool-projection"}
    assert {s.id for s in groups[VISIBILITY_TENANT]} == {
        "globex-funnel",
        "acme-onboarding",
    }
    # An org with nothing shared to it has no tenant group at all.
    only_platform = partition_by_visibility(registry, "initech")
    assert VISIBILITY_TENANT not in only_platform
