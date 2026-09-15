"""Shared bootstrap for the ERP core document model suite (ERP-02, #647).

Puts the repository root on ``sys.path`` so the suite imports the model by its
real package path (``integrations.erp.core``) however pytest is invoked, and
publishes the two asset directories plus a session-scoped loaded model so a test
never re-reads the tree by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORE = Path(__file__).resolve().parents[1]
SCHEMAS = CORE / "schemas"
WORKFLOWS = CORE / "workflows"


@pytest.fixture(scope="session")
def model():
    """The model as shipped."""
    from integrations.erp.core import validators

    return validators.load_model()


@pytest.fixture(scope="session")
def validator():
    """A validator rooted at the shipped schema directory."""
    from integrations.erp.core.schema import Validator

    return Validator(base_dir=SCHEMAS)
