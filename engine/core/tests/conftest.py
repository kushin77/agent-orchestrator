"""Pytest bootstrap: make ``core`` and ``support`` importable from any cwd.

``engine/`` has no ``__init__.py`` (a later engine-phase lane owns adding
one), so this inserts ``engine/`` - two levels above this file - at the
front of ``sys.path``.  Every test can then ``from core import ...`` and the
sibling engine packages (``queue``, ``loop``, ...) are importable too.  Also
exposes this tests directory so tests can ``import support``.

Kept free of sibling constants (the plain module name ``conftest`` is shared
across test directories when suites run together).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# engine/core/tests -> engine/core -> engine
_engine_root = os.path.dirname(os.path.dirname(_here))
if _engine_root not in sys.path:
    sys.path.insert(0, _engine_root)
if _here not in sys.path:
    sys.path.insert(0, _here)
