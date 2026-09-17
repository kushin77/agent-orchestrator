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

# governance/board shares the bare basenames "model", "cli" and "gate" with
# sibling governance/* suites. Evict any stale sys.modules entry from an
# earlier-collected suite before this directory's test modules do their own
# bare imports, so they resolve against THIS package's files (issues #699,
# #702, #1042).
for _name in ("model", "cli", "gate"):
    sys.modules.pop(_name, None)
