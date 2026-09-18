"""Pytest bootstrap for the governance/controls suite (issue #890).

governance/controls/ is a standalone-modules directory with no package
``__init__.py`` (the convention shared with governance/conformance, merge,
sync, dispatch and knowledge). Putting the package directory on sys.path lets
the tests import check_spine_coverage.py plainly.
"""

from __future__ import annotations

import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)
