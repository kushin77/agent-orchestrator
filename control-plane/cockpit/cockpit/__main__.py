"""``python3 -m cockpit`` — the terminal cockpit's entry point (issue #566)."""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent.parent
if str(_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_PACKAGE))

from cockpit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
