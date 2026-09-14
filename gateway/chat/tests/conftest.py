"""Pytest bootstrap + fixtures for the ``gateway/chat`` suite (issue #503).

``gateway/`` carries no ``__init__.py``, so this file makes **both** import
styles work, which is exactly what the sibling gateway packages need:

* the repository root is prepended, so ``gateway.chat`` (and the fully
  qualified ``identity.chat`` / ``telemetry.chat`` / ``guardrails.chat`` /
  ``gateway.mcp`` this lane consumes) resolve;
* ``gateway/`` is prepended, so the proxy's own packages keep their plain names
  (``from proxy import ...`` in ``gateway/proxy``, ``from providers import ...``
  in the provider registry, ``from mcp import ...`` in ``gateway/mcp``);
* ``guardrails/`` is prepended for the DLP modules the guardrails chat package
  consumes as ``dlp.*``.

This directory is put on ``sys.path`` too, so the shared builders live in the
uniquely-named :mod:`chat_fixtures` (a generic name such as ``support`` would
collide with a neighbouring lane's helper module in a combined run).  Kept free
of sibling constants — the plain module name ``conftest`` is shared across test
directories when suites run together — and no secret is a literal: the test
signing key is 32 deterministic bytes and the ledger key material is derived at
runtime from the tenant name.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_GATEWAY_ROOT = _HERE.parents[2]  # gateway/
_REPO_ROOT = _HERE.parents[3]  # repository root
_GUARDRAILS_ROOT = _REPO_ROOT / "guardrails"

for _path in (_HERE, _GUARDRAILS_ROOT, _GATEWAY_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pytest  # noqa: E402

from chat_fixtures import (  # noqa: E402
    AGENT,
    CONVERSATION,
    OTHER_TENANT,
    SIGNING_KEY,
    TENANT,
    derived_key,
    grounding_block,
    turn_body,
    write_registry,
)

from gateway.chat.conversation import ConversationStore  # noqa: E402
from gateway.chat.resolver import ChatTaskResolver  # noqa: E402
from gateway.chat.surface import ChatSurface  # noqa: E402
from gateway.chat.wiring import build_gateway  # noqa: E402


@pytest.fixture
def registry_off(tmp_path: Path) -> Path:
    return write_registry(tmp_path / "off.yaml", "off")


@pytest.fixture
def registry_on(tmp_path: Path) -> Path:
    return write_registry(tmp_path / "on.yaml", "on")


@pytest.fixture
def signing_key() -> bytes:
    return SIGNING_KEY


@pytest.fixture
def revocation_store():
    from identity.sso.store import InMemoryStore

    return InMemoryStore()


@pytest.fixture
def mint(signing_key: bytes):
    """Mint a scoped chat credential for an explicit ``(tenant, agent, conversation)``."""
    from identity.chat.credential import mint_chat_credential

    def _mint(
        tenant_id: str = TENANT,
        agent_id: str = AGENT,
        conversation_id: str = CONVERSATION,
        *,
        role: str = "chat-user",
        subject: str = "user-503",
    ):
        return mint_chat_credential(
            tenant_id=tenant_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            signing_key=signing_key,
            role=role,
            subject=subject,
        )

    return _mint


@pytest.fixture
def credential(mint):
    return mint()


@pytest.fixture
def ledger():
    from telemetry.ledger import DictKeystore, KeyMaterial, open_ledger

    keystore = DictKeystore(
        {
            tenant: KeyMaterial(key=derived_key(tenant), key_id=f"ao503:{tenant}:v1")
            for tenant in (TENANT, OTHER_TENANT)
        }
    )
    return open_ledger(None, keystore=keystore)


@pytest.fixture
def usage_store():
    from telemetry.metering.store import MemoryUsageStore

    return MemoryUsageStore()


@pytest.fixture
def attributor(ledger, usage_store):
    from telemetry.chat.attribution import TurnAttributor

    return TurnAttributor(ledger, usage_store=usage_store)


@pytest.fixture
def budget_guard():
    """The shipped rails: kill switch OFF, budgets observe (the declared default)."""
    from telemetry.chat.budget_guard import TurnBudgetGuard

    return TurnBudgetGuard.from_config()


@pytest.fixture
def memory_store():
    from engine.memory.store import InMemoryStore

    return InMemoryStore()


class Rigged:
    """A wired surface plus the handles a test needs to script and inspect it.

    The call-record sinks are read back off the **gateway** (where this lane
    mounted the caller's handles), not off the proxy's ``WiredProxy``: the
    proxy's own builder uses a truthiness test for its sinks, and an empty
    ``ListCallRecordSink`` is falsy, so the ``WiredProxy`` handle can point at a
    fresh, empty sink while the dispatch records land in the caller's.
    """

    def __init__(self, surface: ChatSurface, wired, audit, metering) -> None:
        self.surface = surface
        self.wired = wired
        self.audit_sink = audit
        self.metering_sink = metering

    def script(self, content: str, *, provider: str = "deepseek", **kwargs):
        self.wired.rig.script_success(provider, content, **kwargs)
        return self

    def fail(self, error: Exception, *, provider: str = "deepseek"):
        self.wired.rig.fail(provider, error)
        return self

    @property
    def records(self) -> list:
        return list(self.audit_sink.records)

    @property
    def metered(self) -> list:
        return list(self.metering_sink.records)


@pytest.fixture
def rigged(
    registry_on,
    signing_key,
    revocation_store,
    memory_store,
    budget_guard,
    attributor,
):
    """The real surface over the real merged siblings, over an offline provider rig."""
    from proxy.sinks import ListCallRecordSink

    audit = ListCallRecordSink()
    metering = ListCallRecordSink()
    gateway, wired = build_gateway(audit_sink=audit, metering_sink=metering)
    surface = ChatSurface(
        gateway,
        registry_path=registry_on,
        signing_key=signing_key,
        revocation_store=revocation_store,
        conversation=ConversationStore(memory_store=memory_store),
        task_resolver=ChatTaskResolver(),
        budget_guard=budget_guard,
        attributor=attributor,
    )
    assert gateway.audit_sink is audit and gateway.metering_sink is metering
    return Rigged(surface, wired, audit, metering)


@pytest.fixture
def body():
    return turn_body()


@pytest.fixture
def grounding():
    return grounding_block()
