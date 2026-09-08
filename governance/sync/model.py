#!/usr/bin/env python3
"""Shared vocabulary for the governance/sync engine (issue #44).

The sync/drift subtree is one lane with one contract, so its modules share a
closed vocabulary instead of redefining each other's enums. ``DriftState`` is
the honest tri-state every drift assessment returns; the manifest/catalog
schema identities and closed vocabularies (asset kinds, pin kinds, reconcile
ops) live here so ``provenance``, ``drift``, ``blast_radius`` and
``sync_plan`` stay mutually consistent.

The tri-state discipline is consumed from the repo's honesty stack
(guardrails/honesty, issue #28): CANNOT-ASSESS is never a pass and never a
clean verdict — an assessment that cannot compare against the recorded
canonical source reports itself honestly instead of pretending to be clean.
"""

from __future__ import annotations

import enum


class DriftState(str, enum.Enum):
    """Honest tri-state for a drift assessment.

    - CLEAN: local content matches the recorded canonical source (content and,
      where a desired version is supplied, version).
    - DRIFT: local content/version no longer matches the pinned source, or an
      expected vendored asset is missing.
    - CANNOT_ASSESS: the canonical source cannot be compared (no content pin,
      no canonical mirror, or an unresolvable manifest entry). Never clean.
    """

    CLEAN = "clean"
    DRIFT = "drift"
    CANNOT_ASSESS = "cannot-assess"

    @property
    def is_clean(self) -> bool:
        """True only for CLEAN — CANNOT_ASSESS is never clean."""
        return self is DriftState.CLEAN


def aggregate_states(states):
    """Aggregate a sequence of ``DriftState`` into one honest verdict.

    Mirrors the honesty aggregate (issue #28): any DRIFT fails the verdict,
    any CANNOT_ASSESS keeps the verdict from being clean, and only an
    all-CLEAN set reports CLEAN.
    """
    states = list(states)
    if any(s is DriftState.DRIFT for s in states):
        return DriftState.DRIFT
    if any(s is DriftState.CANNOT_ASSESS for s in states):
        return DriftState.CANNOT_ASSESS
    if all(s is DriftState.CLEAN for s in states):
        return DriftState.CLEAN
    # An empty assessment set cannot claim clean (no-false-green).
    return DriftState.CANNOT_ASSESS


# --- manifest / catalog identity --------------------------------------------
PROVENANCE_MANIFEST_SCHEMA = "ao.sync/provenance-manifest-v1"
DEPENDENCY_CATALOG_SCHEMA = "ao.sync/dependency-catalog-v1"
SYNC_TARGET_SCHEMA = "ao.sync/sync-target-v1"

# --- closed vocabularies ----------------------------------------------------
ASSET_KINDS = ("vendored", "mirrored", "pack", "instruction")
SHA_KINDS = ("commit", "content")
RECONCILE_OPS = ("install", "upgrade", "rollback", "noop")

# --- manifest field identities (shared, never redefined) --------------------
ASSET_REQUIRED_FIELDS = (
    "source_repo", "source_path", "version", "sha", "local_path",
)
