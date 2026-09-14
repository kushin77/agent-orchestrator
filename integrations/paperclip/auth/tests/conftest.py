"""Pytest bootstrap for the paperclip cross-boundary auth tests (issue #412).

Puts the repo root on ``sys.path`` so the tests import the auth package by its
real path (``integrations.paperclip.auth``) regardless of where pytest runs, and
shares the fixtures every module needs. The signing key fixture is an
obviously-fake placeholder: no real key material exists anywhere in this repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: A fixed clock (epoch seconds) so every test is deterministic.
NOW = 1_700_000_000

#: A fleet company/tenant scope.
COMPANY = "purebliss"


@pytest.fixture()
def root() -> Path:
    return ROOT


@pytest.fixture()
def now() -> int:
    return NOW


@pytest.fixture()
def company() -> str:
    return COMPANY


@pytest.fixture()
def key() -> str:
    """An obviously-fake placeholder signing key (never a real credential)."""
    return "placeholder-signing-key-not-a-real-credential"


@pytest.fixture()
def session_record() -> dict:
    """A fleet session record in ``SessionIdentity.to_json()`` shape."""
    return {
        "session_id": "sess-operator-1",
        "issue": 412,
        "agent_id": "operator",
        "lane": "ops",
        "branch": "issue-412-auth",
        "worktree": "/tmp/ao412",
        "roles": ["operator"],
    }
