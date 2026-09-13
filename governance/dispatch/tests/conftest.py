"""Pytest bootstrap for the governance/dispatch suite (issue #157).

The modules under ``governance/dispatch`` are scripts, not an installable
package (mirroring ``governance/sync`` and ``governance/merge``), so the package
directory is put at the front of ``sys.path`` and the modules import plainly.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

from model import Issue, Snapshot  # noqa: E402

BASE_TIME = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def base_time() -> datetime:
    return BASE_TIME


@pytest.fixture
def snapshot() -> Snapshot:
    """A small, deterministic board: #601 frontier, #602 blocked, #603 later, #605 child."""
    issues = {
        600: Issue(600, "epic of the milestone", milestone="M25", labels=("type:epic",)),
        601: Issue(601, "frontier", milestone="M25", labels=("type:task",)),
        602: Issue(602, "blocked by 603", milestone="M25", blocked_by=(603,)),
        603: Issue(603, "later in the milestone", milestone="M25"),
        604: Issue(604, "already closed", state="closed", milestone="M25"),
        605: Issue(605, "child of the frontier", milestone="M25", parent=601),
        606: Issue(606, "different milestone", milestone="M24"),
    }
    return Snapshot(generated_at="2026-09-13T12:00:00Z", source="test", issues=issues)
