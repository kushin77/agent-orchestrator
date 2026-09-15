"""Shared pytest bootstrap and fixtures for the ERP FinOps suite (issue #654).

Puts the repository root on ``sys.path`` so the tests import the package by its
real path (``integrations.erp.finops``) wherever pytest is invoked, and gives
every test the same offline, deterministic workspace the lane's own check uses —
so a test cannot pass against a configuration the check never measures.

Nothing here touches the wall clock: an event timestamp is supplied by the test
(:func:`~integrations.erp.finops.harness.stamp`) or the determinism assertions
would be measuring ``datetime.now()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.erp.core.validators import load_model  # noqa: E402
from integrations.erp.finops.harness import (  # noqa: E402
    DEFAULT_TENANT,
    Workspace,
    build_workspace,
)


@pytest.fixture(scope="session")
def model():
    """The live core document model this lane meters against."""
    return load_model()


@pytest.fixture(scope="session")
def tenant() -> str:
    """The tenant the shipped budget catalog declares."""
    return DEFAULT_TENANT


@pytest.fixture()
def workspace() -> Workspace:
    """A fresh in-memory workspace over the shipped catalog."""
    return build_workspace()
