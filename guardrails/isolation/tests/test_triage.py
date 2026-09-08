"""Security-finding triage gate tests (AC4).

Severity maps to auto-BLOCK (critical/high) or an SME-reviewer queue
(medium) or LOG (low); an unrecognized severity raises rather than defaulting
silently (fail closed).
"""

from __future__ import annotations

import pytest

from isolation.model import Finding, FindingCategory, Severity
from isolation.triage import (TriageAction, action_for_severity, gate_blocks,
                              partition, triage_finding)


def _finding(severity: Severity) -> Finding:
    return Finding(
        rule_id="R1-SCOPE-DROP-READ", category=FindingCategory.SCOPE_DROP_READ,
        severity=severity, message="x", target="t.py", scope="C.m",
    )


class TestSeverityMapping:
    @pytest.mark.parametrize("severity,expected", [
        (Severity.CRITICAL, TriageAction.BLOCK),
        (Severity.HIGH, TriageAction.BLOCK),
        (Severity.MEDIUM, TriageAction.SME_REVIEW),
        (Severity.LOW, TriageAction.LOG),
    ])
    def test_mapping(self, severity, expected):
        assert action_for_severity(severity) is expected
        assert triage_finding(_finding(severity)) is expected

    def test_unknown_severity_raises(self):
        with pytest.raises(ValueError):
            action_for_severity("catastrophic")  # type: ignore[arg-type]

    def test_block_never_auto_deploys(self):
        assert TriageAction.BLOCK.blocks
        assert not TriageAction.SME_REVIEW.blocks
        assert not TriageAction.LOG.blocks
        assert TriageAction.SME_REVIEW.requires_reviewer


class TestPartitionAndGate:
    def test_partition_buckets(self):
        findings = [_finding(Severity.CRITICAL), _finding(Severity.HIGH),
                    _finding(Severity.MEDIUM), _finding(Severity.LOW)]
        buckets = partition(findings)
        assert len(buckets[TriageAction.BLOCK]) == 2
        assert len(buckets[TriageAction.SME_REVIEW]) == 1
        assert len(buckets[TriageAction.LOG]) == 1

    def test_gate_blocks_on_auto_block_finding(self):
        assert gate_blocks([_finding(Severity.HIGH)])
        assert gate_blocks([_finding(Severity.MEDIUM), _finding(Severity.HIGH)])
        assert not gate_blocks([_finding(Severity.MEDIUM)])
        assert not gate_blocks([_finding(Severity.LOW)])
        assert not gate_blocks([])
