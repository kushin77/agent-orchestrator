"""Tests for governance/knowledge/knowledge_controls.py (issue #887)."""

from __future__ import annotations

import pytest

import knowledge_controls as kc


def _require_vendor_cmr():
    if not kc.VENDOR_CMR.is_dir():
        pytest.skip("vendor/CMR submodule not checked out")


def test_measures_both_registered_repos():
    _require_vendor_cmr()
    gaps = kc.measure_vendor_compliance_gaps()
    repos = {g.repo for g in gaps}
    assert repos == {"shared-services", "googleworkspace"}
    issues = {g.repo: g.issue for g in gaps}
    assert issues == {"shared-services": 132, "googleworkspace": 133}


def test_findings_are_named_not_summarised():
    """Each finding line must be traceable to the source report, not a count-only stub."""
    _require_vendor_cmr()
    for gap in kc.measure_vendor_compliance_gaps():
        for finding in gap.findings:
            assert finding.startswith("hygiene-report.md:") or finding.startswith(
                "sweep/report.md:"
            ) or finding.startswith("unavailable:")


def test_unavailable_vendor_reports_gap_not_silently_closed(tmp_path, monkeypatch):
    """Negative control: if the CMR hub reports are unreachable, gaps are

    reported unavailable, never treated as closed (no-false-green).
    """
    monkeypatch.setattr(kc, "HYGIENE_REPORT", tmp_path / "hygiene-report.md")
    monkeypatch.setattr(kc, "SWEEP_REPORT", tmp_path / "report.md")
    gaps = kc.measure_vendor_compliance_gaps()
    assert all(g.open for g in gaps)
    for gap in gaps:
        assert any(f.startswith("unavailable:") for f in gap.findings)


def test_grep_repo_lines_does_not_false_match_longer_name():
    text = "| kushin77/shared-services-extra | some other row |\n"
    assert kc._grep_repo_lines(text, "shared-services") == []


def test_grep_repo_lines_matches_real_row_shape():
    text = "| `kushin77/shared-services` | spoke | false | pending-has-guardrails |\n"
    assert len(kc._grep_repo_lines(text, "shared-services")) == 1
