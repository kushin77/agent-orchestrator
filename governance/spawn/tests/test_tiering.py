"""Tests for governance/spawn/tiering.py (issue #1274)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.spawn import tiering

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_TIERS = REPO_ROOT / "gateway" / "finops" / "tiers.yaml"


def test_real_tree_fleet_allowed_at_default_tier():
    verdict = tiering.judge("fleet", "code-author", "L0", tiers_path=REAL_TIERS)
    assert verdict.allowed, verdict


def test_real_tree_claude_subagent_allowed_at_default_tier():
    verdict = tiering.judge("claude-subagent", "code-author", "L0", tiers_path=REAL_TIERS)
    assert verdict.allowed, verdict


def test_real_tree_hermes_persona_allowed_within_window():
    verdict = tiering.judge("hermes-persona", "code-review", "L1", tiers_path=REAL_TIERS)
    assert verdict.allowed, verdict


def test_claude_subagent_refused_above_max_tier():
    verdict = tiering.judge("claude-subagent", "code-author", "L2", tiers_path=REAL_TIERS)
    assert not verdict.allowed
    assert verdict.finding == tiering.FINDING_ROLE_NOT_ALLOWED


def test_hermes_persona_refused_above_max_tier():
    verdict = tiering.judge("hermes-persona", "memory-ops", "L1", tiers_path=REAL_TIERS)
    assert not verdict.allowed
    assert verdict.finding == tiering.FINDING_ROLE_NOT_ALLOWED


def test_fleet_refused_below_security_guardrail():
    verdict = tiering.judge("fleet", "security-review", "L0", tiers_path=REAL_TIERS)
    assert not verdict.allowed
    assert verdict.finding == tiering.FINDING_ROLE_NOT_ALLOWED


def test_unknown_role_refused():
    verdict = tiering.judge("random-runtime", "code-author", "L0", tiers_path=REAL_TIERS)
    assert not verdict.allowed
    assert verdict.finding == tiering.FINDING_UNKNOWN_ROLE


def test_unknown_task_class_refused():
    verdict = tiering.judge("fleet", "not-a-class", "L0", tiers_path=REAL_TIERS)
    assert not verdict.allowed
    assert verdict.finding == tiering.FINDING_UNKNOWN_TASK_CLASS


def test_unknown_tier_refused():
    verdict = tiering.judge("fleet", "code-author", "L9", tiers_path=REAL_TIERS)
    assert not verdict.allowed
    assert verdict.finding == tiering.FINDING_UNKNOWN_TIER


def test_missing_tiers_file_is_cannot_assess():
    with pytest.raises(tiering.TieringUnavailable):
        tiering.judge("fleet", "code-author", "L0", tiers_path=Path("/no/such/tiers.yaml"))


def test_cli_ok_exit_code(capsys):
    rc = tiering.main(["judge", "--role", "fleet", "--class", "code-author", "--tier", "L0",
                        "--tiers-path", str(REAL_TIERS)])
    assert rc == 0


def test_cli_refusal_exit_code_and_finding(capsys):
    rc = tiering.main(["judge", "--role", "claude-subagent", "--class", "code-author",
                        "--tier", "L2", "--tiers-path", str(REAL_TIERS)])
    out = capsys.readouterr().out
    assert rc == 1
    assert tiering.FINDING_ROLE_NOT_ALLOWED in out


def test_cli_cannot_assess_exit_code(capsys):
    rc = tiering.main(["judge", "--role", "fleet", "--class", "code-author", "--tier", "L0",
                        "--tiers-path", "/no/such/tiers.yaml"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "CANNOT-ASSESS" in err
