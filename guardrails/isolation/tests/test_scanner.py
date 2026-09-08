"""Scanner tests: positive and negative, both directions.

The detector must catch every planted cross-tenant bug in the leaky/shared
fixtures (positive) and must clear the safe fixture with no findings and an
``OK`` verdict (negative) — a guard that cannot fail is a formality.
"""

from __future__ import annotations

import os

import pytest

from conftest import FIXTURES_DIR
from isolation.model import TriState
from isolation.scanner import (RULE_CROSS_TENANT_FALLBACK,
                               RULE_SCOPE_DROP_READ, RULE_SCOPE_DROP_WRITE,
                               RULE_SHARED_MUTABLE_STATE, scan_paths)


def _code(name: str) -> str:
    return os.path.join(FIXTURES_DIR, "code", name)


def _scan(name: str):
    return scan_paths([_code(name)], base=os.path.join(FIXTURES_DIR, "code"))


# --------------------------------------------------------------------------- #
# positive: the detector catches planted cross-tenant bugs
# --------------------------------------------------------------------------- #

class TestLeakyStoreIsCaught:
    """The explicit requirement: a planted cross-tenant bug is detected."""

    def test_reports_a_finding(self):
        report = _scan("tenant_leaky_store.py")
        assert report.has_findings
        assert report.aggregate() is TriState.NOT_OK

    def test_every_planted_rule_fires(self):
        report = _scan("tenant_leaky_store.py")
        rules = sorted({f.rule_id for f in report.findings})
        assert rules == [RULE_SCOPE_DROP_READ, RULE_SCOPE_DROP_WRITE,
                         RULE_CROSS_TENANT_FALLBACK]

    def test_findings_carry_evidence(self):
        report = _scan("tenant_leaky_store.py")
        for finding in report.findings:
            assert finding.line > 0
            assert finding.evidence, "every finding carries reproduction evidence"
            assert finding.target == "tenant_leaky_store.py"
            assert finding.fix

    def test_scope_drop_write_is_critical(self):
        report = _scan("tenant_leaky_store.py")
        critical = [f for f in report.findings
                    if f.rule_id == RULE_SCOPE_DROP_WRITE]
        assert len(critical) == 1
        assert critical[0].severity.value == "critical"

    def test_leaky_store_verdict_is_not_ok(self):
        report = _scan("tenant_leaky_store.py")
        leaky = [v for v in report.verdicts
                 if v.owner == "LeakyStore._records"]
        assert len(leaky) == 1
        assert leaky[0].verdict is TriState.NOT_OK


class TestSharedGlobalIsCaught:
    def test_reports_shared_mutable_state(self):
        report = _scan("tenant_shared_global.py")
        rules = {f.rule_id for f in report.findings}
        assert RULE_SHARED_MUTABLE_STATE in rules
        assert report.aggregate() is TriState.NOT_OK

    def test_shared_global_verdict(self):
        report = _scan("tenant_shared_global.py")
        owners = {v.owner for v in report.verdicts
                  if v.verdict is TriState.NOT_OK}
        assert "<module>._AGENT_CACHE" in owners


# --------------------------------------------------------------------------- #
# negative: the safe store clears with no findings and an OK verdict
# --------------------------------------------------------------------------- #

class TestSafeStoreIsClean:
    def test_no_findings_on_safe_store(self):
        report = _scan("tenant_safe_store.py")
        assert not report.has_findings

    def test_safe_store_verdict_is_ok(self):
        report = _scan("tenant_safe_store.py")
        safe = [v for v in report.verdicts
                if v.owner == "TenantSafeStore._records"]
        assert len(safe) == 1
        assert safe[0].verdict is TriState.OK
        assert report.aggregate() is TriState.OK


# --------------------------------------------------------------------------- #
# robustness
# --------------------------------------------------------------------------- #

class TestScannerRobustness:
    def test_scanner_survives_non_python_input(self, tmp_path):
        (tmp_path / "not_python.txt").write_text("def broken(:\n")
        # scan_paths only walks .py; a non-.py file is ignored silently.
        report = scan_paths([str(tmp_path)])
        assert isinstance(report, object)

    def test_deterministic_order(self):
        first = _scan("tenant_leaky_store.py")
        second = _scan("tenant_leaky_store.py")
        assert [(f.rule_id, f.line, f.target) for f in first.findings] == \
               [(f.rule_id, f.line, f.target) for f in second.findings]
