"""Pytest bootstrap for the gateway catalog parity suite (issue #349).

``gateway/`` has no ``__init__.py`` (a later gateway-phase lane owns adding one),
so this inserts ``gateway/`` — two levels above this file — at the front of
``sys.path``. The parity tests can then ``from providers.registry import
PROVIDER_NAMES`` from any cwd (mirrors gateway/providers/tests/conftest.py).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# gateway/catalog/tests -> gateway/catalog -> gateway
_gateway_root = os.path.dirname(os.path.dirname(_here))
if _gateway_root not in sys.path:
    sys.path.insert(0, _gateway_root)
