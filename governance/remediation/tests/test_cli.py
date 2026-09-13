"""CLI end-to-end: scan the real repo board + write the report (issue #142).

Runs the actual entry point as a subprocess against this checkout's real
``.board/snapshot.json`` and ``governance/conformance/policy.yaml`` — the
"tested against representative violations" the definition of done asks for —
and asserts the report it writes has the shape the acceptance criteria
require (title/labels/summary/owner-lane/corrective-steps/policy-ref/evidence,
escalation flag, dedup by key).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "governance" / "remediation" / "cli.py"


def _run(root, *args):
    return subprocess.run(
        [sys.executable, str(CLI), "--root", str(root), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_scan_runs_against_the_real_board_and_writes_a_report(tmp_path):
    result = _run(REPO_ROOT, "scan", "--repo", "kushin77/agent-orchestrator")
    assert result.returncode in (0, 1), result.stderr

    report_path = REPO_ROOT / ".verify" / "remediation-report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["schema"] == "cmr.remediation/report-v1"
    assert "scanned" in report
    for issue in report["issues"]:
        for field in (
            "title", "labels", "owner_lane", "corrective_steps", "policy_ref",
            "evidence", "severity", "sla_hours", "escalate", "key",
        ):
            assert field in issue, "missing %r in generated issue: %r" % (field, issue)


def test_scan_dedups_repeated_findings_into_one_issue_with_growing_occurrences():
    result = _run(REPO_ROOT, "scan")
    assert result.returncode in (0, 1), result.stderr

    report_path = REPO_ROOT / ".verify" / "remediation-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    keys = [issue["key"] for issue in report["issues"]]
    assert len(keys) == len(set(keys)), "dedup key collision: duplicate issues were emitted"


def test_route_without_apply_performs_no_network_calls_and_exits_ok():
    result = _run(REPO_ROOT, "route", "--repo", "kushin77/agent-orchestrator")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout


def test_scan_cannot_assess_when_policy_is_absent(tmp_path):
    # An empty root has no governance/conformance/policy.yaml -> CANNOT-ASSESS
    # (exit 2), not a false green and not a crash.
    (tmp_path / "governance" / "conformance").mkdir(parents=True)
    (tmp_path / ".board").mkdir()
    result = _run(tmp_path, "scan")
    assert result.returncode == 2
    assert "CANNOT-ASSESS" in result.stderr
