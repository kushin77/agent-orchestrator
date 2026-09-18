"""Suite wiring for governance/notices (issue #1269).

The repo root goes on `sys.path` so `governance.notices.*` resolves however the
suite is invoked -- the gate runs it as
`python3 -m pytest governance/notices/tests` from the checkout root.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
