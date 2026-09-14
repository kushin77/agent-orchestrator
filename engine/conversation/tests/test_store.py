"""The transcript store: the object, the ordered log, one stable shape."""

from __future__ import annotations

import pytest

from engine.conversation import (
    ROLES,
    SCHEMA_VERSION,
    Conversation,
    ConversationValidationError,
    Message,
)
from conftest import AGENT_A, T1


def test_create_returns_a_scoped_conversation(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, title="planning")
    assert convo.tenant_id == T1 and convo.agent_id == AGENT_A
    assert convo.title == "planning"
    assert convo.messages == []
    assert convo.deleted_at is None
    assert store.stats()["created"] == 1


def test_messages_are_ordered_and_numbered(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    first = store.append_message(
        convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="user", content="one"
    )
    second = store.append_message(
        convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="assistant", content="two"
    )
    assert [m.content for m in convo.messages] == ["one", "two"]
    assert first.message_id.endswith("m0001") and second.message_id.endswith("m0002")
    assert store.stats()["messages_appended"] == 2


def test_role_is_a_closed_set(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    with pytest.raises(ConversationValidationError):
        store.append_message(
            convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
            role="wizard", content="nope",
        )
    assert "wizard" not in ROLES


def test_citations_are_consumed_from_the_upstream_envelope(store):
    """The #504 envelope is consumed, not re-derived: unknown keys survive."""
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    message = store.append_message(
        convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="assistant",
        content="grounded", citations=[{"source_id": "s-1", "title": "Handbook", "score": "0.91"}],
    )
    assert message.citations[0].source_id == "s-1"
    assert dict(message.citations[0].extra)["score"] == "0.91"


def test_citation_without_a_source_id_is_refused(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    with pytest.raises(ConversationValidationError):
        store.append_message(
            convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="assistant",
            content="ungrounded", citations=[{"title": "no id here"}],
        )


def test_message_round_trips_through_the_stable_shape():
    message = Message(
        message_id="conv:m0001", role="assistant", content="hi", created_at="2026-09-14T12:00:00Z",
        model="deepseek-v4", tier="HIGH", tokens=12, cost_ref="telemetry:42",
        unsupported_claims=("c9",),
    )
    assert Message.from_dict(message.to_dict()) == message


def test_conversation_round_trips_through_the_stable_shape(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, title="t")
    store.append_message(
        convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="user", content="q"
    )
    rebuilt = Conversation.from_dict(convo.to_dict())
    assert rebuilt.to_dict() == convo.to_dict()


def test_snapshot_and_restore_preserve_state(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    store.append_message(
        convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="user", content="q"
    )
    snap = store.snapshot()
    assert snap["schema"] == SCHEMA_VERSION
    other = type(store)()
    other.restore(snap)
    assert other.snapshot() == snap


def test_restore_refuses_an_unknown_schema(store):
    with pytest.raises(ConversationValidationError):
        store.restore({"schema": "conversation.transcript/v99", "conversations": []})


def test_export_is_deterministic(store):
    """Two exports of identical state are byte-identical (audit-friendly)."""
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    store.append_message(
        convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="user", content="q"
    )
    first = store.export_json(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    second = store.export_json(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    assert first == second
    assert "messages" in first and SCHEMA_VERSION in first


def test_append_to_a_deleted_conversation_is_refused(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    store.delete(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    with pytest.raises(ConversationValidationError):
        store.append_message(
            convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="user", content="late"
        )
