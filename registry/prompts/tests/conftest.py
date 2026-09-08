"""Pytest bootstrap: make the prompt-library package importable.

The modules under registry/prompts/ are executable scripts with no cross-file
package imports (each is standalone; abtest.py imports feedback from the same
directory). Inserting the package directory at the front of sys.path lets the
tests import them plainly as ``registry``, ``feedback``, and ``abtest``.
"""

from __future__ import annotations

import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)
