"""Shared pytest bootstrap for the secret vault tests (issue #417).

Puts the repo root on ``sys.path`` so the tests import the primitive by its real
package path (``paperclip.adapters.secrets``) regardless of where pytest is
invoked, and exposes the repository root and the projected view as fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_PATH = ROOT / "paperclip" / "adapters" / "secrets" / "schema" / "secret.schema.json"
CATALOG_PATH = ROOT / "paperclip" / "adapters" / "secrets" / "catalog" / "secrets.json"


@pytest.fixture()
def repo_root() -> Path:
    """The repository root the primitive projects from."""
    return ROOT


@pytest.fixture()
def view() -> dict:
    """The deterministic view built from this tree (the projection under test)."""
    from paperclip.adapters.secrets import vault

    return vault.build_view(ROOT)


@pytest.fixture()
def schema() -> dict:
    """The frozen reference/rotation view schema."""
    from paperclip.adapters.secrets import vault

    return vault.load_schema(ROOT)
