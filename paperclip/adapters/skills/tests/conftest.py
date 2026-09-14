"""Shared fixtures for the paperclip skills adapter suite (issue #419).

The suite never mutates the working tree: every provocation runs against a
scratch copy of the adapter's inputs (the package, the tool authority and the
profile seeds), addressed through the CLI's ``--root``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PKG = Path("paperclip/adapters/skills")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture()
def scratch_root(tmp_path: Path) -> Path:
    """A throwaway repo root carrying the adapter's inputs."""
    root = tmp_path / "repo"
    (root / "paperclip" / "adapters").mkdir(parents=True)
    (root / "registry" / "profiles").mkdir(parents=True)
    shutil.copytree(REPO / PKG, root / PKG)
    shutil.copytree(REPO / "gateway", root / "gateway")
    shutil.copytree(
        REPO / "registry" / "profiles" / "seeds", root / "registry" / "profiles" / "seeds"
    )
    return root
