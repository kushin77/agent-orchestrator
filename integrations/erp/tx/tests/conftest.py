"""Suite for the ERP transactional spine (issue #648).

Shared fixtures only. Every fixture is *resolved*, never declared: the definition
set comes from the indexer and the ERP-02 model through
:func:`integrations.erp.tx.definitions.load`, and the scenarios come from
:mod:`integrations.erp.tx.spine`. A fixture that embedded a state name or a
document kind would be the duplication acceptance criterion 3 forbids, one layer
down.
"""

from __future__ import annotations

import pytest

from integrations.erp.tx import definitions as definitions_module
from integrations.erp.tx import spine


@pytest.fixture(scope="session")
def defs():
    """The resolved definition set (indexer + ERP-02)."""
    return definitions_module.load()


@pytest.fixture(scope="session")
def scene():
    """The golden scenario's data."""
    return spine.golden_scenario()


@pytest.fixture()
def space(defs, scene):
    """A fresh workspace for the golden scenario."""
    return spine.workspace(defs, scene)


@pytest.fixture(scope="session")
def golden(defs):
    """The happy cycle, run once."""
    return spine.golden_path("fixture", defs)


@pytest.fixture(scope="session")
def cancelled(defs):
    """The cycle plus its cancellation, run once."""
    return spine.cancellation_path("fixture", defs)
