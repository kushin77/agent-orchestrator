"""Bootstrap for the command-center CLI's suite (issue #556, RC-5).

Two paths have to be on ``sys.path`` for these tests to exercise what ships:

``control-plane/cli``
    the directory ``main.py`` puts on the path itself, so ``import aoctl.*``
    resolves exactly as it does when an operator runs the CLI;
``<repo root>``
    so ``integrations.paperclip.client`` — the boundary's one transport seam —
    resolves exactly as it does from the CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLI_DIR = ROOT / "control-plane" / "cli"

for _path in (str(CLI_DIR), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
