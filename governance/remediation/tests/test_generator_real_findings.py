"""Generator driven by *real* governance.conformance findings, not fakes.

Imports governance/conformance's checker/model in isolation (the same
sys.modules-eviction trick cli.py uses at runtime) so this suite proves the
adapter against the actual ``Finding`` objects the conformance checker raises
— "tested against real repo code paths" (issue #142) — rather than only the
duck-typed ``FakeFinding`` stand-in the other generator tests use.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REMEDIATION_DIR = os.path.dirname(_TESTS_DIR)
_CONFORMANCE_DIR = os.path.join(os.path.dirname(_REMEDIATION_DIR), "conformance")

_POLICY_YAML = """\
schema: cmr.conformance/policy-v1
ladder:
  - template
  - class
  - pattern
  - enterprise
  - faang
  - elite
mandates:
  iac:
    infra_paths:
      - infra/
required:
  - class
  - type
  - priority
  - area
expectations:
  enterprise:
    - gdc
  elite:
    - gdc
    - pillar
prefixed:
  - class
  - type
  - priority
  - area
  - pillar
  - gdc
"""


def _row(number, *, labels=(), milestone="M24 - Real Findings", state="OPEN"):
    return {
        "number": number,
        "title": "sample %d" % number,
        "state": state,
        "milestone": milestone,
        "labels": list(labels),
        "parent": None,
        "blocked_by": [],
    }


def _load_real_findings():
    """Run the actual conformance checker against a tiny in-memory board and
    return its real ``Finding`` objects, then evict conformance's ``model``
    module from ``sys.modules`` so the remediation package's own ``model``
    resolves fresh for the rest of this process (the two packages each own a
    bare, incompatible ``model.py`` — see cli.py's import section)."""
    # Evict any remediation-side `model`/`checker` already cached in this test
    # session (other test modules in this same process import them) before
    # conformance's checker does its own `from model import ...`.
    sys.modules.pop("model", None)
    sys.modules.pop("checker", None)
    sys.path.insert(0, _CONFORMANCE_DIR)
    try:
        import checker as conformance_checker

        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.yaml"
            policy_path.write_text(_POLICY_YAML, encoding="utf-8")
            policy = conformance_checker.load_policy(policy_path)

        rows = [
            _row(1, labels=()),  # CODE_CLASS_MISSING
            _row(
                2,
                labels=(
                    "class:elite", "type:governance", "priority:P0", "area:board",
                    "gdc:enterprise",
                ),
            ),  # declares elite, missing the `pillar` expectation -> deviation
        ]
        report = conformance_checker.check_board(rows, policy, include_unmilestoned=True)
        findings = list(report.findings)
    finally:
        sys.path.remove(_CONFORMANCE_DIR)
        sys.modules.pop("checker", None)
        sys.modules.pop("model", None)
    return findings


@pytest.fixture
def real_findings():
    findings = _load_real_findings()
    assert findings, "expected the sample board to raise at least one real finding"
    return findings


def test_generate_accepts_real_conformance_findings(real_findings):
    from generator import generate

    issues = generate(real_findings, repo="kushin77/agent-orchestrator")
    assert issues
    for generated in issues:
        assert generated.title
        assert generated.owner_lane
        assert generated.policy_ref
        assert generated.corrective_steps
        assert generated.evidence


def test_real_class_missing_finding_routes_to_governance_lane(real_findings):
    from generator import build_issue

    class_missing = next(f for f in real_findings if f.code == "class-missing")
    issue = build_issue(class_missing)
    assert issue.owner_lane == "governance"
    assert issue.severity  # mapped from the finding's real severity
    assert "issue #1" in issue.evidence[0]
