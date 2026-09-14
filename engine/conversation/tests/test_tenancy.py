"""Tenancy: a transcript is reachable only by its exact scope, and a probe is counted."""

from __future__ import annotations

import pytest

from engine.conversation import (
    ConversationIsolationError,
    ConversationNotFound,
    TranscriptStore,
)
from conftest import AGENT_A, AGENT_B, T1, T2


@pytest.fixture()
def acme(store):
    """One conversation owned by (T1, AGENT_A)."""
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, title="acme planning")
    store.append_message(
        convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
        role="user", content="the acme secret is 42",
    )
    return convo


def test_exact_scope_is_readable(store, acme):
    assert store.get(acme.conversation_id, tenant_id=T1, agent_id=AGENT_A) is acme


def test_cross_tenant_read_raises_and_is_counted(store, acme):
    before = store.stats()["isolation_violations"]
    with pytest.raises(ConversationIsolationError):
        store.get(acme.conversation_id, tenant_id=T2, agent_id=AGENT_A)
    assert store.stats()["isolation_violations"] == before + 1, (
        "a refused cross-tenant read must be visible in the counters, not merely refused"
    )


def test_cross_agent_read_raises_and_is_counted(store, acme):
    before = store.stats()["isolation_violations"]
    with pytest.raises(ConversationIsolationError):
        store.get(acme.conversation_id, tenant_id=T1, agent_id=AGENT_B)
    assert store.stats()["isolation_violations"] == before + 1


def test_unknown_conversation_is_a_different_error(store):
    """Absent and forbidden must not collapse into one answer."""
    with pytest.raises(ConversationNotFound):
        store.get("conv-does-not-exist", tenant_id=T1, agent_id=AGENT_A)
    assert store.stats()["isolation_violations"] == 0


def test_isolation_refusal_does_not_leak_content(store, acme):
    with pytest.raises(ConversationIsolationError) as caught:
        store.get(acme.conversation_id, tenant_id=T2, agent_id=AGENT_A)
    message = str(caught.value)
    assert "42" not in message, "the refusal must name the scope, never the content"
    assert T2 in message or acme.conversation_id in message


def test_writes_are_scope_checked_too(store, acme):
    with pytest.raises(ConversationIsolationError):
        store.append_message(
            acme.conversation_id, tenant_id=T2, agent_id=AGENT_A, role="user", content="x"
        )
    with pytest.raises(ConversationIsolationError):
        store.rename(acme.conversation_id, tenant_id=T2, agent_id=AGENT_A, title="stolen")


def test_listing_is_tenant_scoped(store, acme):
    store.create_conversation(tenant_id=T2, agent_id=AGENT_A, title="globex planning")
    acme_list = store.list_conversations(tenant_id=T1)
    globex_list = store.list_conversations(tenant_id=T2)
    assert [c["conversation_id"] for c in acme_list["conversations"]] == [acme.conversation_id]
    assert len(globex_list["conversations"]) == 1
    assert globex_list["conversations"][0]["conversation_id"] != acme.conversation_id


def test_search_is_tenant_scoped(store, acme):
    store.create_conversation(tenant_id=T2, agent_id=AGENT_A, title="acme planning for globex")
    assert [c.conversation_id for c in store.search("acme", tenant_id=T1)] == [acme.conversation_id]
    assert store.search("secret", tenant_id=T2) == []


def test_erase_is_tenant_scoped(store, acme):
    with pytest.raises(ConversationIsolationError):
        store.forget(acme.conversation_id, tenant_id=T2, agent_id=AGENT_A)
    assert store.get(acme.conversation_id, tenant_id=T1, agent_id=AGENT_A) is acme


def test_a_fresh_store_shares_nothing(store, acme):
    other = TranscriptStore()
    assert (other.list_conversations(tenant_id=T1)["total"]) == 0
