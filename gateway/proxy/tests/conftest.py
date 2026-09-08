"""Pytest bootstrap: make the ``proxy`` package importable from any cwd.

``gateway/`` has no ``__init__.py`` (a later gateway-phase lane owns adding
one), so this inserts ``gateway/`` - three levels above this file - at the
front of ``sys.path``.  Every test can then ``from proxy import ...`` and the
sibling gateway packages (``providers``, ``limits``) are importable too.  Also
exposes this tests directory so tests can ``import support``.

Kept free of sibling constants (the plain module name ``conftest`` is shared
across test directories when suites run together).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# gateway/proxy/tests -> gateway/proxy -> gateway
_gateway_root = os.path.dirname(os.path.dirname(_here))
if _gateway_root not in sys.path:
    sys.path.insert(0, _gateway_root)
if _here not in sys.path:
    sys.path.insert(0, _here)
