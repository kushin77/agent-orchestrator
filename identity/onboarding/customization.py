"""Per-tenant instruction customization within the allowed contract
(issue #14, work item 10).

A tenant may customize how its agents' instructions are layered, but only
inside the fields the platform contract allows. The overlay is validated
against an *allowed-fields allowlist*; anything else is rejected before any
write (fail closed). This mirrors the harvested CMR
``controller/customize-instructions.sh`` design - a declarative per-tenant
profile with a closed schema (``additionalProperties: false``) that renders
deterministically - adapted to the platform's existing contracts:

- ``defaultModelTier`` - the AgentProfile tier enum (registry/profiles)
- ``memoryScope`` - the AgentProfile memory-scope enum (registry/profiles)
- ``instructionLayers`` - ordered per-tenant instruction layers. A layer
  either references a registered prompt module (``ref``, resolved against
  registry/prompts - fail closed) or carries tenant free text (``inline``).
  ``inline`` text is only permitted at the ``tenant`` layer: the ``system``
  prompt content is owned by the platform registry and is never open to raw
  tenant injection.

Disallowed keys, unknown enums, unknown module refs, and system-layer inline
text are all rejected with ``OverlayValidationError`` - the negative-test
surface for this lane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from identity.onboarding import registry_assets
from identity.onboarding.model import (
    INSTRUCTION_LAYER_KINDS,
    MEMORY_SCOPES,
    MODEL_TIERS,
    TenantCustomization,
    utcnow_iso,
)

ALLOWED_OVERLAY_FIELDS: tuple[str, ...] = (
    "defaultModelTier",
    "memoryScope",
    "instructionLayers",
)

_ALLOWED_LAYER_FIELDS: tuple[str, ...] = ("layer", "ref", "inline", "priority")


class OverlayValidationError(ValueError):
    """The customization overlay violates the allowed-fields contract."""


class UnknownTenantError(KeyError):
    """No tenant row exists to customize."""


def validate_overlay(overlay: Any, *, repo_root: Path | None = None) -> dict[str, Any]:
    """Validate an overlay against the allowlist; return a normalized copy.

    Raises ``OverlayValidationError`` on the first violation. No state is
    touched, so a rejected overlay never leaves a partial customization.
    """
    if not isinstance(overlay, dict):
        raise OverlayValidationError(
            f"customization overlay must be a mapping, got {type(overlay).__name__}"
        )
    unknown = sorted(set(overlay) - set(ALLOWED_OVERLAY_FIELDS))
    if unknown:
        raise OverlayValidationError(
            f"disallowed customization field(s): {', '.join(unknown)}; "
            f"allowed fields: {', '.join(ALLOWED_OVERLAY_FIELDS)}"
        )

    normalized: dict[str, Any] = {}

    if "defaultModelTier" in overlay:
        tier = overlay["defaultModelTier"]
        if tier not in MODEL_TIERS:
            raise OverlayValidationError(
                f"defaultModelTier {tier!r} not allowed; "
                f"must be one of {', '.join(MODEL_TIERS)}"
            )
        normalized["defaultModelTier"] = tier

    if "memoryScope" in overlay:
        scopes = overlay["memoryScope"]
        if not isinstance(scopes, list) or not scopes:
            raise OverlayValidationError("memoryScope must be a non-empty list")
        if any(scope not in MEMORY_SCOPES for scope in scopes):
            raise OverlayValidationError(
                f"memoryScope entries must be in {', '.join(MEMORY_SCOPES)}"
            )
        if len(set(scopes)) != len(scopes):
            raise OverlayValidationError("memoryScope entries must be unique")
        normalized["memoryScope"] = list(scopes)

    if "instructionLayers" in overlay:
        normalized["instructionLayers"] = _validate_layers(
            overlay["instructionLayers"], repo_root=repo_root
        )

    return normalized


def _validate_layers(raw_layers: Any, *, repo_root: Path | None) -> list[dict[str, Any]]:
    if not isinstance(raw_layers, list) or not raw_layers:
        raise OverlayValidationError("instructionLayers must be a non-empty list")
    seen_priorities: set[int] = set()
    layers: list[dict[str, Any]] = []
    for raw in raw_layers:
        if not isinstance(raw, dict):
            raise OverlayValidationError("each instruction layer must be a mapping")
        unknown = sorted(set(raw) - set(_ALLOWED_LAYER_FIELDS))
        if unknown:
            raise OverlayValidationError(
                f"disallowed instruction-layer field(s): {', '.join(unknown)}; "
                f"allowed: {', '.join(_ALLOWED_LAYER_FIELDS)}"
            )
        layer_kind = raw.get("layer")
        if layer_kind not in INSTRUCTION_LAYER_KINDS:
            raise OverlayValidationError(
                f"instruction layer {layer_kind!r} not allowed; "
                f"must be one of {', '.join(INSTRUCTION_LAYER_KINDS)}"
            )
        ref = raw.get("ref")
        inline = raw.get("inline")
        if (ref is None) == (inline is None):
            raise OverlayValidationError(
                "each instruction layer must carry exactly one of 'ref' or 'inline'"
            )
        if inline is not None:
            if layer_kind != "tenant":
                raise OverlayValidationError(
                    "inline instruction text is only allowed at the 'tenant' layer; "
                    "the 'system' layer is owned by the platform registry"
                )
            if not isinstance(inline, str) or not inline.strip():
                raise OverlayValidationError("inline instruction text must be non-empty")
        if ref is not None:
            if not isinstance(ref, str) or not ref:
                raise OverlayValidationError("instruction-layer ref must be non-empty")
            resolved = registry_assets.resolve("prompt", ref, repo_root)
            if not resolved.present:
                raise OverlayValidationError(
                    f"instruction-layer ref {ref!r} does not resolve in the "
                    "platform prompt-module registry"
                )
        priority = raw.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise OverlayValidationError(
                f"instruction-layer priority must be an integer, got {priority!r}"
            )
        if priority in seen_priorities:
            raise OverlayValidationError(
                f"instruction-layer priority {priority} is duplicated; "
                "priorities must be unique for a deterministic order"
            )
        seen_priorities.add(priority)
        layer: dict[str, Any] = {"layer": layer_kind, "priority": priority}
        if ref is not None:
            layer["ref"] = ref
        if inline is not None:
            layer["inline"] = inline
        layers.append(layer)
    return sorted(layers, key=lambda layer: layer["priority"])


def apply_customization(
    store,
    tenant_id: str,
    overlay: Any,
    *,
    repo_root: Path | None = None,
) -> TenantCustomization:
    """Validate and upsert a tenant's customization overlay (idempotent replace).

    ``applied_at`` is preserved across replaces; ``updated_at`` moves on every
    successful apply.
    """
    if store.get_tenant(tenant_id) is None:
        raise UnknownTenantError(tenant_id)
    normalized = validate_overlay(overlay, repo_root=repo_root)
    now = utcnow_iso()
    existing = store.get_customization(tenant_id)
    customization = TenantCustomization(
        tenant_id=tenant_id,
        overlay=normalized,
        applied_at=existing.applied_at if existing is not None else now,
        updated_at=now,
    )
    store.put_customization(customization)
    return customization


def render_instruction_manifest(store, tenant_id: str) -> list[dict[str, Any]]:
    """Render the tenant's effective instruction layers, in priority order.

    Deterministic: layers sort by priority (ties impossible - apply enforces
    unique priorities), then by layer kind and ref for stable output. The
    platform seed-pack underlay (the tenant's installed profile prompts) stays
    authoritative for each agent's pinned system prompt; this manifest lists
    the tenant's customization layers on top of that underlay.
    """
    customization = store.get_customization(tenant_id)
    if customization is None:
        return []
    layers = customization.overlay.get("instructionLayers") or []
    manifest = []
    for layer in layers:
        entry: dict[str, Any] = {
            "order": layer["priority"],
            "layer": layer["layer"],
        }
        if "ref" in layer:
            entry["ref"] = layer["ref"]
        if "inline" in layer:
            entry["inline"] = layer["inline"]
        manifest.append(entry)
    return sorted(
        manifest,
        key=lambda entry: (entry["order"], entry["layer"], str(entry.get("ref", ""))),
    )
