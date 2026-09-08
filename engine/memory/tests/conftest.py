"""Pytest bootstrap + shared fixtures for engine/memory tests.

``engine/`` has no ``__init__.py`` (a later engine-phase lane owns adding
one), so ``engine.memory`` is reached through the repo-root PEP-420
namespace: this conftest puts the repo root on ``sys.path`` (mirroring the
identity/onboarding and identity/rbac conftests).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

_here = os.path.dirname(os.path.abspath(__file__))       # .../engine/memory/tests
_memory_root = os.path.dirname(_here)                     # .../engine/memory
_engine_root = os.path.dirname(_memory_root)              # .../engine
_repo_root = os.path.dirname(_engine_root)                # repo root
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import pytest  # noqa: E402

from engine.memory.embedding import BagOfWordsEmbedder  # noqa: E402
from engine.memory.store import InMemoryStore  # noqa: E402

T1 = "tenant-acme"
T2 = "tenant-globex"
AGENT_A = "coder"
AGENT_B = "reviewer"
SESS_1 = "session-1"
SESS_2 = "session-2"


@pytest.fixture()
def store():
    """A fresh in-memory store per test."""
    return InMemoryStore()


@pytest.fixture()
def embedder():
    return BagOfWordsEmbedder()


@pytest.fixture()
def at():
    """Build pinned, timezone-aware UTC instants for deterministic TTL tests."""

    def _at(year, month=1, day=1, hour=0, minute=0, second=0):
        return datetime(year, month, day, hour, minute, second,
                        tzinfo=timezone.utc)

    return _at
