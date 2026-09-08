"""Blast-radius engine tests (issue #44, acceptance criterion 3).

A proposed change to a shared asset reports exactly the consumers/dependents
that manifest + dependency graph say are affected — a widely-consumed asset
reports many consumers, a leaf reports few, and a consumer outside the
dependency graph is never reported (negative test).
"""

from __future__ import annotations

import pytest

import synchelpers
from blast_radius import BlastRadiusEngine, BlastRadiusError


def _engine(consumers, catalog=None):
    manifests = [manifest for _local, manifest in consumers.values()]
    return BlastRadiusEngine(manifests, catalog=catalog)


class TestWideSharedAsset:
    def test_core_change_reports_all_direct_and_transitive_consumers(
            self, ecosystem):
        _canonical, consumers = ecosystem
        engine = _engine(consumers, catalog=synchelpers.CATALOG)
        report = engine.compute("core-guardrails")
        # closure: core-guardrails + its transitive dependents
        assert sorted(report["closure"]) == sorted(
            ["core-guardrails", "pack-sync-engine", "portal-shell"])
        affected = {c["consumer"] for c in report["consumers"]}
        # acme/globex vendor it directly; nexus via portal-shell;
        # zeta vendors core-guardrails directly.
        assert affected == {"acme", "globex", "nexus", "zeta"}
        assert report["summary"]["consumers_affected"] == 4
        assert "rogue" not in affected

    def test_zeta_direct_consumer_is_reported(self, ecosystem):
        _canonical, consumers = ecosystem
        engine = _engine(consumers, catalog=synchelpers.CATALOG)
        assert engine.consumers_affected_by("core-guardrails") == \
            ["acme", "globex", "nexus", "zeta"]

    def test_transitive_dependents_enumerated(self, ecosystem):
        _canonical, consumers = ecosystem
        engine = _engine(consumers, catalog=synchelpers.CATALOG)
        report = engine.compute("core-guardrails")
        # pack-sync-engine and portal-shell both depend (transitively) on
        # core-guardrails.
        assert sorted(report["dependents"]) == ["pack-sync-engine",
                                                "portal-shell"]
        depths = {row["asset"]: row["depth"] for row in
                  report["closure_order"]}
        assert depths["core-guardrails"] == 0
        assert depths["pack-sync-engine"] == 1
        assert depths["portal-shell"] == 1


class TestLeaf:
    def test_leaf_change_reports_few_consumers(self, ecosystem):
        _canonical, consumers = ecosystem
        engine = _engine(consumers, catalog=synchelpers.CATALOG)
        report = engine.compute("design-tokens")
        assert report["closure"] == ["design-tokens"]
        assert report["dependents"] == []
        affected = {c["consumer"] for c in report["consumers"]}
        assert affected == {"acme"}  # only acme vendors design-tokens
        assert report["summary"]["consumers_affected"] == 1
        assert report["summary"]["direct_consumers"] == 1


class TestNegative:
    def test_unrelated_consumer_not_reported(self, ecosystem):
        """Negative test: rogue (vendors only unrelated-asset) is NOT affected."""
        _canonical, consumers = ecosystem
        engine = _engine(consumers, catalog=synchelpers.CATALOG)
        report = engine.compute("core-guardrails")
        consumer_ids = [c["consumer"] for c in report["consumers"]]
        assert "rogue" not in consumer_ids
        # rogue's own asset is not in the closure of any shared change
        assert "unrelated-asset" not in report["closure"]

    def test_unknown_asset_raises_not_empty_report(self, ecosystem):
        _canonical, consumers = ecosystem
        engine = _engine(consumers, catalog=synchelpers.CATALOG)
        with pytest.raises(BlastRadiusError):
            engine.compute("never-heard-of-it")


class TestNoCatalog:
    def test_without_catalog_closure_is_direct_only(self, ecosystem):
        _canonical, consumers = ecosystem
        engine = _engine(consumers)  # no dependency catalog
        report = engine.compute("core-guardrails")
        assert report["closure"] == ["core-guardrails"]
        assert report["dependents"] == []
        # direct vendoring consumers only (all four vendor it directly)
        assert report["summary"]["consumers_affected"] == 4

    def test_shared_assets_index(self, ecosystem):
        _canonical, consumers = ecosystem
        engine = _engine(consumers, catalog=synchelpers.CATALOG)
        assert "core-guardrails" in engine.shared_assets()
        assert "design-tokens" in engine.shared_assets()

    def test_duplicate_consumer_refused(self, ecosystem):
        _canonical, consumers = ecosystem
        manifests = [manifest for _l, manifest in consumers.values()]
        with pytest.raises(BlastRadiusError):
            BlastRadiusEngine(manifests + [manifests[0]])
