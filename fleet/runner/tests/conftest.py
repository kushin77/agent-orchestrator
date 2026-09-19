"""Pytest bootstrap for fleet/runner: the repo root goes on sys.path so the
package imports as `fleet.runner.*` the way `cli.py` imports it."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
