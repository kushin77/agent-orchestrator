"""GDPR-ready forget/export tests (tenant data-subject rights)."""

from __future__ import annotations

from engine.memory.gdpr import export_memory, forget
from engine.memory.model import MemoryScope

from conftest import T1, T2, AGENT_A, AGENT_B, SESS_1, SESS_2


def _seed(store):
    store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
              text="org memory acme")
    store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
              key="a", text="agent a memory")
    store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_B,
              key="b", text="agent b memory")
    store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
              session_id=SESS_1, key="s1", text="session one memory")
    store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
              session_id=SESS_2, key="s2", text="session two memory")
    store.put(tenant_id=T2, scope=MemoryScope.TENANT, key="org",
              text="org memory globex")


class TestExport:
    def test_export_all_tenant_entries(self, store):
        _seed(store)
        payload = export_memory(store, tenant_id=T1)
        assert payload["tenant_id"] == T1
        assert payload["count"] == 5
        assert {e["key"] for e in payload["entries"]} == {
            "org", "a", "b", "s1", "s2",
        }

    def test_export_is_tenant_scoped(self, store):
        _seed(store)
        payload = export_memory(store, tenant_id=T1)
        assert all(e["tenant_id"] == T1 for e in payload["entries"])
        assert not any(e["tenant_id"] == T2 for e in payload["entries"])

    def test_export_filters(self, store):
        _seed(store)
        agent = export_memory(store, tenant_id=T1, agent_id=AGENT_A)
        assert {e["key"] for e in agent["entries"]} == {"a", "s1", "s2"}
        session = export_memory(store, tenant_id=T1, agent_id=AGENT_A,
                                session_id=SESS_1)
        assert {e["key"] for e in session["entries"]} == {"s1"}
        tenant_only = export_memory(store, tenant_id=T1,
                                    scope=MemoryScope.TENANT)
        assert {e["key"] for e in tenant_only["entries"]} == {"org"}

    def test_export_is_deterministic(self, store):
        _seed(store)
        first = export_memory(store, tenant_id=T1)
        second = export_memory(store, tenant_id=T1)
        assert first == second


class TestForget:
    def test_forget_deletes_matches(self, store):
        _seed(store)
        report = forget(store, tenant_id=T1, agent_id=AGENT_A)
        assert report.matched == 3
        assert report.deleted == 3
        assert report.dry_run is False
        remaining = export_memory(store, tenant_id=T1)
        assert {e["key"] for e in remaining["entries"]} == {"org", "b"}

    def test_forget_dry_run_deletes_nothing(self, store):
        _seed(store)
        report = forget(store, tenant_id=T1, agent_id=AGENT_A, dry_run=True)
        assert report.matched == 3
        assert report.deleted == 0
        assert store.count(tenant_id=T1) == 5

    def test_forget_session_only(self, store):
        _seed(store)
        report = forget(store, tenant_id=T1, agent_id=AGENT_A,
                        session_id=SESS_1)
        assert report.matched == 1
        remaining = export_memory(store, tenant_id=T1)
        assert {e["key"] for e in remaining["entries"]} == {
            "org", "a", "b", "s2",
        }

    def test_forget_never_crosses_tenants(self, store):
        _seed(store)
        report = forget(store, tenant_id=T2)
        assert report.matched == 1
        assert report.deleted == 1
        # tenant one untouched
        assert store.count(tenant_id=T1) == 5

    def test_forget_older_than(self, store, at):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="old",
                  text="old fact", created_at=at(2025, 1, 1).isoformat())
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="new",
                  text="new fact", created_at=at(2026, 1, 1).isoformat())
        report = forget(store, tenant_id=T1,
                        older_than=at(2025, 6, 1).isoformat())
        assert report.matched == 1
        remaining = export_memory(store, tenant_id=T1)
        assert {e["key"] for e in remaining["entries"]} == {"new"}

    def test_forget_scope_filter(self, store):
        _seed(store)
        report = forget(store, tenant_id=T1, scope=MemoryScope.SESSION)
        assert report.matched == 2
        remaining = export_memory(store, tenant_id=T1)
        assert {e["key"] for e in remaining["entries"]} == {"org", "a", "b"}


def test_put_accepts_created_at_override(store, at):
    # used by forget(older_than=...) tests above
    entry = store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="k",
                      text="x", created_at=at(2025, 1, 1).isoformat())
    assert entry.created_at.startswith("2025")
