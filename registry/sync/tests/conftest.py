"""Pytest bootstrap: make the ``sync`` package importable from any cwd.

``registry/`` has no ``__init__.py`` (mirrors registry/profiles,
registry/personas), so this inserts ``registry/`` — two levels above this
file — at the front of ``sys.path``.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# registry/sync/tests -> registry/sync -> registry
_registry_root = os.path.dirname(os.path.dirname(_here))
if _registry_root not in sys.path:
    sys.path.insert(0, _registry_root)
