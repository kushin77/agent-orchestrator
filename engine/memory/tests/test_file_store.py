"""FileStore (JSON persistence) tests: roundtrip, determinism, isolation."""

from __future__ import annotations

import json

import pytest

from engine.memory.model import MemoryIsolationError, MemoryScope
from engine.memory.store import FileStore

from conftest import T1, T2, AGENT_A, SESS_1


@pytest.fixture()
def memfile(tmp_path):
    return tmp_path / "memory.json"


def _seed(store):
    store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
              text="acme org fact")
    store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
              key="a", text="acme agent fact")
    store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
              session_id=SESS_1, key="s", text="acme session fact")
    store.put(tenant_id=T2, scope=MemoryScope.TENANT, key="org",
              text="globex org fact")


class TestFileStoreRoundtrip:
    def test_reload_restores_entries(self, memfile):
        store = FileStore(memfile)
        _seed(store)
        reloaded = FileStore(memfile)
        assert reloaded.count(tenant_id=T1) == 3
        assert reloaded.count(tenant_id=T2) == 1
        keys = {e.key for e in reloaded.list_entries(tenant_id=T1)}
        assert keys == {"org", "a", "s"}

    def test_reload_restores_full_entry_content(self, memfile):
        original = FileStore(memfile)
        entry = original.put(tenant_id=T1, scope=MemoryScope.SESSION,
                             agent_id=AGENT_A, session_id=SESS_1,
                             key="plan", text="ship on friday",
                             ttl_seconds=3600)
        reloaded = FileStore(memfile)
        got = reloaded.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_A,
                           session_id=SESS_1)
        assert got is not None
        assert got.text == "ship on friday"
        assert got.ttl_seconds == 3600
        assert got.scope is MemoryScope.SESSION
        assert got.embedding is not None

    def test_isolation_survives_reload(self, memfile):
        store = FileStore(memfile)
        _seed(store)
        reloaded = FileStore(memfile)
        entry = next(e for e in reloaded.list_entries(tenant_id=T1)
                     if e.scope is MemoryScope.SESSION)
        with pytest.raises(MemoryIsolationError):
            reloaded.get(entry.memory_id, tenant_id=T2)
        with pytest.raises(MemoryIsolationError):
            reloaded.get(entry.memory_id, tenant_id=T1, agent_id=AGENT_A,
                         session_id="other-session")

    def test_mutations_persist_between_instances(self, memfile):
        store = FileStore(memfile)
        _seed(store)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="extra",
                  text="added later")
        reloaded = FileStore(memfile)
        assert reloaded.count(tenant_id=T1) == 4


class TestFileStoreDeterminism:
    def test_file_is_deterministic_json(self, memfile):
        store = FileStore(memfile)
        _seed(store)
        with memfile.open(encoding="utf-8") as handle:
            data = json.load(handle)
        assert data["format"] == 1
        ids = [e["memory_id"] for e in data["entries"]]
        assert ids == sorted(ids)

    def test_no_temp_files_left_behind(self, memfile):
        store = FileStore(memfile)
        _seed(store)
        leftovers = list(memfile.parent.glob(memfile.name + ".tmp"))
        assert leftovers == []

    def test_expired_entries_pruned_after_reload(self, memfile, at):
        store = FileStore(memfile)
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="keep",
                  text="kept", created_at=at(2026, 1, 1).isoformat())
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="gone",
                  text="expired", ttl_seconds=60,
                  created_at=at(2020, 1, 1).isoformat())
        reloaded = FileStore(memfile)
        pruned = reloaded.prune_expired(now=at(2026, 6, 1))
        assert pruned == 1
        assert {e.key for e in reloaded.list_entries(tenant_id=T1)} == {"keep"}
