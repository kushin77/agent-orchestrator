"""Pytest bootstrap for the ERP end-to-end suite (issue #655).

Makes the merged pillars importable exactly like every other suite's ``conftest.py``
(repo root for the PEP-420 namespace packages — ``integrations.erp.*``, ``portal.*``,
``telemetry.*`` — and the four pillar directories for the top-level packages
``proxy``/``rbac``/``service``/``dlp``, which is this repository's own convention).

The journey and the controls are **module-scoped**: both are deterministic and keyless
by construction, they are the same values the lane's own ``check`` measures, and
re-running them per test would only make the suite slower without measuring anything
more (the determinism of the journey is itself asserted, twice, by ``cli check`` and by
``test_the_journey_is_deterministic`` here).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# e2e/erp/tests -> e2e/erp -> e2e -> repo root
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
for _rel in ("", "gateway", "identity", "registry", "guardrails"):
    _path = _repo_root if not _rel else os.path.join(_repo_root, _rel)
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402

from e2e.erp.gate import DEFAULT_TENANT, mint_session  # noqa: E402
from e2e.erp.golden_path import run_erp_golden_path, run_cycle  # noqa: E402
from e2e.erp.negative_controls import run_erp_negative_controls  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> str:
    """The repository under test (this checkout)."""
    return _repo_root


@pytest.fixture(scope="session")
def run_dir(tmp_path_factory) -> str:
    """A scratch directory for the evidence documents the stages write."""
    return str(tmp_path_factory.mktemp("erp-e2e-run"))


@pytest.fixture(scope="session")
def session():
    """One minted console session, shared by the journey and the flag control."""
    return mint_session(tenant=DEFAULT_TENANT)


@pytest.fixture(scope="module")
def cycle():
    """One resolved cycle, so the scope and metering assertions share the spine's own."""
    return run_cycle()


@pytest.fixture(scope="module")
def journey(run_dir, repo_root, session) -> dict:
    """The whole golden path, measured once."""
    return run_erp_golden_path(work_dir=run_dir, repo_root=repo_root, session=session)


@pytest.fixture(scope="module")
def controls(run_dir, repo_root, session, cycle) -> dict:
    """Every negative control, measured once."""
    return run_erp_negative_controls(
        work_dir=run_dir, repo_root=repo_root, session=session, cycle=cycle
    )


@pytest.fixture(scope="module")
def control_by_id(controls) -> dict:
    """The controls keyed by id, so a test names the one it is about."""
    return {control["controlId"]: control for control in controls["controls"]}
