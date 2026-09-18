"""Tests for governance/controls/check_spine_coverage.py (issue #890).

Covers: every parsed spine rule id has a row, a row naming a missing gate
file is refused BY NAME (the negative control), a suite gate not declared in
scripts/pytest-suites.txt is refused, and the real map + real repository
pass end to end.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import check_spine_coverage as csc

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_real_map_passes_against_real_repo() -> None:
    spine_ids = csc.parse_rule_ids(
        REPO_ROOT / "AGENTS.md", REPO_ROOT / "docs" / "GOLDEN-RULES.md"
    )
    map_path = REPO_ROOT / "governance" / "controls" / "spine-coverage.yaml"
    code, findings = csc.check(REPO_ROOT, map_path, spine_ids)
    assert code == 0, f"expected OK, got findings: {findings}"


def test_self_test_negative_control_passes() -> None:
    # Exercises the same mutant-refusal path the gate script runs.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "controls" / "check_spine_coverage.py"), "--self-test"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF-TEST: OK" in result.stdout


def test_missing_rule_row_is_reported(tmp_path: Path) -> None:
    map_path = tmp_path / "spine-coverage.yaml"
    map_path.write_text(
        textwrap.dedent(
            """
            rules:
              AO-GR-1:
                status: ENFORCED
                covered: true
                gate: scripts/check-pr-contract.sh
            """
        )
    )
    code, findings = csc.check(REPO_ROOT, map_path, {"AO-GR-1", "AO-GR-2"})
    assert code == 1
    assert any("AO-GR-2" in f and "rule-missing-row" in f for f in findings)


def test_covered_row_with_nonexistent_gate_is_refused_by_name(tmp_path: Path) -> None:
    map_path = tmp_path / "spine-coverage.yaml"
    map_path.write_text(
        textwrap.dedent(
            """
            rules:
              AO-GR-1:
                status: ENFORCED
                covered: true
                gate: scripts/check-totally-fake-control.sh
            """
        )
    )
    code, findings = csc.check(REPO_ROOT, map_path, {"AO-GR-1"})
    assert code == 1
    assert any(
        "AO-GR-1" in f and "check-totally-fake-control.sh" in f and "missing-gate-file" in f
        for f in findings
    )


def test_undeclared_suite_is_refused(tmp_path: Path) -> None:
    map_path = tmp_path / "spine-coverage.yaml"
    map_path.write_text(
        textwrap.dedent(
            """
            rules:
              AO-GR-1:
                status: ENFORCED
                covered: true
                gate: "suite:governance/totally-undeclared-suite"
            """
        )
    )
    code, findings = csc.check(REPO_ROOT, map_path, {"AO-GR-1"})
    assert code == 1
    assert any("undeclared-suite" in f for f in findings)


def test_gap_claiming_covered_is_refused(tmp_path: Path) -> None:
    map_path = tmp_path / "spine-coverage.yaml"
    map_path.write_text(
        textwrap.dedent(
            """
            rules:
              AO-GR-1:
                status: GAP
                covered: true
                gate: scripts/check-pr-contract.sh
            """
        )
    )
    code, findings = csc.check(REPO_ROOT, map_path, {"AO-GR-1"})
    assert code == 1
    assert any("gap-claims-covered" in f for f in findings)
