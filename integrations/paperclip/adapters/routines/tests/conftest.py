"""pytest bootstrap for the routines adapter (issue #418).

Puts the repo root on ``sys.path`` so the tests import the adapter by its real
package path (``integrations.paperclip.adapters.routines``) regardless of where
pytest is invoked, and provides a minimal *schedule tree* — the real
``fleet/cron.py`` beside its ``runtime`` dependency — so a test can mutate the
schedule the code declares without touching the checkout.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FLEET = ROOT / "fleet"


def build_schedule_tree(tmp_path: Path, mutate=None) -> Path:
    """A tree carrying ``fleet/cron.py`` (+ ``runtime``) and nothing else.

    ``mutate`` may rewrite the ``cron.py`` text before it is written — that is
    how a test provokes drift (an entry added or removed) without editing the
    checkout. The PMO side is deliberately absent: the schedule tests must not
    depend on a board.
    """
    tree = tmp_path / "tree"
    (tree / "fleet").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FLEET / "runtime.py", tree / "fleet" / "runtime.py")
    text = (FLEET / "cron.py").read_text(encoding="utf-8")
    if mutate is not None:
        text = mutate(text)
    (tree / "fleet" / "cron.py").write_text(text, encoding="utf-8")
    return tree


@pytest.fixture()
def schedule_tree(tmp_path: Path) -> Path:
    """A pristine schedule tree: exactly the three entries the code declares."""
    return build_schedule_tree(tmp_path)
