"""Offline-gate tests (issue #45): check-rollout passes clean and fails bad input.

The committed rollout declarations must pass ``check_rollout.check_all()`` and
the negative self-test must confirm every check can fail (no-false-green). The
script is also executed end-to-end via subprocess.

The plan/state pairing is asserted in BOTH directions (#966): a declared flag
no phase reaches is a finding, and so is a phase naming a flag the state does
not declare. The two are reported apart because they take different fixes.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys

import yaml


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


# --------------------------------------------------------------------------- #
# #966: the plan/state pairing is enforced in BOTH directions
# --------------------------------------------------------------------------- #


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _pair(repo_root: str):
    """(check_rollout, plan_doc, state_doc, model) read from the tree under test."""
    sys.path.insert(0, repo_root)
    from infra.rollout.checks import check_rollout
    from infra.rollout.model import StageModel

    rollout_dir = os.path.join(repo_root, "infra", "rollout")
    plan = _load(os.path.join(rollout_dir, "go-live-plan.yaml"))
    state = _load(os.path.join(rollout_dir, "rollout-state.yaml"))
    model = StageModel.load(_load(os.path.join(rollout_dir, "stage-model.yaml")))
    return check_rollout, plan, state, model


def test_committed_state_is_reachable_from_the_plan(repo_root: str) -> None:
    """Direction 1 on the real tree: every declared row is named by a phase.

    This is the half that must NOT be a false positive - the committed state
    and plan are consistent, so the new rule reports nothing.
    """
    check_rollout, plan, state, _ = _pair(repo_root)
    assert check_rollout.check_state_reachability(list(state["flags"]), plan) == []


def test_check_go_live_plan_rejects_a_declared_flag_no_phase_reaches(repo_root: str) -> None:
    """Direction 1, negative: one extra state row and NO phase naming it.

    This is the issue's mutant verbatim - a flag added to ``rollout-state.yaml``
    only. Before #966 the check returned the committed-tree verdict (rc 0) for
    this input, so a declared, drivable flag could be permanently unreachable
    with nothing red. The assertion is on the flag being NAMED, not merely on
    "some error".
    """
    check_rollout, plan, state, model = _pair(repo_root)
    errors = check_rollout.check_go_live_plan(plan, [*state["flags"], "rollout.probe_orphan"], model)
    assert errors, "a declared flag no phase reaches was accepted"
    assert any("named by no phase" in e and "rollout.probe_orphan" in e for e in errors), errors


def test_check_go_live_plan_keeps_the_two_directions_distinct(repo_root: str) -> None:
    """Direction 2: a phase naming an undeclared flag is a NAME error.

    The two failures have different fixes - add the row to a phase, versus
    declare the flag - so the check must not report one as the other.
    """
    check_rollout, plan, state, model = _pair(repo_root)
    plan = copy.deepcopy(plan)
    plan["phases"]["7"]["surfaces"].append({"flag": "services.probe_undeclared", "go_live_stage": "full"})
    errors = check_rollout.check_go_live_plan(plan, list(state["flags"]), model)
    assert any("does not resolve to a known flag" in e for e in errors), errors
    assert not any("named by no phase" in e for e in errors), errors
