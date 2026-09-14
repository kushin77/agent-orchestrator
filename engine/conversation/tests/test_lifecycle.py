"""Lifecycle: list / search / rename / pin / delete, and soft vs hard delete."""

from __future__ import annotations

import pytest

from engine.conversation import ConversationNotFound, ConversationValidationError
from conftest import AGENT_A, T1


def _three(store):
    made = []
    for n in range(1, 6):
        convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A, title=f"c{n}")
        store.append_message(
            convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, role="user", content=f"body {n}"
        )
        made.append(convo)
    return made


def test_list_pages_with_a_cursor_without_gaps_or_repeats(store):
    made = _three(store)
    seen, cursor, pages = [], None, 0
    while True:
        page = store.list_conversations(tenant_id=T1, limit=2, cursor=cursor)
        seen.extend(c["conversation_id"] for c in page["conversations"])
        pages += 1
        cursor = page["cursor"]
        if not cursor:
            break
    assert pages == 3
    assert seen == [c.conversation_id for c in made]
    assert len(seen) == len(set(seen))


def test_list_rejects_a_non_positive_limit(store):
    with pytest.raises(ConversationValidationError):
        store.list_conversations(tenant_id=T1, limit=0)


def test_search_matches_title_and_content(store):
    made = _three(store)
    by_title = store.search("c3", tenant_id=T1)
    assert [c.conversation_id for c in by_title] == [made[2].conversation_id]
    by_body = store.search("body 4", tenant_id=T1)
    assert [c.conversation_id for c in by_body] == [made[3].conversation_id]


def test_search_refuses_an_empty_query(store):
    with pytest.raises(ConversationValidationError):
        store.search("   ", tenant_id=T1)


def test_rename_and_pin(store):
    convo = store.create_conversation(tenant_id=T1, agent_id=AGENT_A)
    store.rename(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, title="renamed")
    store.pin(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    assert (convo.title, convo.pinned) == ("renamed", True)
    store.pin(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, pinned=False)
    assert convo.pinned is False
    assert store.stats()["renamed"] == 1


def test_soft_delete_marks_rather_than_erases(store):
    convo = _three(store)[0]
    report = store.delete(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    assert report.erased_conversation is False
    assert convo.deleted_at is not None
    # hidden from the default listing ...
    assert convo.conversation_id not in [
        c["conversation_id"] for c in store.list_conversations(tenant_id=T1)["conversations"]
    ]
    # ... but still present, and readable when asked for explicitly
    listed = store.list_conversations(tenant_id=T1, include_deleted=True)["conversations"]
    assert convo.conversation_id in [c["conversation_id"] for c in listed]
    assert store.get(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                     include_deleted=True) is convo


def test_soft_deleted_is_not_readable_by_default(store):
    convo = _three(store)[0]
    store.delete(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)
    with pytest.raises(ConversationNotFound):
        store.get(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A)


def test_hard_delete_erases(store):
    convo = _three(store)[0]
    report = store.delete(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, hard=True)
    assert report.erased_conversation is True
    assert report.matched_messages == 1
    with pytest.raises(ConversationNotFound):
        store.get(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A, include_deleted=True)
    assert store.stats()["hard_deleted"] == 1


def test_delete_dry_run_touches_nothing(store):
    convo = _three(store)[0]
    report = store.delete(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A,
                          hard=True, dry_run=True)
    assert report.dry_run is True and report.matched_messages == 1
    assert report.erased_conversation is True  # what WOULD happen
    assert store.get(convo.conversation_id, tenant_id=T1, agent_id=AGENT_A) is convo
    assert store.stats()["hard_deleted"] == 0
