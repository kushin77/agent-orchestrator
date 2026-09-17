"""Pytest bootstrap for the governance/waves suite (issue #181).

``governance/waves`` is a namespace package (no ``__init__.py``), mirroring
``governance/dispatch`` and ``governance/merge``: the package directory is put
at the front of ``sys.path`` and the modules import plainly.
"""

from __future__ import annotations

import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

# governance/waves shares the bare basenames "model" and "cli" with sibling
# governance/* suites. Evict any stale sys.modules entry from an
# earlier-collected suite before this directory's test modules do their own
# bare imports, so they resolve against THIS package's files (issues #699,
# #702, #1042).
for _name in ("model", "cli"):
    sys.modules.pop(_name, None)
