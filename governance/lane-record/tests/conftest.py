"""Import bootstrap for the lane-record suite (issue #1270).

The package directory carries a dash (``governance/lane-record``), so it is a
script directory and never an importable dotted package. This conftest puts the
package directory and the repository root on ``sys.path`` -- and nothing else --
so the suite imports ``lane_record`` the same way the CLI does, and imports
``governance.*`` from the checkout rather than from a sibling suite's bootstrap
(measured collision: governance/conformance and governance/tagging disagree about
a bare ``model``).
"""

from __future__ import annotations

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parents[1]

for entry in (str(ROOT), str(PKG)):
    if entry not in sys.path:
        sys.path.insert(0, entry)
