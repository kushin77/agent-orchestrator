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

import importlib
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for extra in (REPO_ROOT, REPO_ROOT / "fleet"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


@pytest.fixture(autouse=True)
def isolate_the_beat_producer(tmp_path: Path, monkeypatch) -> None:
    """Point the runtime-beat producer (#1412) at this test's own tmp dir.

    ``fleet/terminal.py::run_once`` posts a runtime beat BEFORE it refuses or
    spawns — ``beats.best_effort(..., root=beats.ROOT, ...)`` — and
    ``beats.ROOT`` is a module constant naming the tree the beat lands in: this
    repository. ``fleet/tests/conftest.py`` redirects that constant for every
    test in THAT suite; this suite drives the same loop (through
    ``governance/spawn/cli.py`` and ``terminal.run_once``), so it needs the same
    cover.

    Measured without it (#1459):

        python3 -m pytest -p no:cacheprovider -q \\
          governance/spawn/tests/test_consumption.py::test_the_loop_still_spawns_when_the_envelope_is_well_formed

    leaves ``.fleet/runtime-beats/deepseek-executor.json`` in the repository, and
    the next ``scripts/check-runtime-liveness.sh`` then reports every OTHER
    registered runtime ``runtime-stale`` — a gate whose verdict depends on which
    check ran before it, the ``check-docs.sh find .`` class (#764).

    A tree with no producer cannot leak one, so that absence is accepted (and a
    ``fleet/beats.py`` that is present but not importable is NOT treated as one:
    it is refused by name, the rule ``redirect_runtime_paths`` follows in
    ``fleet/tests/conftest.py``).

    The registry travels with the tree, exactly as it does in the check scripts
    that redirect this producer (#1459): the beat is therefore really WRITTEN,
    just not into this repository. A bare redirect to a tmp dir would be a
    refusal instead of a redirect — `load_registry(root)` reads
    ``<root>/fleet/runtimes.yaml`` — and a suite whose stamps are all refused
    would pass this fixture for a reason that has nothing to do with the loop
    actually stamping.
    """
    producer = REPO_ROOT / "fleet" / "beats.py"
    try:
        beats = importlib.import_module("beats")
    except ImportError:
        if producer.exists():
            pytest.fail(
                f"{producer} exists but the producer module is not importable — "
                "the beats cover cannot be assumed"
            )
        return
    if not hasattr(beats, "ROOT"):
        pytest.fail("fleet/beats.py no longer exposes ROOT — the beats cover cannot be assumed")
    root = tmp_path / "fleet-beats"
    registry = REPO_ROOT / "fleet" / "runtimes.yaml"
    if registry.is_file():
        (root / "fleet").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(registry, root / "fleet" / "runtimes.yaml")
    monkeypatch.setattr(beats, "ROOT", root)


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
