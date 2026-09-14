"""Shared pytest bootstrap for the paperclip adapter tests (issue #428).

Puts the repo root on ``sys.path`` so the tests import the adapter by its real
package path (``integrations.paperclip``) regardless of where pytest is invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api.json"
