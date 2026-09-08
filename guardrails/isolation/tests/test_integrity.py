"""Data-integrity detector + runtime cadence-probe tests (AC1).

The dataset detector must flag orphaned buckets, fallback-tenant pile-up,
cross-tenant duplicates and denormalization violations in the leaky fixture
and stay silent on the clean one.  The runtime per-tenant probes must fail
closed on the real store and must *report* a leak on a deliberately leaky
store (the runner is negative-controlled, so it is not a formality).
"""

from __future__ import annotations

import copy
import json
import os

import pytest

from conftest import FIXTURES_DIR
from isolation.integrity import (RULE_DENORMALIZED, RULE_DUPLICATE,
                                 RULE_FALLBACK_PILEUP, RULE_ORPHANED,
                                 RULE_PROBE, run_cadence_probes,
                                 scan_dataset)
from isolation.model import TriState
from isolation.store import TenantScopedStore


def _load(name: str) -> dict:
    with open(os.path.join(FIXTURES_DIR, "data", name), "r",
              encoding="utf-8") as handle:
        return json.load(handle)


class TestCleanDataset:
    def test_clean_dataset_has_no_findings(self):
        report = scan_dataset(_load("clean_dataset.json"))
        assert not report.has_findings

    def test_clean_aggregate_is_ok(self):
        report = scan_dataset(_load("clean_dataset.json"))
        assert report.aggregate() is TriState.OK


class TestLeakyDatasetDetection:
    def test_detects_all_four_violation_classes(self):
        report = scan_dataset(_load("leaky_dataset.json"))
        rules = {f.rule_id for f in report.findings}
        assert rules == {RULE_ORPHANED, RULE_FALLBACK_PILEUP, RULE_DUPLICATE,
                         RULE_DENORMALIZED}

    def test_orphaned_bucket_detected(self):
        report = scan_dataset(_load("leaky_dataset.json"))
        orphaned = [f for f in report.findings if f.rule_id == RULE_ORPHANED]
        assert len(orphaned) == 1
        assert "ghost" in orphaned[0].message

    def test_fallback_pileup_both_rows(self):
        report = scan_dataset(_load("leaky_dataset.json"))
        pileup = [f for f in report.findings
                  if f.rule_id == RULE_FALLBACK_PILEUP]
        assert len(pileup) == 2

    def test_duplicate_detected(self):
        report = scan_dataset(_load("leaky_dataset.json"))
        dups = [f for f in report.findings if f.rule_id == RULE_DUPLICATE]
        assert len(dups) == 1
        assert "agent-1" in dups[0].message

    def test_denormalization_detected(self):
        report = scan_dataset(_load("leaky_dataset.json"))
        denorm = [f for f in report.findings if f.rule_id == RULE_DENORMALIZED]
        assert len(denorm) == 1
        assert "agent-dup" in denorm[0].scope

    def test_findings_carry_tenant_evidence(self):
        report = scan_dataset(_load("leaky_dataset.json"))
        for finding in report.findings:
            assert finding.message
        assert report.aggregate() is TriState.NOT_OK


# --------------------------------------------------------------------------- #
# runtime per-tenant probes
# --------------------------------------------------------------------------- #

def _probe_store():
    store = TenantScopedStore()
    store.put("acme", {"tenant_id": "acme", "record_id": "a1"})
    store.put("globex", {"tenant_id": "globex", "record_id": "g1"})
    return store


class TestCadenceProbesFailClosed:
    def test_all_probes_pass_on_real_store(self):
        store = _probe_store()
        findings, outcome = run_cadence_probes(store, ["acme", "globex"])
        assert outcome.probes_run > 0
        assert outcome.failed == 0
        assert outcome.passed == outcome.probes_run
        assert outcome.all_passed
        assert findings == []


class _LeakyStore(TenantScopedStore):
    """A store that leaks foreign rows on a cross-tenant read (test only)."""

    def get(self, tenant_id, rid):  # noqa: D102 - deliberately leaky
        for tenant in self._backend:
            bucket = self._backend.get(tenant, {})
            if rid in bucket:
                return dict(bucket[rid])
        return None


class TestProbeRunnerCatchesLeaks:
    """The probe runner must be able to fail (no-false-green negative control)."""

    def test_leaky_store_produces_probe_failures(self):
        store = _LeakyStore()
        store.put("acme", {"tenant_id": "acme", "record_id": "a1"})
        store.put("globex", {"tenant_id": "globex", "record_id": "g1"})
        findings, outcome = run_cadence_probes(store, ["acme", "globex"])
        assert outcome.failed > 0
        assert not outcome.all_passed
        assert any(f.rule_id == RULE_PROBE for f in findings)
        assert all(f.severity.value == "critical" for f in findings)
