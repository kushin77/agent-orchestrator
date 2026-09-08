"""Pytest bootstrap: make ``entitlements`` and ``rbac`` importable.

``identity/`` has no ``__init__.py`` (a later identity-phase lane owns adding
one), so this inserts ``identity/`` - three levels above this file - at the
front of ``sys.path``. Every test can then ``from entitlements import ...``
and ``from rbac import ...`` no matter where pytest is invoked from.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# identity/entitlements/tests -> identity/entitlements -> identity
_identity_root = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _identity_root)

import pytest  # noqa: E402

import entitlements as E  # noqa: E402
from rbac import InMemoryStore as RbacStore  # noqa: E402


@pytest.fixture()
def catalog():
    """The shipped plan catalog (plans/catalog.yaml)."""
    return E.load_default_catalog()


@pytest.fixture()
def estore():
    """A fresh entitlement store per test."""
    return E.InMemoryStore()


@pytest.fixture()
def rbac_store():
    """A fresh RBAC store per test (identity/rbac InMemoryStore)."""
    return RbacStore()
