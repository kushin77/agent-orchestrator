"""Pytest bootstrap for engine/loop tests.

``engine/`` has no ``__init__.py`` (a PEP-420 namespace; sibling lanes own
their subpackages), so ``engine.loop`` is reached from the repo root — three
levels above this file — which is inserted at the front of ``sys.path``,
mirroring the engine/queue and engine/memory conftests.  This tests directory
is also inserted so tests can ``from loop_support import ...``.

Kept free of sibling constants: the plain module name ``conftest`` is shared
across engine test directories when suites run together.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))  # .../engine/loop/tests
_loop_root = os.path.dirname(_here)  # .../engine/loop
_engine_root = os.path.dirname(_loop_root)  # .../engine
_repo_root = os.path.dirname(_engine_root)  # repo root
for _path in (_repo_root, _here):
    if _path not in sys.path:
        sys.path.insert(0, _path)
