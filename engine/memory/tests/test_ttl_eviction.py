"""TTL expiry + per-scope capacity eviction (LRU and FIFO) tests."""

from __future__ import annotations

from engine.memory.lifecycle import EvictionPolicy, ScopePolicy, default_policies
from engine.memory.model import MemoryScope
from engine.memory.retrieval import Retriever
from engine.memory.store import InMemoryStore

from conftest import T1, AGENT_A, SESS_1


def _policies(overrides=None):
    policies = default_policies()
    if overrides:
        policies.update(overrides)
    return policies


class TestTTLExpiry:
    def test_get_returns_none_after_expiry(self, store, at):
        t0 = at(2026, 3, 1, 9, 0, 0)
        entry = store.put(tenant_id=T1, scope=MemoryScope.SESSION,
                          agent_id=AGENT_A, session_id=SESS_1,
                          key="temp", text="temporary note",
                          ttl_seconds=600, now=t0)
        assert store.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_A,
                         session_id=SESS_1, now=at(2026, 3, 1, 9, 5, 0))
        assert store.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_A,
                         session_id=SESS_1,
                         now=at(2026, 3, 1, 9, 15, 0)) is None

    def test_prune_expired_removes_only_expired(self, store, at):
        t0 = at(2026, 3, 1, 9, 0, 0)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="org fact", now=t0)
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="temp", text="temp",
                  ttl_seconds=60, now=t0)
        pruned = store.prune_expired(now=at(2026, 3, 1, 9, 10, 0))
        assert pruned == 1
        assert store.count(tenant_id=T1) == 1
        assert store.stats()["expired_removed"] == 1

    def test_expired_not_retrieved(self, store, at):
        t0 = at(2026, 3, 1, 9, 0, 0)
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="temp", text="deploy note",
                  ttl_seconds=60, now=t0)
        hits = Retriever(store).search("deploy note", tenant_id=T1,
                                       agent_id=AGENT_A, session_id=SESS_1,
                                       now=at(2026, 3, 1, 9, 10, 0)).hits
        assert hits == []

    def test_scope_default_ttls_applied(self):
        # tenant-global never expires by default; agent and session have
        # long/short defaults (default_policies).
        store = InMemoryStore()
        tenant_entry = store.put(tenant_id=T1, scope=MemoryScope.TENANT,
                                 key="org", text="x")
        agent_entry = store.put(tenant_id=T1, scope=MemoryScope.AGENT,
                                agent_id=AGENT_A, key="a", text="x")
        session_entry = store.put(tenant_id=T1, scope=MemoryScope.SESSION,
                                  agent_id=AGENT_A, session_id=SESS_1,
                                  key="s", text="x")
        defaults = default_policies()
        assert tenant_entry.ttl_seconds is None
        assert (agent_entry.ttl_seconds
                == defaults[MemoryScope.AGENT].default_ttl_seconds)
        assert (session_entry.ttl_seconds
                == defaults[MemoryScope.SESSION].default_ttl_seconds)

    def test_explicit_ttl_overrides_policy_default(self, store):
        entry = store.put(tenant_id=T1, scope=MemoryScope.SESSION,
                          agent_id=AGENT_A, session_id=SESS_1, key="s",
                          text="x", ttl_seconds=42)
        assert entry.ttl_seconds == 42


class TestCapacityEviction:
    def test_lru_evicts_least_recently_used(self, at):
        store = InMemoryStore(policies=_policies(
            {MemoryScope.TENANT: ScopePolicy(capacity=2)}))
        t0 = at(2026, 3, 1)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k1",
                  text="one", now=t0)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k2",
                  text="two", now=t0)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k3",
                  text="three", now=t0)
        # k1 evicted when k3 arrived (LRU head at capacity time)
        remaining = {e.key for e in store.list_entries(tenant_id=T1)}
        assert remaining == {"k2", "k3"}

        # access k2 (now most recent) then add k4 -> k3 evicted, k2 survives
        k2 = next(e for e in store.list_entries(tenant_id=T1)
                  if e.key == "k2")
        store.get(k2.memory_id, tenant_id=T1)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k4",
                  text="four", now=t0)
        remaining = {e.key for e in store.list_entries(tenant_id=T1)}
        assert remaining == {"k2", "k4"}
        assert store.stats()["evicted"] == 2

    def test_fifo_evicts_oldest_created(self, at):
        store = InMemoryStore(policies=_policies(
            {MemoryScope.TENANT: ScopePolicy(capacity=2)}),
            eviction=EvictionPolicy.FIFO)
        t0 = at(2026, 3, 1)
        for key, text in [("k1", "one"), ("k2", "two"), ("k3", "three")]:
            store.put(tenant_id=T1, scope=MemoryScope.TENANT, key=key,
                      text=text, now=t0)
        # k1 evicted by k3
        assert {e.key for e in store.list_entries(tenant_id=T1)} == {"k2",
                                                                     "k3"}
        # FIFO: reading k2 does not reorder; adding k4 evicts k2
        k2 = next(e for e in store.list_entries(tenant_id=T1)
                  if e.key == "k2")
        store.get(k2.memory_id, tenant_id=T1)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k4",
                  text="four", now=t0)
        assert {e.key for e in store.list_entries(tenant_id=T1)} == {"k3",
                                                                     "k4"}

    def test_eviction_scoped_per_container(self, at):
        # Two tenants each with capacity 2: one tenant's writes never evict
        # the other tenant's entries.
        store = InMemoryStore(policies=_policies(
            {MemoryScope.TENANT: ScopePolicy(capacity=2)}))
        t0 = at(2026, 3, 1)
        for i in range(3):
            store.put(tenant_id=T1, scope=MemoryScope.TENANT,
                      key=f"a{i}", text="x", now=t0)
        for i in range(3):
            store.put(tenant_id="tenant-other", scope=MemoryScope.TENANT,
                      key=f"b{i}", text="x", now=t0)
        assert store.count(tenant_id=T1) == 2
        assert store.count(tenant_id="tenant-other") == 2

    def test_upsert_does_not_grow_or_evict(self, store):
        store = InMemoryStore(policies=_policies(
            {MemoryScope.TENANT: ScopePolicy(capacity=2)}))
        for _ in range(5):
            store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k1",
                      text="same logical key updated")
        assert store.count(tenant_id=T1) == 1
        assert store.stats()["evicted"] == 0
