"""Pytest bootstrap for the registry parity suite (issue #145).

``registry/parity/parity.py`` is a standalone script with no package
``__init__.py`` (mirroring ``registry/profiles`` and ``registry/personas``).
Putting the package directory at the front of ``sys.path`` lets the tests import
it plainly as ``parity``.
"""

from __future__ import annotations

import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)
