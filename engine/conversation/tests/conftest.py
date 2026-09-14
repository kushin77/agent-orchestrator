"""Pytest bootstrap + shared fixtures for engine/conversation tests.

``engine/`` has no ``__init__.py`` (a later engine-phase lane owns adding one),
so ``engine.conversation`` is reached through the repo-root PEP-420 namespace:
this conftest puts the repo root on ``sys.path``, mirroring the
``engine/memory`` and ``identity/*`` conftests.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_here = os.path.dirname(os.path.abspath(__file__))        # .../engine/conversation/tests
_conversation_root = os.path.dirname(_here)               # .../engine/conversation
_engine_root = os.path.dirname(_conversation_root)        # .../engine
_repo_root = os.path.dirname(_engine_root)                # repo root
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import pytest  # noqa: E402

from engine.conversation import TranscriptStore  # noqa: E402

T1 = "tenant-acme"
T2 = "tenant-globex"
AGENT_A = "coder"
AGENT_B = "reviewer"
START = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    """A deterministic, injectable clock (the engine/memory convention)."""

    def __init__(self, start: datetime = START) -> None:
        self.moment = start

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, *, seconds: int = 0, days: int = 0) -> datetime:
        self.moment = self.moment + timedelta(seconds=seconds, days=days)
        return self.moment


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store(clock: Clock) -> TranscriptStore:
    return TranscriptStore(now=clock)


@pytest.fixture()
def memory_store():
    from engine.memory.store import InMemoryStore

    return InMemoryStore()
