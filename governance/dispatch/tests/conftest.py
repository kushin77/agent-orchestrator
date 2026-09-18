"""Pytest bootstrap for the governance/dispatch suite (issue #157).

The modules under ``governance/dispatch`` are scripts, not an installable
package (mirroring ``governance/sync`` and ``governance/merge``), so the package
directory is put at the front of ``sys.path`` and the modules import plainly.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

# governance/dispatch shares the bare basenames "model" and "cli" with sibling
# governance/* suites. Evict any stale sys.modules entry from an
# earlier-collected suite before this package's own bare imports (and this
# directory's test modules' bare imports), so they resolve against THIS
# package's files (issues #699, #702, #1042).
for _name in ("model", "cli"):
    sys.modules.pop(_name, None)

from model import Issue, Snapshot  # noqa: E402
import focus  # noqa: E402
import owner_queue  # noqa: E402
import pool  # noqa: E402

BASE_TIME = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def base_time() -> datetime:
    return BASE_TIME


def _absent_focus_file(tmp_path) -> str:
    """A path that does not exist, i.e. "nothing is pinned".

    ``focus.load`` returns ``None`` for a missing file, so ``focus.active`` falls
    back to "the lowest-numbered open workable epic" — which is the *product's*
    documented resolution rule (F1/#716), not an absence of focus. Tests that need
    a board with no active epic must therefore build one with no workable epic
    (see ``test_order.test_a_board_with_no_epic_pools_nothing``); this fixture
    only guarantees the suite does not depend on whichever pin happens to be
    committed in the checkout that is the cwd.
    """
    return str(tmp_path / "absent-focus.json")


@pytest.fixture(autouse=True)
def no_ambient_focus(tmp_path, monkeypatch) -> None:
    """Decouple the suite from the committed ``.board/focus.json``.

    ``focus.DEFAULT_PATH`` is *relative*, so without this every test that omits
    ``focus_path`` would resolve the repository's own pin (epic #707) and the
    suite's meaning would depend on the checkout that happens to be the cwd.
    Pointing the default at a non-existent file leaves resolution to the
    documented fallback (lowest open workable epic in the *fixture's* board),
    which is what the pre-#721 tests were written against.

    The patch targets the *module objects* rather than the string form
    ``"focus.DEFAULT_PATH"``: the package directory is on ``sys.path`` under a
    bare name (this is a script package, not an installable one), so a string
    target can resolve to a different module instance than the one ``order`` and
    ``claims`` imported — and the patch would then look applied while having no
    effect.
    """
    monkeypatch.setattr(focus, "DEFAULT_PATH", _absent_focus_file(tmp_path))
    monkeypatch.setattr(pool, "POOL_PATH", tmp_path / "no-pool.jsonl")
    # Same reasoning for the owner queue (#928): without this, every test that
    # does not ask for a queue fixture would read the repository's own
    # committed governance/dispatch/queue.yaml, so adding a number to it could
    # move an unrelated test's blocked_by set.
    monkeypatch.setattr(owner_queue, "DEFAULT_PATH", tmp_path / "no-queue.yaml")


@pytest.fixture
def focused(tmp_path):
    """Builder for a focus file pinning a given epic.

    Several suites need the same fixture shape (``test_focus``, ``test_order``,
    ``test_claims``); declaring it once here keeps the schema in one place, so a
    change to ``.board/focus.json`` cannot desynchronise three copies.
    """

    def build(epic, *, wave_cap: int = 12, max_agents: int = 0):
        path = tmp_path / f"focus-{epic}.json"
        path.write_text(
            json.dumps(
                {
                    "active_epic": epic,
                    "activated_at": "2026-09-13T12:00:00Z",
                    "wave_cap": wave_cap,
                    "max_agents": max_agents,
                    "pooled": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    return build


@pytest.fixture
def snapshot() -> Snapshot:
    """A small, deterministic board: #601 frontier, #602 blocked, #603 later, #605 child."""
    issues = {
        600: Issue(600, "epic of the milestone", state="closed", milestone="M25", labels=("type:epic",)),
        601: Issue(601, "frontier", milestone="M25", labels=("type:task",)),
        602: Issue(602, "blocked by 603", milestone="M25", blocked_by=(603,)),
        603: Issue(603, "later in the milestone", milestone="M25"),
        604: Issue(604, "already closed", state="closed", milestone="M25"),
        605: Issue(605, "child of the frontier", milestone="M25", parent=601),
        606: Issue(606, "different milestone", milestone="M24"),
    }
    return Snapshot(generated_at="2026-09-13T12:00:00Z", source="test", issues=issues)


@pytest.fixture
def pool_rail(tmp_path):
    """An isolated pool rail, so a test never writes the repository's own pool."""
    return tmp_path / "pool.jsonl"
