"""Pytest bootstrap: make `infra/fleet`'s modules importable from any cwd.

`infra/fleet/` has no `__init__.py`, so this inserts it — one level above this
file — at the front of `sys.path`, mirroring the sibling suites (e.g.
`gateway/health/tests/conftest.py`). Every test can then `import env_contract`
/ `import secrets_contract` no matter where pytest is invoked from.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# infra/fleet/tests -> infra/fleet
_fleet_dir = os.path.dirname(_here)
if _fleet_dir not in sys.path:
    sys.path.insert(0, _fleet_dir)

REPO_ROOT = os.path.dirname(os.path.dirname(_fleet_dir))
