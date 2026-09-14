"""Retention and the data-subject surface: export determinism, purge, erasure."""

from __future__ import annotations

import pytest

from engine.conversation import ConversationNotFound, ConversationValidationError, TranscriptStore
from conftest import AGENT_A, T1


def _seed_memory(memory_store, conversation_id, key="fact-1"):
    from engine.memory.model import MemoryScope

    memory_store.put(
        tenant_id=T1, agent_id=AGENT_A, session_id=conversation_id,
        scope=MemoryScope.SESSION, key=key, text="a remembered fact",
    )
    return memory_store


def test_retention_is_measured_from_creation_not_activity(store, clock):
    """Using a conversation must not extend its declared window."""
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, retention_seconds=60)
    clock.advance(seconds=50)
    store.append_message(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                         role="user", content="keeps it alive?")
    clock.advance(seconds=20)  # 70s since creation, only 20s since the append
    assert store.expired() == [convo.conversation_id]


def test_nothing_is_expired_before_its_window(store, clock):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, retention_seconds=60)
    clock.advance(seconds=59)
    assert store.expired() == []
    assert store.get(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A) is convo


def test_per_conversation_retention_overrides_the_store_default(store, clock):
    short = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, retention_seconds=10)
    long = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    clock.advance(seconds=11)
    assert store.expired() == [short.conversation_id]
    assert long.conversation_id not in store.expired()


def test_a_non_positive_retention_is_refused(store):
    with pytest.raises(ConversationValidationError):
        TranscriptStore(retention_seconds=0)


def test_purge_removes_from_the_transcript_and_the_memory_container(store, memory_store, clock):
    """Scoped, not a tenant-wide wipe: the expired conversation's SESSION
    container goes, an unrelated session's memory stays."""
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, retention_seconds=10)
    store.memory_store = memory_store
    _seed_memory(memory_store, convo.conversation_id)
    _seed_memory(memory_store, "another-session", key="unrelated")

    clock.advance(seconds=11)
    report = store.purge_expired()

    assert report.expired == (convo.conversation_id,)
    assert report.purged == (convo.conversation_id,)
    assert report.memory_erased == 1, "the SESSION container must be erased with the transcript"
    assert memory_store.list_entries(tenant_id=T1, session_id=convo.conversation_id) == []
    assert len(memory_store.list_entries(tenant_id=T1, session_id="another-session")) == 1, (
        "an erasure must not reach beyond the conversation it was asked about"
    )
    with pytest.raises(ConversationNotFound):
        store.get(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, include_deleted=True)


def test_purge_is_idempotent(store, memory_store, clock):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, retention_seconds=10)
    store.memory_store = memory_store
    _seed_memory(memory_store, convo.conversation_id)
    clock.advance(seconds=11)
    first = store.purge_expired()
    second = store.purge_expired()
    assert first.purged == (convo.conversation_id,)
    assert second.purged == ()


def test_forget_dry_run_reports_without_touching(store, memory_store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    store.append_message(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                         role="user", content="q")
    store.memory_store = memory_store
    _seed_memory(memory_store, convo.conversation_id)

    report = store.forget(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, dry_run=True)
    assert report.dry_run is True
    assert report.matched_messages == 1
    assert report.memory_erased == 1, "the container count is measured, not estimated"
    assert store.get(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A) is convo
    assert memory_store.count(T1) == 1


def test_forget_erases_both_halves(store, memory_store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    store.append_message(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                         role="user", content="q")
    store.memory_store = memory_store
    _seed_memory(memory_store, convo.conversation_id)

    report = store.forget(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    assert report.erased_conversation is True and report.memory_erased == 1
    assert memory_store.count(T1) == 0
    with pytest.raises(ConversationNotFound):
        store.export(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)


def test_export_before_erasure_is_a_complete_snapshot(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, title="keep me")
    store.append_message(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                         role="helper", content="x") if False else None
    store.append_message(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                         role="user", content="q1")
    store.append_message(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                         role="assistant", content="a1")
    payload = store.export(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    assert payload["conversation"]["title"] == "keep me"
    assert payload["conversation"]["message_count"] == 2
    assert [m["role"] for m in payload["conversation"]["messages"]] == ["user", "assistant"]
