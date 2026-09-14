"""Pytest bootstrap for the paperclip HTTP surface projection (issue #413).

Puts the repo root on ``sys.path`` so the package imports by its real path
(``integrations.paperclip.api``) regardless of where pytest runs, and provides a
deterministic throwaway tree with a declared tenancy and a fresh projection so
no test depends on the shared checkout's runtime ``.board/`` state.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: A fixed instant so every freshness decision is deterministic.
NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)

#: The two companies the throwaway tree declares.
DECLARED = ("acme", "globex")


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def root() -> Path:
    """The real repo root (for the frozen contracts and the adapter shapes)."""
    return ROOT


@pytest.fixture()
def now() -> datetime:
    return NOW


@pytest.fixture()
def tree(tmp_path: Path, now: datetime) -> Path:
    """A throwaway tree: a declared tenancy, a readable ledger, a fresh projection."""
    (tmp_path / "telemetry" / "budgets" / "config").mkdir(parents=True)
    (tmp_path / "telemetry" / "budgets" / "config" / "policies.yaml").write_text(
        "schemaVersion: 1\npolicies:\n"
        + "".join(f"  - tenantId: {tenant}\n    mode: enforce\n" for tenant in DECLARED),
        encoding="utf-8",
    )
    board = tmp_path / ".board"
    board.mkdir()
    (board / "claims.jsonl").write_text('{"issue": 413, "state": "claimed"}\n', encoding="utf-8")
    (board / "snapshot.json").write_text(
        json.dumps({"generated_at": iso(now), "issues": []}), encoding="utf-8"
    )
    return tmp_path
