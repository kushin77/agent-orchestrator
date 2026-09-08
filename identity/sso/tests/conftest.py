"""Pytest bootstrap + shared env for identity/sso tests.

``identity/`` has no ``__init__.py`` (it acts as a PEP-420 namespace
package), so this inserts the repo root and ``identity/`` on ``sys.path`` -
mirroring ``identity/onboarding/tests/conftest.py`` - so both ``identity.sso``
and (where a test wants a sibling contract) ``rbac`` / ``identity.onboarding``
are importable no matter where pytest is invoked from.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# identity/sso/tests -> identity/sso -> identity -> repo root
_sso_root = os.path.dirname(_here)
_identity_root = os.path.dirname(_sso_root)
_repo_root = os.path.dirname(_identity_root)
sys.path.insert(0, _repo_root)
sys.path.insert(0, _identity_root)

import pytest  # noqa: E402

from identity.sso.tests._support import Env, NOW, new_env  # noqa: E402


@pytest.fixture()
def env() -> Env:
    """A fresh offline SSO environment per test (store/keystore/keys)."""
    return new_env()


@pytest.fixture()
def now() -> int:
    """The deterministic fixture clock."""
    return NOW
