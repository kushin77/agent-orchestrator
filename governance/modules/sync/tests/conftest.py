"""Pytest bootstrap for ``governance/modules/sync`` (issue #889).

``governance/`` is a PEP-420 namespace package, so the repository root goes on
``sys.path`` and the module is imported qualified
(``governance.modules.sync``), mirroring ``governance/modules/tests/conftest.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def scratch_repo_root(tmp_path):
    """A scratch repo root with a real, honest admission register + pin —
    copied from the actual committed files so the probe reads real data."""
    real_manifest = json.loads((REPO_ROOT / "module.json").read_text(encoding="utf-8"))
    (tmp_path / "module.json").write_text(json.dumps(real_manifest), encoding="utf-8")
    real_pin = (REPO_ROOT / "cmr-pin.yaml").read_text(encoding="utf-8")
    (tmp_path / "cmr-pin.yaml").write_text(real_pin, encoding="utf-8")
    return tmp_path
