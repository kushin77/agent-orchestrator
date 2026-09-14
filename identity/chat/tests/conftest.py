"""Pytest bootstrap + shared fixtures for the identity/chat test suite.

``identity/`` has no ``__init__.py`` (a later identity-phase lane owns adding
one), so ``identity.chat`` is reached through the repo-root PEP-420 namespace:
this file puts the repo root - **three** levels above this directory - at the
front of ``sys.path``, mirroring the ``engine/memory`` conftest. Tests import
the fully-qualified path (``from identity.chat.credential import ...``).

There is deliberately no ``tests/__init__.py`` (house convention) and every
fixture here is a fixture, never a module constant imported by a sibling suite:
pytest shares the plain module name ``conftest`` across test directories.

``SIGNING_KEY`` is a test-only constant, not a secret: it exists for the
duration of a test process and is never read from a tracked file or from a
request. The fixtures that mint credentials always pass it explicitly, because
the production path has no default key.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))       # .../identity/chat/tests
_chat_root = os.path.dirname(_here)                       # .../identity/chat
_identity_root = os.path.dirname(_chat_root)              # .../identity
_repo_root = os.path.dirname(_identity_root)              # repo root
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import pytest  # noqa: E402

from engine.memory.store import InMemoryStore as ScopedMemoryStore  # noqa: E402
from identity.chat.binding import IdentityMap  # noqa: E402
from identity.chat.credential import mint_chat_credential  # noqa: E402
from identity.chat.isolation import ChatIsolation  # noqa: E402
from identity.sso.store import InMemoryStore as RevocationStore  # noqa: E402

#: Test-only signing key (32 deterministic bytes). Never a production secret.
SIGNING_KEY = bytes(range(32))

#: A pinned instant so mint/verify/expiry are deterministic.
NOW = 1_800_000_000

TENANT_A = "tenant-acme"
TENANT_B = "tenant-globex"
AGENT_A = "coder"
AGENT_B = "analyst"
CONVERSATION_1 = "conv-1"
CONVERSATION_2 = "conv-2"

#: Declared external identities (see identity-map.json).
EXTERNAL_ADA = "ada@acme.example"
EXTERNAL_OPS = "ops@acme.example"
EXTERNAL_GRACE = "grace@globex.example"
EXTERNAL_UNKNOWN = "mallory@evil.example"

CLIENT = "openwebui"


@pytest.fixture()
def signing_key() -> bytes:
    return SIGNING_KEY


@pytest.fixture()
def now() -> int:
    return NOW


@pytest.fixture()
def revocation_store() -> RevocationStore:
    """The merged jti deny list the chat path always consults."""
    return RevocationStore()


@pytest.fixture()
def identity_map() -> IdentityMap:
    """The declared map shipped with the package."""
    return IdentityMap.load()


@pytest.fixture()
def memory_store() -> ScopedMemoryStore:
    """A fresh scoped ``engine/memory`` store per test."""
    return ScopedMemoryStore()


@pytest.fixture()
def isolation(memory_store: ScopedMemoryStore) -> ChatIsolation:
    return ChatIsolation(memory_store)


@pytest.fixture()
def mint(signing_key: bytes):
    """Factory: mint a chat credential for an explicit scope."""

    def _mint(
        tenant_id: str = TENANT_A,
        agent_id: str = AGENT_A,
        conversation_id: str = CONVERSATION_1,
        *,
        role: str = "chat-user",
        subject: str = "user-1",
        ttl_seconds: int = 3600,
        at: int = NOW,
    ):
        return mint_chat_credential(
            tenant_id=tenant_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            signing_key=signing_key,
            role=role,
            subject=subject,
            ttl_seconds=ttl_seconds,
            now=at,
        )

    return _mint


@pytest.fixture()
def credential(mint):
    """A tenant-A credential for ``(coder, conv-1)``."""
    return mint(TENANT_A, AGENT_A, CONVERSATION_1)


@pytest.fixture()
def other_tenant_credential(mint):
    """A tenant-B credential for ``(analyst, conv-2)``."""
    return mint(TENANT_B, AGENT_B, CONVERSATION_2, subject="user-2")
