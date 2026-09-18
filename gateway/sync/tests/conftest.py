"""Pytest bootstrap: make the ``sync`` package importable from any cwd.

``gateway/`` has no ``__init__.py``, so this inserts ``gateway/`` — two levels
above this file — at the front of ``sys.path``, mirroring
``gateway/catalog/tests/conftest.py`` and ``gateway/health/tests/conftest.py``.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
# gateway/sync/tests -> gateway/sync -> gateway
_gateway_root = os.path.dirname(os.path.dirname(_here))
if _gateway_root not in sys.path:
    sys.path.insert(0, _gateway_root)


@pytest.fixture()
def fixture_gateway_root(tmp_path):
    """A scratch ``gateway/`` tree with one real, offline catalog module."""
    modules_dir = tmp_path / "catalog" / "modules" / "hermes"
    modules_dir.mkdir(parents=True)
    (modules_dir / "module.json").write_text(
        json.dumps(
            {
                "schema": "cmr.module/v1",
                "id": "hermes",
                "class": ["model-gateway", "provider", "hermes", "local"],
                "distribution": {"package": "gateway.providers.hermes"},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path
