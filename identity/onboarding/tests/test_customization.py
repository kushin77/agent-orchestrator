"""Per-tenant instruction customization within the allowed contract
(issue #14, work item 10).

Positive path: allowed fields (defaultModelTier, memoryScope, instructionLayers)
apply and render deterministically. Negative path: disallowed fields, unknown
enums, unresolved prompt-module refs, system-layer inline text and duplicate
priorities are all rejected - and nothing is written on rejection.
"""

import pytest
import yaml

from identity.onboarding.customization import (
    OverlayValidationError,
    UnknownTenantError,
    apply_customization,
    render_instruction_manifest,
    validate_overlay,
)
from identity.onboarding.provisioning import provision


@pytest.fixture()
def provisioned_acme(stores, make_spec):
    store, rbac_store = stores
    provision(store, rbac_store, make_spec())
    return store, rbac_store, "acme"


def test_valid_overlay_applies_and_renders_deterministically(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    overlay = yaml.safe_load(
        """
        defaultModelTier: HIGH
        memoryScope: [repository]
        instructionLayers:
          - layer: system
            ref: classify-route@v2
            priority: 10
          - layer: tenant
            inline: "Always follow Acme compliance policy."
            priority: 20
        """
    )
    applied = apply_customization(store, slug, overlay)
    assert set(applied.overlay) == {"defaultModelTier", "memoryScope", "instructionLayers"}

    manifest = render_instruction_manifest(store, slug)
    assert [entry["order"] for entry in manifest] == [10, 20]
    assert render_instruction_manifest(store, slug) == manifest  # deterministic


def test_apply_is_idempotent_replace(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    first = apply_customization(store, slug, {"defaultModelTier": "LOW"})
    second = apply_customization(store, slug, {"defaultModelTier": "HIGH"})
    assert store.get_customization(slug) is second
    assert second.applied_at == first.applied_at
    assert store.dump()["customizations"][slug]["overlay"] == {"defaultModelTier": "HIGH"}


def test_disallowed_field_rejected_and_nothing_written(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, {"stealSecrets": True})
    assert store.get_customization(slug) is None


def test_bad_model_tier_rejected(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, {"defaultModelTier": "ULTRA"})
    assert store.get_customization(slug) is None


def test_bad_memory_scope_rejected(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, {"memoryScope": ["planet"]})
    assert store.get_customization(slug) is None


def test_system_layer_inline_text_rejected(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    overlay = {
        "instructionLayers": [
            {"layer": "system", "inline": "raw system text", "priority": 1}
        ]
    }
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, overlay)
    assert store.get_customization(slug) is None


def test_unresolved_prompt_ref_rejected(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    overlay = {
        "instructionLayers": [
            {"layer": "system", "ref": "ghost-module@v99", "priority": 1}
        ]
    }
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, overlay)
    assert store.get_customization(slug) is None


def test_layer_without_ref_or_inline_rejected(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, {"instructionLayers": [{"layer": "tenant", "priority": 1}]})


def test_duplicate_priority_rejected(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    overlay = {
        "instructionLayers": [
            {"layer": "tenant", "inline": "a", "priority": 5},
            {"layer": "tenant", "inline": "b", "priority": 5},
        ]
    }
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, overlay)


def test_unknown_layer_field_rejected(provisioned_acme):
    store, _rbac, slug = provisioned_acme
    with pytest.raises(OverlayValidationError):
        apply_customization(store, slug, {"instructionLayers": [{"layer": "tenant", "inline": "x", "priority": 1, "surprise": 1}]})


def test_customizing_unknown_tenant_rejected(stores, make_spec):
    store, _rbac = stores
    with pytest.raises(UnknownTenantError):
        apply_customization(store, "nope", {"defaultModelTier": "LOW"})


def test_validate_returns_normalized_copy_only():
    normalized = validate_overlay({"defaultModelTier": "LOW"})
    assert normalized == {"defaultModelTier": "LOW"}


def test_empty_overlay_is_valid_noop():
    assert validate_overlay({}) == {}
