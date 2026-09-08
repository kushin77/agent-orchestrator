"""Isolation contract tests, including the negatives:

* tenant B can never read / delete / retrieve tenant A's memory;
* session 1 can never read session 2's memory (same tenant + agent);
* agent A can never read agent B's agent-scope memory;
* tenant-global memory IS visible to every agent/session in the tenant.
"""

from __future__ import annotations

import pytest

from engine.memory.model import MemoryIsolationError, MemoryScope
from engine.memory.retrieval import Retriever

from conftest import T1, T2, AGENT_A, AGENT_B, SESS_1, SESS_2


def _seed_tenant(store, tenant):
    """One tenant-global, one agent, one session memory per tenant."""
    store.put(tenant_id=tenant, scope=MemoryScope.TENANT, key="org",
              text="release cadence is every two weeks")
    store.put(tenant_id=tenant, scope=MemoryScope.AGENT, agent_id=AGENT_A,
              key="agent-note", text="coder prefers typed returns")
    store.put(tenant_id=tenant, scope=MemoryScope.SESSION, agent_id=AGENT_A,
              session_id=SESS_1, key="session-note",
              text="session one plan")


class TestCrossTenantIsolation:
    def test_cannot_get_another_tenant_memory(self, store):
        _seed_tenant(store, T1)
        entry = store.list_entries(tenant_id=T1)[0]
        with pytest.raises(MemoryIsolationError):
            store.get(entry.memory_id, tenant_id=T2,
                      agent_id=AGENT_A, session_id=SESS_1)

    def test_cannot_delete_another_tenant_memory(self, store):
        _seed_tenant(store, T1)
        entry = store.list_entries(tenant_id=T1)[0]
        with pytest.raises(MemoryIsolationError):
            store.delete(entry.memory_id, tenant_id=T2,
                         agent_id=AGENT_A, session_id=SESS_1)

    def test_cannot_erase_another_tenant_memory(self, store):
        _seed_tenant(store, T1)
        entry = store.list_entries(tenant_id=T1)[0]
        with pytest.raises(MemoryIsolationError):
            store.erase(entry.memory_id, tenant_id=T2)

    def test_retrieval_never_sees_another_tenant(self, store):
        _seed_tenant(store, T1)
        retriever = Retriever(store)
        # tenant T2 has no memory at all: a query that matches T1's content
        # must find nothing under T2's identity (no cross-tenant leak).
        result = retriever.search("release cadence", tenant_id=T2,
                                  agent_id=AGENT_A, session_id=SESS_1)
        assert result.hits == []
        assert result.evaluated == 0
        # but T1's own tenant can still retrieve it
        own = retriever.search("release cadence", tenant_id=T1)
        assert any(h.key == "org" for h in own.hits)

    def test_retrieval_returns_only_own_tenant(self, store):
        _seed_tenant(store, T1)
        _seed_tenant(store, T2)
        result = Retriever(store).search("release cadence", tenant_id=T2,
                                         min_score=0.0)
        assert result.hits
        assert all(h.tenant_id == T2 for h in result.hits)
        assert all(h.key != "session-note" or h.tenant_id == T2
                   for h in result.hits)

    def test_tenant_same_key_different_id(self, store):
        # hash/id check: identical key in two tenants must not collide.
        a = store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k",
                      text="acme secret")
        b = store.put(tenant_id=T2, scope=MemoryScope.TENANT, key="k",
                      text="globex secret")
        assert a.memory_id != b.memory_id

    def test_violations_counted(self, store):
        _seed_tenant(store, T1)
        entry = store.list_entries(tenant_id=T1)[0]
        for _ in range(2):
            with pytest.raises(MemoryIsolationError):
                store.get(entry.memory_id, tenant_id=T2)
        assert store.stats()["cross_scope_violations"] == 2


class TestSessionIsolation:
    def test_session_one_cannot_read_session_two(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="plan",
                  text="session one secret plan")
        entry = store.list_entries(tenant_id=T1)[0]
        with pytest.raises(MemoryIsolationError):
            store.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_A,
                      session_id=SESS_2)
        # the owning session reads it fine
        assert store.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_A,
                         session_id=SESS_1) is not None

    def test_session_two_retrieval_excludes_session_one(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="tenant wide policy")
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="agent-note", text="agent long term fact")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="s1",
                  text="session one private note")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_2, key="s2",
                  text="session two private note")

        retriever = Retriever(store)
        # min_score=0.0 isolates the SCOPE filter from the relevance filter:
        # every visible candidate is returned, every invisible one excluded.
        result = retriever.search("private note", tenant_id=T1,
                                  agent_id=AGENT_A, session_id=SESS_2,
                                  min_score=0.0)
        keys = {h.key for h in result.hits}
        assert "s2" in keys
        assert "s1" not in keys  # session one's memory is invisible here
        assert "agent-note" in keys
        assert "org" in keys
        assert result.scope_excluded >= 1  # s1 was excluded, not scored

    def test_session_read_requires_matching_agent(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="plan", text="a plan")
        entry = store.list_entries(tenant_id=T1)[0]
        # same session id but different agent -> not covered
        with pytest.raises(MemoryIsolationError):
            store.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_B,
                      session_id=SESS_1)


class TestAgentIsolation:
    def test_agent_a_cannot_read_agent_b(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="a-note", text="coder private note")
        entry = store.list_entries(tenant_id=T1)[0]
        with pytest.raises(MemoryIsolationError):
            store.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_B)

    def test_agent_retrieval_scoped_to_own_agent(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="a-note", text="coder style: typed returns")
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_B,
                  key="b-note", text="reviewer style: terse comments")
        result = Retriever(store).search("coder style", tenant_id=T1,
                                         agent_id=AGENT_A)
        keys = {h.key for h in result.hits}
        assert "a-note" in keys
        assert "b-note" not in keys

    def test_sessionless_caller_cannot_see_agent_memory(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="a-note", text="agent fact")
        result = Retriever(store).search("agent fact", tenant_id=T1,
                                         min_score=0.0)
        # no agent identity supplied -> agent-scope memories are excluded
        assert result.hits == []
        assert result.scope_excluded >= 1


class TestTenantGlobalVisibility:
    def test_tenant_global_visible_to_all_in_tenant(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="quarterly planning cadence")
        retriever = Retriever(store)
        for agent, session in [(AGENT_A, SESS_1), (AGENT_B, SESS_2),
                               (AGENT_A, None), (None, None)]:
            hits = retriever.search("planning cadence", tenant_id=T1,
                                    agent_id=agent, session_id=session).hits
            assert any(h.scope is MemoryScope.TENANT for h in hits)
