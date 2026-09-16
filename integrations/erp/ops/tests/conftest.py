"""Shared fixtures: the model and the catalogue are loaded once per session.

Both reads go through the lane's own loaders rather than constructing anything
by hand, so a suite that passes has exercised the loaders a caller would use —
including the frozen catalogue schema and the asset contract over both halves of
the document model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.erp.ops import catalog as catalog_mod  # noqa: E402
from integrations.erp.ops import flows  # noqa: E402
from integrations.erp.ops.model import load_model  # noqa: E402
from integrations.erp.ops.workspace import Workspace  # noqa: E402


@pytest.fixture(scope="session")
def model():
    """The composite document model: ERP-02's families plus this lane's."""
    return load_model()


@pytest.fixture(scope="session")
def catalog():
    """The lane's declaration set, as loaded from ``catalog/``."""
    return catalog_mod.load()


@pytest.fixture()
def space(model, catalog) -> Workspace:
    """A fresh workspace with the masters seeded."""
    return flows.workspace(model, catalog)
