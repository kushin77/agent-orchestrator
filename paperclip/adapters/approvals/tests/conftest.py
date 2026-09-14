"""pytest bootstrap for the approvals adapter (issue #416).

Puts the repo root on ``sys.path`` so the tests import the adapter by its real
package path (``paperclip.adapters.approvals``) regardless of where pytest is
invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperclip.adapters.approvals import fixtures  # noqa: E402


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A clean fixture tree, the shape the projector reads."""
    return fixtures.build_tree(tmp_path)
