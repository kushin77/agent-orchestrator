"""The conversation primitive: one ``engine/memory`` container per conversation.

A conversation is bound to the credential's scope — ``session:<tenant>:<agent>:
<conversation>`` as ``engine.memory.model.container_id`` spells it — and every
read and write goes through ``identity/chat``'s isolation onto that container.
These tests hold the binding from both sides: the right credential can read what
it wrote, another conversation's credential cannot, and a request that *claims*
a different container is refused rather than trusted.
"""

from __future__ import annotations

import pytest

from chat_fixtures import (
    AGENT,
    CONVERSATION,
    OTHER_CONVERSATION,
    TENANT,
    grounded_payload,
    token_of,
    with_grounding,
)

from gateway.chat.conversation import ConversationStore, replay_messages
from gateway.chat.errors import ChatSurfaceError


def refusal_of(callable_, *args, **kwargs) -> ChatSurfaceError:
    with pytest.raises(ChatSurfaceError) as raised:
        callable_(*args, **kwargs)
    return raised.value


def test_the_turn_lands_in_the_credentials_container(rigged, body, credential, grounding):
    from engine.memory.model import MemoryScope, container_id

    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    expected = container_id(MemoryScope.SESSION, TENANT, AGENT, CONVERSATION)
    assert response["ao"]["container"] == expected
    assert expected == f"session:{TENANT}:{AGENT}:{CONVERSATION}"
    assert response["ao"]["conversationKey"] == "turn:000001"


def test_the_conversation_history_is_readable_by_its_own_credential(
    rigged, body, credential, grounding
):
    rigged.script(grounded_payload())
    rigged.surface.completions(with_grounding(body, grounding), token=token_of(credential))
    history = rigged.surface.conversation.history(credential)
    assert len(history) == 1
    assert history[0]["turn_id"]
    assert history[0]["answer"].startswith("The ingest worker")
    assert history[0]["messages"][0]["role"] == "user"
    # the replay a client would get is role + text only, never derived fields
    assert replay_messages(history)[0]["content"].startswith("What happened")


def test_another_conversation_cannot_read_this_one(rigged, body, credential, grounding, mint):
    """A different conversation is a different container: its history is empty,
    and asking for the first one's container is refused by the isolation layer."""
    from identity.chat.errors import ConversationScopeMismatch

    rigged.script(grounded_payload())
    rigged.surface.completions(with_grounding(body, grounding), token=token_of(credential))
    other = mint(conversation_id=OTHER_CONVERSATION)
    assert rigged.surface.conversation.history(other) == []
    # and the store refuses to address the first conversation's container
    with pytest.raises(ConversationScopeMismatch) as raised:
        rigged.surface.conversation.history(
            other, requested_container=f"session:{TENANT}:{AGENT}:{CONVERSATION}"
        )
    assert "container" in str(raised.value)


def test_a_serialized_conversation_is_written_to_the_engine_store(
    rigged, body, credential, grounding, memory_store
):
    """The record really is an ``engine/memory`` entry, not this lane's own file."""
    rigged.script(grounded_payload())
    rigged.surface.completions(with_grounding(body, grounding), token=token_of(credential))
    entries = [
        entry
        for entry in memory_store._by_id.values()  # the store's own entries
        if entry.session_id == CONVERSATION
    ]
    assert len(entries) == 1
    assert entries[0].scope.value == "session"
    assert entries[0].tenant_id == TENANT
    assert entries[0].agent_id == AGENT
    assert entries[0].metadata["kind"] == "chat-turn"


def test_the_store_refuses_to_grow_without_a_bound(
    rigged, body, credential, grounding, memory_store
):
    """A bound is a bound: the store refuses rather than growing unbounded."""
    store = ConversationStore(memory_store=memory_store, max_turns=1)
    rigged.script(grounded_payload())
    rigged.surface.completions(with_grounding(body, grounding), token=token_of(credential))
    assert len(store.history(credential)) == 1
    with pytest.raises(Exception) as raised:
        store.append(credential, {"turn_id": "turn-2"})
    assert "cap" in str(raised.value)
