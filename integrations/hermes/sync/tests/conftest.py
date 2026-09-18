"""Pytest bootstrap for ``integrations/hermes/sync`` (issue #889).

Puts the repo root on ``sys.path`` so the tests import by the real package
path (``integrations.hermes.sync``), mirroring
``integrations/hermes/tests/conftest.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
