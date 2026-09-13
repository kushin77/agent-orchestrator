"""Pytest bootstrap for the governance/board suite (issue #143).

Same convention as governance/conformance, governance/lessons: no package
``__init__.py``, the directory goes on sys.path so the tests import plainly.
"""

from __future__ import annotations

import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)
