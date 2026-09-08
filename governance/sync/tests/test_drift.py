"""Drift detection tests (issue #44, acceptance criterion 2).

The honest tri-state: local content matches the pinned canonical source is
CLEAN; a changed/missing consumer asset is DRIFT; an unavailable canonical
source is CANNOT_ASSESS and is never reported clean.
"""

from __future__ import annotations

import os

import pytest

import drift
import synchelpers
from drift import DriftState, check_asset, check_manifest


def _local_path(consumers, consumer_id, asset_id):
    local_root, _manifest = consumers[consumer_id]
    rel = synchelpers.CANON[asset_id][0]
    return os.path.join(local_root, rel)


class TestClean:
    def test_clean_ecosystem_reports_all_clean(self, ecosystem):
        canonical_root, consumers = ecosystem
        for consumer_id, (local_root, manifest) in consumers.items():
            if consumer_id == "rogue":
                # rogue vendors an asset outside the canonical mirror (the
                # blast-radius negative fixture); it is not expected clean.
                continue
            report = check_manifest(manifest, local_root,
                                    canonical_root=canonical_root)
            assert report["verdict"] is DriftState.CLEAN
            assert report["summary"]["cannot_assess"] == 0
            assert all(r["state"] is DriftState.CLEAN for r in report["assets"])

    def test_clean_without_canonical_mirror_uses_content_pin(self, ecosystem):
        # No canonical_root: the manifest's content_sha256 is the target.
        _canonical, consumers = ecosystem
        for consumer_id, (local_root, manifest) in consumers.items():
            if consumer_id == "rogue":
                continue
            report = check_manifest(manifest, local_root)
            assert report["verdict"] is DriftState.CLEAN


class TestDrift:
    def test_changed_consumer_asset_is_drift(self, ecosystem):
        """Negative test: a local edit to a vendored asset is DRIFT."""
        _canonical, consumers = ecosystem
        local_root, manifest = consumers["acme"]
        # tamper the local copy of a shared asset
        target = os.path.join(local_root,
                              synchelpers.CANON["core-guardrails"][0])
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("local tamper\n")
        report = check_manifest(manifest, local_root,
                                canonical_root=_canonical)
        row = next(r for r in report["assets"]
                   if r["asset"] == "core-guardrails")
        assert row["state"] is DriftState.DRIFT
        assert report["verdict"] is DriftState.DRIFT
        # sibling untouched asset stays clean (not a blanket failure)
        clean = next(r for r in report["assets"]
                     if r["asset"] == "design-tokens")
        assert clean["state"] is DriftState.CLEAN

    def test_missing_local_asset_is_drift(self, ecosystem):
        _canonical, consumers = ecosystem
        local_root, manifest = consumers["globex"]
        os.remove(_local_path(consumers, "globex", "core-guardrails"))
        row = check_asset(manifest, "core-guardrails", local_root,
                          canonical_root=_canonical)
        assert row["state"] is DriftState.DRIFT

    def test_version_drift_via_desired_map(self, ecosystem):
        _canonical, consumers = ecosystem
        local_root, manifest = consumers["acme"]
        desired = {"core-guardrails": "9.9.9"}
        report = check_manifest(manifest, local_root,
                                canonical_root=_canonical, desired=desired)
        row = next(r for r in report["assets"]
                   if r["asset"] == "core-guardrails")
        assert row["state"] is DriftState.DRIFT
        assert "desired version" in row["reason"]


class TestCannotAssess:
    def test_unavailable_source_is_cannot_assess_never_clean(self, ecosystem,
                                                             tmp_path):
        """Negative test: canonical file deleted -> CANNOT_ASSESS, not clean."""
        _canonical, consumers = ecosystem
        local_root, manifest = consumers["zeta"]
        # A canonical mirror whose source file is gone (source unavailable).
        empty_mirror = tmp_path / "empty-mirror"
        empty_mirror.mkdir()
        row = check_asset(manifest, "core-guardrails", local_root,
                          canonical_root=str(empty_mirror))
        assert row["state"] is DriftState.CANNOT_ASSESS
        assert row["state"].is_clean is False

    def test_missing_content_pin_is_cannot_assess(self, ecosystem):
        # No canonical mirror AND no content_sha256 -> cannot compare offline.
        _canonical, consumers = ecosystem
        local_root, manifest = consumers["acme"]
        doc = manifest.to_dict()
        doc["assets"]["design-tokens"].pop("content_sha256", None)
        from provenance import ProvenanceManifest
        stripped = ProvenanceManifest.from_dict(doc)
        row = check_asset(stripped, "design-tokens", local_root)
        assert row["state"] is DriftState.CANNOT_ASSESS
        assert "no content pin" in row["reason"]

    def test_unknown_asset_is_cannot_assess(self, ecosystem):
        _canonical, consumers = ecosystem
        local_root, manifest = consumers["acme"]
        row = check_asset(manifest, "no-such-asset", local_root)
        assert row["state"] is DriftState.CANNOT_ASSESS

    def test_aggregate_never_clean_with_a_cannot_assess(self):
        verdict = drift.aggregate_states(
            [DriftState.CLEAN, DriftState.CANNOT_ASSESS])
        assert verdict is DriftState.CANNOT_ASSESS
        assert verdict.is_clean is False

    def test_aggregate_drift_wins_over_cannot_assess(self):
        verdict = drift.aggregate_states(
            [DriftState.CLEAN, DriftState.CANNOT_ASSESS, DriftState.DRIFT])
        assert verdict is DriftState.DRIFT


class TestPathSafety:
    def test_local_path_traversal_refused(self, ecosystem, tmp_path):
        _canonical, consumers = ecosystem
        _local_root, manifest = consumers["acme"]
        doc = manifest.to_dict()
        doc["assets"]["design-tokens"]["local_path"] = "../../etc/passwd"
        from provenance import ProvenanceManifest
        evil = ProvenanceManifest.from_dict(doc)
        with pytest.raises(ValueError):
            check_manifest(evil, str(tmp_path))
