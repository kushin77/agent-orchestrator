#!/usr/bin/env python3
"""Drift detection for vendored/mirrored assets (issue #44, criterion 2).

Compares each asset a consumer vendors against its recorded canonical source
and reports an honest tri-state:

    CLEAN           local content matches the pinned canonical source
    DRIFT           local content differs (tampered/stale), or a vendored
                    asset is missing, or the pinned version differs from the
                    desired version
    CANNOT_ASSESS   the canonical source cannot be compared — no content pin
                    recorded, no canonical mirror to hash, or the manifest
                    entry is unresolvable. Never clean.

Canonical content is resolved in two ways (both offline):

1. A ``canonical_root`` mirror of the source repo — the authoritative live
   compare: the canonical file at ``source_path`` is hashed directly. When the
   canonical file is unavailable (source deleted / never mirrored) the asset
   is CANNOT_ASSESS, never clean.
2. Otherwise the manifest's recorded ``content_sha256`` (the digest of the
   canonical content at the pin) is the offline comparison target. When no
   content pin is recorded the asset is CANNOT_ASSESS.

Version drift is assessed when a ``desired`` map (asset_id -> version) is
supplied, mirroring the reconcile target of ``sync_plan.py``.

Adapted (not copied) from `kushin77/CMR` `sync/drift-check.sh` (report
vocabulary: summary + findings, dry-run report-only posture) and
`kushin77/shared-frontend` `docs/PROVENANCE.md` (MATCH/DRIFT/MISSING verdicts
where MISSING-style states are never clean).
"""

from __future__ import annotations

import os

from model import DriftState, aggregate_states
from provenance import content_sha256_of

HEX64_OK = set("0123456789abcdef")


def _looks_hex64(text):
    return (isinstance(text, str) and len(text) == 64
            and all(c in HEX64_OK for c in text.lower()))


def _safe_join(root, rel):
    """Join ``root`` + ``rel`` refusing path traversal outside ``root``."""
    root_abs = os.path.abspath(root)
    joined = os.path.abspath(os.path.join(root_abs, rel))
    if os.path.commonpath([root_abs, joined]) != root_abs:
        raise ValueError("path escapes root: %r" % rel)
    return joined


def _canonical_digest(entry, canonical_root, asset_id):
    """Return ``(digest, reason)`` for the canonical source of an asset.

    ``digest`` is None when the canonical source cannot be compared.
    """
    if canonical_root:
        src_path = entry.get("source_path")
        if not src_path:
            return None, "asset '%s' records no source_path" % asset_id
        canonical_file = _safe_join(canonical_root, src_path)
        if not os.path.isfile(canonical_file):
            return None, ("canonical source unavailable for asset '%s' "
                          "(mirror file missing: %s)" % (asset_id, src_path))
        return content_sha256_of(canonical_file), None
    recorded = entry.get("content_sha256")
    if _looks_hex64(recorded):
        return recorded.lower(), None
    return None, ("canonical source unavailable for asset '%s' offline (no "
                  "content pin recorded and no canonical mirror)"
                  % asset_id)


def check_asset(manifest, asset_id, local_root, canonical_root=None,
                desired=None):
    """Assess one asset; return a dict with the honest ``DriftState``."""
    entry = manifest.asset(asset_id)
    if entry is None:
        return {"asset": asset_id, "state": DriftState.CANNOT_ASSESS,
                "reason": ("asset '%s' is not recorded in the provenance "
                           "manifest (unresolvable)" % asset_id),
                "version": None}
    digest, digest_reason = _canonical_digest(entry, canonical_root, asset_id)
    if digest is None:
        return {"asset": asset_id, "state": DriftState.CANNOT_ASSESS,
                "reason": digest_reason, "version": entry.get("version")}
    local_rel = entry.get("local_path")
    local_file = _safe_join(local_root, local_rel) if local_rel else None
    if local_file is None or not os.path.isfile(local_file):
        return {"asset": asset_id, "state": DriftState.DRIFT,
                "reason": ("vendored asset '%s' is missing at local_path %s"
                           % (asset_id, local_rel)),
                "version": entry.get("version")}
    if content_sha256_of(local_file) != digest:
        return {"asset": asset_id, "state": DriftState.DRIFT,
                "reason": ("local content for asset '%s' does not match the "
                           "pinned canonical source (sha256 mismatch)"
                           % asset_id),
                "version": entry.get("version")}
    desired_version = (desired or {}).get(asset_id)
    if desired_version and str(entry.get("version")) != str(desired_version):
        return {"asset": asset_id, "state": DriftState.DRIFT,
                "reason": ("asset '%s' is at version %s but the desired "
                           "version is %s" % (asset_id, entry.get("version"),
                                              desired_version)),
                "version": entry.get("version")}
    return {"asset": asset_id, "state": DriftState.CLEAN,
            "reason": "local content matches the pinned canonical source",
            "version": entry.get("version")}


def check_manifest(manifest, local_root, canonical_root=None, desired=None):
    """Assess every asset in a manifest; return an aggregate drift report.

    The aggregate verdict never reports clean while any asset is
    CANNOT_ASSESS or DRIFT (``model.aggregate_states``).
    """
    rows = []
    for asset_id in sorted(manifest.assets):
        rows.append(check_asset(manifest, asset_id, local_root,
                                canonical_root=canonical_root,
                                desired=desired))
    states = [r["state"] for r in rows]
    return {
        "manifest": manifest.consumer_id(),
        "assets": rows,
        "summary": {
            "clean": sum(1 for s in states if s is DriftState.CLEAN),
            "drift": sum(1 for s in states if s is DriftState.DRIFT),
            "cannot_assess": sum(1 for s in states
                                 if s is DriftState.CANNOT_ASSESS),
        },
        "verdict": aggregate_states(states),
    }


# Re-exported for parity with the sibling suites.
__all__ = ["DriftState", "check_asset", "check_manifest"]
