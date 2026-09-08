"""Pytest bootstrap: make the ``engine.queue`` package importable from any cwd.

``engine/`` has no ``__init__.py`` (sibling lanes add their own subpackages),
so the repo root — three levels above this file — is inserted at the front of
``sys.path``, mirroring the identity/onboarding and gateway/health lanes.
Every test can then ``from engine.queue import ...`` no matter where pytest
is invoked from.
"""

from __future__ import annotations

import os
import sys

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
# engine/queue/tests -> engine/queue -> engine -> repo root
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from engine.queue.config import QueueConfig  # noqa: E402
from engine.queue.queue import JobQueue  # noqa: E402
from engine.queue.store import FileStore, InMemoryStore  # noqa: E402


class FakeClock:
    """Deterministic clock for lease / reap / age-bump timing tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_queue(clock):
    def _make(store=None, config=None) -> JobQueue:
        cfg = config if config is not None else QueueConfig()
        return JobQueue(store=store or InMemoryStore(), config=cfg, clock=clock)

    return _make


@pytest.fixture
def q(make_queue) -> JobQueue:
    return make_queue()


@pytest.fixture
def file_store(tmp_path) -> FileStore:
    return FileStore(str(tmp_path / "queue.json"))


def cfg(**kwargs) -> QueueConfig:
    """Build a QueueConfig overriding only the given knobs."""
    return QueueConfig(**kwargs)
