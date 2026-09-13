"""Pytest bootstrap for the governance/finops suite (M26, issue #164).

``governance/finops/chooser.py`` is a standalone script (repo convention:
namespace modules), so the package directory goes to the front of ``sys.path``.
"""

from __future__ import annotations

import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

import chooser  # noqa: E402
