"""Pytest bootstrap for the e2e suite (issue #46).

Makes the merged pillar modules importable exactly like every other suite's
``conftest.py``:

* repo root on ``sys.path``  -> PEP-420 namespace packages
  (``identity.onboarding``, ``telemetry.*``, ``engine.core``),
* pillar dirs on ``sys.path`` -> top-level packages (``proxy``, ``rbac``,
  ``service``, ``dlp``, ``policy``, ``honesty``).

``telemetry/`` is intentionally NOT a top-level root (gateway/finops ships a
plain top-level module literally named ``metering``); see e2e/_paths.py.

This file intentionally carries no module-level state shared with sibling
suites (the plain name ``conftest`` collides when suites run in one pytest
invocation, which is exactly why the QA gate runs each suite in isolation).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# e2e/tests -> e2e -> repo root
_e2e_root = os.path.dirname(_here)
_repo_root = os.path.dirname(_e2e_root)
_paths = [_repo_root] + [
    os.path.join(_repo_root, rel)
    for rel in ("gateway", "identity", "registry", "guardrails")
]
for _path in _paths:
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402

from e2e.wiring import TENANT, build_control_plane  # noqa: E402


@pytest.fixture()
def control(tmp_path):
    """A fresh, fully provisioned offline control plane per test."""
    work_dir = os.path.join(str(tmp_path), "e2e-run")
    return build_control_plane(TENANT, work_dir=work_dir)
