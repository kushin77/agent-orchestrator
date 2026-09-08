"""Pytest bootstrap + fixtures for the infra/rollout suite (issue #45).

``infra/`` has no ``__init__.py`` (a PEP-420 namespace; sibling pillar dirs
own their subpackages), so ``infra.rollout.*`` is reached from the repo root,
which is inserted at the front of ``sys.path``. This tests directory is not
injected so the plain module name ``conftest`` stays collision-free when suites
run together.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))  # .../infra/rollout/tests
_rollout_root = os.path.dirname(_here)  # .../infra/rollout
_infra_root = os.path.dirname(_rollout_root)  # .../infra
_repo_root = os.path.dirname(_infra_root)  # repo root
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> str:
    return _repo_root


@pytest.fixture(scope="session")
def rollout_dir(repo_root: str) -> str:
    return os.path.join(repo_root, "infra", "rollout")


@pytest.fixture(scope="session")
def stage_model_path(rollout_dir: str) -> str:
    return os.path.join(rollout_dir, "stage-model.yaml")


@pytest.fixture(scope="session")
def state_path(rollout_dir: str) -> str:
    return os.path.join(rollout_dir, "rollout-state.yaml")


@pytest.fixture(scope="session")
def plan_path(rollout_dir: str) -> str:
    return os.path.join(rollout_dir, "go-live-plan.yaml")


@pytest.fixture
def engine(stage_model_path: str, state_path: str):
    """A fresh engine seeded from the committed (all-OFF) state per test."""
    from infra.rollout.engine import RolloutEngine

    return RolloutEngine.load(stage_model_path=stage_model_path, rollout_state_path=state_path)
