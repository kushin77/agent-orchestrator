"""Structural isolation: one container per conversation, credential must match.

The two counters are asserted separately on purpose. ``cross_scope_denials``
proves this layer refused; ``engine_cross_scope_violations`` - read back out of
``engine.memory``'s own ``stats()`` - proves the engine's guard ran underneath
and was not bypassed. A test that only checked the first could pass while the
engine's isolation had been short-circuited.
"""

from __future__ import annotations

import pytest

from engine.memory.model import MemoryIsolationError, MemoryScope, container_id
from engine.memory.store import InMemoryStore
from identity.chat.errors import ConversationScopeMismatch, InvalidScope
from identity.chat.isolation import (
    DENIAL_CONVERSATION_SCOPE,
    DENIAL_ENGINE_ISOLATION,
    ChatIsolation,
    conversation_container,
)

TENANT_A = "tenant-acme"
TENANT_B = "tenant-globex"
AGENT_A = "coder"
AGENT_B = "analyst"
CONVERSATION_1 = "conv-1"
CONVERSATION_2 = "conv-2"
CLIENT = "openwebui"


def test_the_container_is_the_engine_vocabulary_not_a_chat_invention(monkeypatch):
    """The chat surface delegates the name; it does not format one itself.

    Value equality is not enough on its own - a re-implementation that agreed
    today would drift silently. So the test also proves delegation: patching
    the imported ``container_id`` changes what the chat helper returns.
    """
    import identity.chat.isolation as isolation_module
    from engine.memory import model as engine_model

    assert isolation_module.container_id is engine_model.container_id
    expected = container_id(MemoryScope.SESSION, TENANT_A, AGENT_A, CONVERSATION_1)
    assert conversation_container(TENANT_A, AGENT_A, CONVERSATION_1) == expected
    assert expected == f"session:{TENANT_A}:{AGENT_A}:{CONVERSATION_1}"

    monkeypatch.setattr(
        isolation_module, "container_id", lambda *args, **kwargs: "SENTINEL"
    )
    assert conversation_container(TENANT_A, AGENT_A, CONVERSATION_1) == "SENTINEL"


def test_the_credential_container_matches_the_engine_container(credential, isolation):
    assert credential.container_id == container_id(
        MemoryScope.SESSION, TENANT_A, AGENT_A, CONVERSATION_1
    )
    assert isolation.container_for(credential) == credential.container_id


def test_a_write_then_read_round_trip_stays_inside_one_container(credential, isolation):
    """Control for every refusal below: the correct scope does work."""
    entry = isolation.write(credential, key="turn-1", text="hello there")
    landed = container_id(
        entry.scope, entry.tenant_id, entry.agent_id, entry.session_id
    )
    assert landed == credential.container_id
    assert isolation.read(credential, memory_id=entry.memory_id).text == "hello there"
    assert isolation.cross_scope_denials == 0
    assert isolation.engine_cross_scope_violations == 0


def test_a_credential_naming_a_foreign_container_is_refused_and_counted(
    credential, isolation
):
    """The credential's scope must match the container it names."""
    foreign = conversation_container(TENANT_A, AGENT_A, CONVERSATION_2)
    before = isolation.cross_scope_denials
    with pytest.raises(ConversationScopeMismatch) as caught:
        isolation.read(
            credential, memory_id="irrelevant", requested_container=foreign
        )
    assert caught.value.code == "conversation_scope_mismatch"
    assert caught.value.details["expectedContainer"] == credential.container_id
    assert isolation.cross_scope_denials == before + 1
    assert isolation.denials[DENIAL_CONVERSATION_SCOPE] == 1


def test_a_write_naming_a_foreign_container_is_refused_and_counted(
    credential, isolation
):
    foreign = conversation_container(TENANT_B, AGENT_B, CONVERSATION_2)
    with pytest.raises(ConversationScopeMismatch):
        isolation.write(
            credential, key="turn-1", text="x", requested_container=foreign
        )
    assert isolation.cross_scope_denials == 1
    assert isolation.store.count() == 0


def test_reading_another_tenants_entry_cannot_bypass_the_engine(credential, isolation):
    """The chat path's refusal is *the engine's* refusal, re-raised chained."""
    other_tenant_entry = isolation.store.put(
        tenant_id=TENANT_B,
        agent_id=AGENT_B,
        session_id=CONVERSATION_2,
        scope=MemoryScope.SESSION,
        key="turn-1",
        text="globex confidential",
    )
    assert isolation.store.count(TENANT_B) == 1

    with pytest.raises(ConversationScopeMismatch) as caught:
        isolation.read(credential, memory_id=other_tenant_entry.memory_id)

    assert isinstance(caught.value.__cause__, MemoryIsolationError)
    assert isolation.cross_scope_denials == 1
    assert isolation.denials[DENIAL_ENGINE_ISOLATION] == 1
    assert isolation.engine_cross_scope_violations == 1


def test_the_engine_still_refuses_when_the_chat_path_is_bypassed(isolation):
    """Independent proof the isolation is the store's, not this wrapper's."""
    entry = isolation.store.put(
        tenant_id=TENANT_B,
        agent_id=AGENT_B,
        session_id=CONVERSATION_2,
        scope=MemoryScope.SESSION,
        key="turn-1",
        text="globex confidential",
    )
    with pytest.raises(MemoryIsolationError):
        isolation.store.get(
            entry.memory_id,
            tenant_id=TENANT_A,
            agent_id=AGENT_A,
            session_id=CONVERSATION_1,
        )
    assert isolation.engine_cross_scope_violations == 1
    # Control: the owning tenant can read it, so the entry is genuinely there.
    assert isolation.store.get(
        entry.memory_id,
        tenant_id=TENANT_B,
        agent_id=AGENT_B,
        session_id=CONVERSATION_2,
    ).text == "globex confidential"


def test_one_conversation_cannot_read_another_conversations_container(
    credential, isolation, mint
):
    """Same tenant and agent, different conversation: still a refusal."""
    sibling = mint(TENANT_A, AGENT_A, CONVERSATION_2, subject="user-1")
    entry = isolation.write(sibling, key="turn-1", text="other conversation")
    with pytest.raises(ConversationScopeMismatch):
        isolation.read(credential, memory_id=entry.memory_id)
    assert isolation.cross_scope_denials == 1
    assert isolation.engine_cross_scope_violations == 1
    # Control: the sibling conversation reads its own entry.
    assert isolation.read(sibling, memory_id=entry.memory_id).text == (
        "other conversation"
    )


def test_a_ref_of_addresses_the_credentials_own_container(credential, isolation):
    ref = isolation.ref_of(credential, key="turn-1")
    assert ref.container_id == credential.container_id
    assert isolation.write(credential, key="turn-1", text="hi").memory_id == (
        ref.memory_id
    )


def test_counters_start_at_zero_and_report_both_layers(memory_store):
    fresh = ChatIsolation(memory_store)
    assert fresh.cross_scope_denials == 0
    assert fresh.denials == {}
    assert fresh.engine_cross_scope_violations == 0


def test_an_unaddressable_read_is_refused(credential, isolation):
    """An empty memory id is a bad request, not a silent any-entry read."""
    with pytest.raises(InvalidScope):
        isolation.read(credential, memory_id="")
    assert isolation.cross_scope_denials == 0


def test_a_separate_store_yields_a_separate_isolation(credential):
    """Nothing is shared between two isolations (no process-global scope state)."""
    first = ChatIsolation(InMemoryStore())
    second = ChatIsolation(InMemoryStore())
    first.write(credential, key="turn-1", text="hello")
    assert second.cross_scope_denials == 0
    assert second.store.count() == 0
