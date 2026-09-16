"""Shared legacy-model-id alias table (issue #971, #895 follow-up).

Single source of truth for mapping a retired Claude model id to its current
replacement. ``gateway/providers/config.py`` owns the *consumer-facing* fail-
closed model check (``ProviderConfig.normalize_model``) but the alias table
itself is defined here so both the gateway (routing/fail-closed checks) and
telemetry/metering (rate-card price lookup) resolve a legacy id through the
exact same map — one alias table, never two that can drift apart.

``gateway/providers/config.py`` re-exports ``LEGACY_MODEL_ALIASES`` /
``normalize_model`` from this module rather than defining its own copy.
"""

from __future__ import annotations

from typing import Mapping

#: DEPRECATED - pre-parity (issue #894) Claude model ids that other pillars
#: (``gateway/health/health.yaml``/``fallback.py``/``cli.py``,
#: ``gateway/finops/tiers.yaml``) may still reference, plus any caller that
#: still submits an old id. Rather than fail closed on the old id, it is
#: normalized to its current replacement before any fail-closed model check
#: or rate-card lookup, so an old id still resolves to a real, currently
#: priced/served model.
LEGACY_MODEL_ALIASES: Mapping[str, str] = {
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-sonnet-4-5": "claude-sonnet-5",
    "claude-opus-4-5": "claude-opus-5",
    "claude-opus-4-1": "claude-opus-5",
}


def normalize_model(model: str) -> str:
    """Map a deprecated ``LEGACY_MODEL_ALIASES`` id to its current
    replacement; a current (or unknown) id passes through unchanged."""
    return LEGACY_MODEL_ALIASES.get(model, model)
