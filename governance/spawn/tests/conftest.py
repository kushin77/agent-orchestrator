"""Pytest bootstrap + fixtures for the governance/spawn suite (issue #793).

``governance/`` is a PEP-420 namespace package, so the repository root goes on
``sys.path`` and the modules are imported qualified (``governance.spawn.*``).
``fleet/`` goes on too: the point of this suite is partly that the LOOP consumes
the envelope, and that means importing ``fleet/terminal.py`` and
``fleet/watchdog.py`` as the standalone scripts they are.

The `envelope_fields` fixture is a COMPLETE, well-formed field set with nothing
live in it. Every refusal test removes exactly one thing from it, so a test that
passes because the box happened to be unmeasurable cannot exist: the fields are
the test's own.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for extra in (REPO_ROOT, REPO_ROOT / "fleet"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


@pytest.fixture
def envelope_fields(tmp_path: Path) -> dict[str, Any]:
    """One complete, well-formed field set — offline, deterministic, no live box."""
    worktree = tmp_path / "ao-793-fixture"
    return {
        "issue": 793,
        "lane": "spawn-envelope",
        "worktree": str(worktree),
        "session": {
            "id": "fixture000042",
            "issue": "793",
            "agent": "fixture-agent",
            "lane": "spawn-envelope",
            "branch": "issue-793",
            "worktree": str(worktree),
            "repo_slug": "kushin77/agent-orchestrator",
            "author_name": "agent-fixture-agent",
            "author_email": "agent+fixture-agent@agents.invalid",
            "committer_name": "agent-fixture-agent",
            "committer_email": "agent+fixture-agent@agents.invalid",
        },
        "trailer": "Refs kushin77/agent-orchestrator#793",
        "claim": {"owner": "fixture-agent", "state": "claim", "lane": "spawn-envelope", "at": "2026-09-15T00:00:00Z"},
        "focus": {"epic": 708, "source": "board-snapshot-parent", "pinned_epic": 707, "wave_cap": 12, "max_agents": 0},
        "capacity": {
            "effective": 1,
            "binding": "disjoint",
            "assessed": True,
            "bounds": [{"name": "pool", "limit": 10, "why": "fixture"}],
            "problems": [],
            "permit": {
                "store": str(tmp_path / "gate-store"),
                "worktree_key": "0123456789abcdef",
                "lock": str(tmp_path / "gate-store" / "worktrees" / "0123456789abcdef.lock"),
                "max_concurrent": 4,
            },
        },
        "budget": {"attempts": 0, "cap": 5, "state": "pending", "next_attempt_at": None},
        "gate": {
            "of_record": "make verify",
            "bound": "at most one composite gate per worktree, bounded box-wide (AO-GR-22)",
            "entry": "scripts/gate-lock.sh",
            "max_concurrent": 4,
            "ttl_seconds": 900,
        },
        "verify": {"command": "bash scripts/check-spawn-envelope.sh", "source": "issue-verify-clause"},
        "spawn": {
            "path": "fleet",
            "agent": "fixture-agent",
            "directive": "fixture-directive",
            # The admission inputs the three judges read (#1413): the fixture is a
            # spawn that is admissible, so every refusal test below still removes
            # exactly ONE thing.
            "runtime": "claude-subagent",
            "role": "fleet",
            "tier": "L0",
            "task_class": "code-author",
            "actor": "claude-subagent",
            "verbs": [],
            "skills": [],
            "secrets": [],
        },
    }


@pytest.fixture
def envelope(envelope_fields: dict[str, Any]):
    """The assembled document for those fields."""
    from governance.spawn import model

    fields = dict(envelope_fields)
    spawn_meta = fields.pop("spawn")
    return model.assemble(fields, spawn=spawn_meta)
