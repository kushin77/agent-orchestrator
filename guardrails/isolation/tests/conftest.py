"""Pytest bootstrap: make ``isolation`` and ``honesty`` importable.

``guardrails/`` is a PEP-420 namespace package, so this inserts
``guardrails/`` — three levels above this file — at the front of
``sys.path``.  Every test can then ``from isolation import ...`` and
``from honesty.tristate import ...``.  Also exposes the lane's fixture
directory so tests reference fixture files by path.

Kept free of sibling constants (the plain module name ``conftest`` is shared
across test directories when suites run together).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# guardrails/isolation/tests -> guardrails/isolation -> guardrails
_isolation_root = os.path.dirname(_here)
_guardrails_root = os.path.dirname(_isolation_root)
for _path in (_guardrails_root, _isolation_root):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: The lane's fixtures directory (code + dataset fixtures).
FIXTURES_DIR = os.path.join(_isolation_root, "fixtures")
#: Repository root (guardrails/isolation -> guardrails -> repo root).
REPO_ROOT = os.path.dirname(_guardrails_root)
