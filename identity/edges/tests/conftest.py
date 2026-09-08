"""Pytest bootstrap for identity/edges tests (not collected by pytest itself).

``identity/`` has no ``__init__.py`` (it acts as a PEP-420 namespace
package), so this inserts the repo root on ``sys.path`` - mirroring
``identity/sso/tests/conftest.py`` - so both ``identity.edges`` and the
sibling ``identity.sso`` contract (real offline ``SsoService`` integration in
``test_authn.py``) are importable no matter where pytest is invoked from.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# identity/edges/tests -> identity/edges -> identity -> repo root
_edges_root = os.path.dirname(_here)
_identity_root = os.path.dirname(_edges_root)
_repo_root = os.path.dirname(_identity_root)
sys.path.insert(0, _repo_root)

import pytest  # noqa: E402

from identity.edges.allowlist import default_public_routes  # noqa: E402

# Deterministic fixture clock (matches identity/sso NOW; far in the future so
# freshly issued test tokens are never "expired" by wall-clock drift).
NOW = 1_800_000_000


@pytest.fixture()
def routes():
    """The shipped default public allowlist."""
    return default_public_routes()


@pytest.fixture()
def now() -> int:
    """The deterministic fixture clock."""
    return NOW
