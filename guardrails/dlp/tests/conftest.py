"""Pytest bootstrap: make ``dlp`` and ``support`` importable from any cwd.

``guardrails/`` is a PEP-420 namespace package (no ``__init__.py`` yet), so
this inserts ``guardrails/`` — two levels above this file — at the front of
``sys.path``. Every test can then ``from dlp import ...``. Also exposes this
tests directory so tests can ``import support``.

Kept free of sibling constants (the plain module name ``conftest`` is shared
across test directories when suites run together).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# guardrails/dlp/tests -> guardrails/dlp -> guardrails
_guardrails_root = os.path.dirname(os.path.dirname(_here))
if _guardrails_root not in sys.path:
    sys.path.insert(0, _guardrails_root)
if _here not in sys.path:
    sys.path.insert(0, _here)
