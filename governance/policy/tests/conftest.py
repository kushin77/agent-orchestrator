"""Pytest bootstrap for the governance/policy suite (issue #1763).

``governance/`` and ``governance/policy/`` carry no ``__init__.py`` (the
namespace-package convention shared with ``governance/merge``, ``sync``,
``dispatch``, ``conformance`` and ``knowledge``). Putting the repository root on
``sys.path`` lets the tests import the module by its real dotted path,
``governance.policy.registry`` — the same idiom ``portal/tests/conftest.py``
uses — rather than a bare basename that a sibling ``governance/*`` suite could
shadow.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
