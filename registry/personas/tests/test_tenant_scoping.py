"""Tenant scoping: a persona belongs to a tenant or the platform default, and
tenants extend the platform library without cross-tenant leakage.
"""

import pytest

from registry import UnknownPersonaError


def test_platform_default_is_inherited_by_any_tenant(scratch):
    """A tenant with no card of its own inherits the platform-default persona."""
    reg = scratch.write_registry(
        {
            "security-sme.yaml": scratch.card_yaml(id="security-sme", name="Security SME"),
        },
        use_platform=True,
    )
    card = reg.get("acme", "security-sme")
    assert card["tenant"] == "platform"
    assert card["name"] == "Security SME"


def test_tenant_card_shadows_platform_default(scratch):
    """A tenant extends the platform library by adding its own card file."""
    scratch.write(
        {"security-sme.yaml": scratch.card_yaml(id="security-sme", name="Security SME")},
        target="platform",
    )
    scratch.write(
        {
            "security-sme.yaml": scratch.card_yaml(
                id="security-sme",
                tenant="acme",
                name="Acme Security SME",
                defaultModelTier="MAX",
            )
        },
        target="tenant",
    )
    reg = scratch.registry(use_platform=True)
    # Platform keeps its own persona; acme resolves its stricter override.
    platform_card = reg.get("platform", "security-sme")
    assert platform_card["name"] == "Security SME"
    acme_card = reg.get("acme", "security-sme")
    assert acme_card["tenant"] == "acme"
    assert acme_card["name"] == "Acme Security SME"
    assert acme_card["defaultModelTier"] == "MAX"


def test_no_cross_tenant_leak(scratch):
    """A persona a tenant defines is never visible to another tenant."""
    reg = scratch.write_registry(
        {"foo.yaml": scratch.card_yaml(id="foo", tenant="acme", name="Acme only")}
    )
    with pytest.raises(UnknownPersonaError):
        reg.get("beta", "foo")  # beta must not fall back into acme's library
    with pytest.raises(UnknownPersonaError):
        reg.get("platform", "foo")  # nor into the platform default set


def test_publish_is_scoped_to_the_owning_tenant(scratch):
    reg = scratch.write_registry(
        {"foo.yaml": scratch.card_yaml(id="foo", tenant="acme", name="Acme only")}
    )
    rec = reg.publish("acme", "foo")
    assert rec["tenant"] == "acme"
    assert reg.resolve("acme", "foo")["id"] == "foo"
    # The persona does not exist for a non-owning tenant.
    with pytest.raises(UnknownPersonaError):
        reg.publish("beta", "foo")


def test_tenant_library_composes_with_platform(scratch):
    """A tenant registry sees its own cards plus platform defaults."""
    scratch.write(
        {"coder.yaml": scratch.card_yaml(id="coder", name="Coder")}, target="platform"
    )
    scratch.write(
        {"acme-specialist.yaml": scratch.card_yaml(id="acme-specialist", tenant="acme")},
        target="tenant",
    )
    reg = scratch.registry(use_platform=True)
    cards = reg.discover()
    assert ("platform", "coder") in cards
    assert ("acme", "acme-specialist") in cards
    assert ("acme", "coder") not in cards  # inherited, not copied
    assert reg.get("acme", "coder")["id"] == "coder"
