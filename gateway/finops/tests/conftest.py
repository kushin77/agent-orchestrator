"""Pytest bootstrap: make the finops modules importable.

The modules under gateway/finops/ are standalone scripts with plain
cross-file imports (chooser.py imports loader/budget/metering/complexity).
Inserting the package directory at the front of sys.path lets the tests
import them plainly as ``loader``, ``budget``, ``metering``, ``complexity``
and ``chooser`` (mirrors the registry/prompts lane convention).
"""

from __future__ import annotations

import os
import sys

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)


@pytest.fixture(scope="session")
def table():
    """The shipped, validated tier table (tiers.yaml)."""
    import loader

    return loader.load_tier_table()


@pytest.fixture(scope="session")
def seeded_enforcer():
    """Budget enforcer loaded from the shipped budgets.yaml."""
    import budget

    return budget.load_budgets()
