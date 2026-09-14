"""Fixtures for the shared-frontend onboarding suite (issue #703).

The lane directory has a hyphen in its name, so `render.py` is not importable as
a package module: it is loaded by file path. Every test runs against a SCRATCH
repo tree that mirrors the real layout (the lane under
`governance/onboarding/shared-frontend/`, the two assets at the root), so the
suite never mutates the checkout it is grading.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

LANE = Path(__file__).resolve().parents[1]
REPO_ROOT = LANE.parents[2]
LANE_REL = Path("governance/onboarding/shared-frontend")
VENDORED_TOKENS = REPO_ROOT / "vendor/CMR/templates/frontend/shared/design-tokens/tokens.json"


def _load_renderer():
    spec = importlib.util.spec_from_file_location("shared_frontend_render", LANE / "render.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def renderer():
    """The renderer module, loaded by path."""
    return _load_renderer()


@pytest.fixture
def stage():
    """Return a function that stages a scratch repo tree under `dest`."""

    def _stage(dest: Path) -> Path:
        dest = Path(dest)
        (dest / LANE_REL).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(LANE, dest / LANE_REL)
        for name in ("tokens.json", "gdc-manifest.yaml"):
            shutil.copy2(REPO_ROOT / name, dest / name)
        return dest

    return _stage


@pytest.fixture
def clean_repo(tmp_path, stage):
    """A scratch repo tree that IS the render of the committed instance."""
    return stage(tmp_path / "repo")


@pytest.fixture
def lane_in(clean_repo):
    """Return the lane path inside a scratch repo tree."""
    return clean_repo / LANE_REL
