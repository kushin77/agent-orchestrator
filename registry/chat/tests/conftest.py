"""Pytest bootstrap for the chat quality loop (issue #509).

``registry/`` carries no ``__init__.py`` (per-issue package directories), so this
inserts the repo root at the front of ``sys.path`` and every test imports
``registry.chat.*`` no matter where pytest is invoked — the same bootstrap
``telemetry/budgets/tests/conftest.py`` uses. There is deliberately no
``tests/__init__.py``.

The fixtures hand a test either the committed package (read-only: the fixtures,
the modules and the manifest are what they are) or a disposable copy of it, so a
test that must mutate a module definition or a fixture expectation never writes
into the lane tree.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CHAT_PKG = Path(REPO_ROOT) / "registry" / "chat"
CASES_PATH = CHAT_PKG / "eval" / "cases.yaml"


@pytest.fixture()
def chat_root() -> Path:
    """The committed chat package root (read-only in every test)."""
    return CHAT_PKG


@pytest.fixture()
def cases_path() -> Path:
    """The committed fixture set."""
    return CASES_PATH


@pytest.fixture()
def scratch_package(tmp_path: Path) -> Path:
    """A disposable copy of the chat package, safe to mutate.

    The copy carries the same relative layout as the real package, so every
    reference (bodyRefs, outputSchema) resolves inside the copy and a mutated
    definition cannot leak back into the lane tree.
    """
    destination = tmp_path / "chat"
    shutil.copytree(
        CHAT_PKG,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"),
    )
    return destination
