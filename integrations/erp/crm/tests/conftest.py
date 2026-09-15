"""Shared pytest bootstrap and fixtures for the CRM-family suite (issue #650).

Puts the repository root on ``sys.path`` so the tests import the package by its
real path (``integrations.erp.crm``) regardless of where pytest is invoked. The
``definitions`` and ``golden`` fixtures are session-scoped and read-only: the
flows return new workspaces rather than mutating one, so one golden path can be
shared by every test without any test being able to affect another.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.erp.crm import flows  # noqa: E402
from integrations.erp.crm.definitions import DefinitionSet, load  # noqa: E402


@pytest.fixture(scope="session")
def definitions() -> DefinitionSet:
    """The declaration set this lane ships."""
    return load()


@pytest.fixture(scope="session")
def golden(definitions: DefinitionSet) -> flows.GoldenPath:
    """The deterministic end-to-end scenario, computed once."""
    return flows.golden_path("acme", definitions)


@pytest.fixture()
def space(definitions: DefinitionSet) -> flows.Workspace:
    """An empty workspace for a test that wants to build its own scenario."""
    return flows.workspace("acme", definitions)
