"""Offline-gate tests (issue #45): check-rollout passes clean and fails bad input.

The committed rollout declarations must pass ``check_rollout.check_all()`` and
the negative self-test must confirm every check can fail (no-false-green). The
script is also executed end-to-end via subprocess.
"""

from __future__ import annotations

import os
import subprocess
import sys


def test_check_all_passes_on_committed_tree(repo_root: str) -> None:
    sys.path.insert(0, repo_root)
    from infra.rollout.checks import check_rollout

    assert check_rollout.check_all() == []


def test_check_stage_model_rejects_bad_doc(repo_root: str) -> None:
    sys.path.insert(0, repo_root)
    from infra.rollout.checks import check_rollout

    bad = {
        "stages": {
            "off": {"order": 0, "rollout_pct": 0, "exposed": False},
            "canary": {"order": 1, "rollout_pct": 5, "exposed": True},
            "gradual": {"order": 2, "rollout_pct": 100, "exposed": True},
            "full": {"order": 3, "rollout_pct": 100, "exposed": True},
            "bogus": {"order": 4, "rollout_pct": 100, "exposed": True},
        },
        "promotion_rules": {"mode": "strict-forward", "every_transition_requires": []},
    }
    assert check_rollout.check_stage_model(bad)


def test_check_state_rejects_default_on(repo_root: str) -> None:
    sys.path.insert(0, repo_root)
    from infra.rollout.checks import check_rollout

    bad = {"schema_version": 1, "flags": {"services.registry": {"stage": "full", "rollout_pct": 100, "targeted": []}}}
    errors = check_rollout.check_rollout_state(bad)
    assert errors
    assert any("must default to off" in e for e in errors)


def test_check_triggers_requires_disabled_rollout_triggers(repo_root: str) -> None:
    sys.path.insert(0, repo_root)
    from infra.rollout.checks import check_rollout

    # A directory with no rollout triggers must be reported as missing.
    errors = check_rollout.check_triggers(os.path.join(repo_root, "infra", "rollout", "tests"))
    assert errors
    assert any("missing trigger" in e for e in errors)


def test_script_exit_zero_on_committed_tree(repo_root: str) -> None:
    script = os.path.join(repo_root, "infra", "rollout", "checks", "check_rollout.py")
    result = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=repo_root)
    assert result.returncode == 0, result.stderr
    assert "check-rollout: OK" in result.stdout


def test_script_self_test_exit_zero(repo_root: str) -> None:
    script = os.path.join(repo_root, "infra", "rollout", "checks", "check_rollout.py")
    result = subprocess.run([sys.executable, script, "--self-test"], capture_output=True, text=True, cwd=repo_root)
    assert result.returncode == 0, result.stderr
    assert "check-rollout self-test: OK" in result.stdout
