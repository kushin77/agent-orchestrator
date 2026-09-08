"""SemanticCache: TTL expiry, LRU eviction, accounting, tenant/tier isolation,
and the file-backed persistence seam."""

from __future__ import annotations

import pytest

from limits.cache import FileCacheStore, MemoryCacheStore, SemanticCache

PROMPT = "What is the token budget for acme?"
VALUE_A = "cached answer A"
VALUE_B = "cached answer B"


@pytest.fixture
def cache(clock) -> SemanticCache:
    return SemanticCache(default_ttl=60, max_entries=100, clock=clock)


class TestBasic:
    def test_set_get_roundtrip(self, cache):
        key = cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        assert cache.get(key) == VALUE_A

    def test_miss_returns_none(self, cache):
        assert cache.get("deadbeef" * 8) is None

    def test_delete_removes_entry(self, cache):
        key = cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        cache.delete(key)
        assert cache.get(key) is None

    def test_clear_empties(self, cache):
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        cache.store_result("acme", "LOW", PROMPT + "?", VALUE_B)
        cache.clear()
        assert len(cache.store) == 0


class TestTTL:
    def test_entry_expires_after_ttl(self, cache, clock):
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)  # ttl 60
        clock.advance(59)
        assert cache.resolve("acme", "LOW", PROMPT).hit is True
        clock.advance(2)  # past 60s
        resolution = cache.resolve("acme", "LOW", PROMPT)
        assert resolution.hit is False
        assert resolution.value is None

    def test_expired_entry_evicted_and_counted(self, cache, clock):
        key = cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        clock.advance(61)
        assert cache.get(key) is None
        assert cache.stats()["evictions"] >= 1

    def test_custom_ttl_per_entry(self, cache, clock):
        cache.store_result("acme", "LOW", PROMPT, VALUE_A, ttl=5)
        clock.advance(6)
        assert cache.resolve("acme", "LOW", PROMPT).hit is False

    def test_prune_removes_only_expired(self, cache, clock):
        cache.store_result("acme", "LOW", PROMPT, VALUE_A, ttl=5)
        cache.store_result("acme", "LOW", PROMPT + "?", VALUE_B, ttl=100)
        clock.advance(6)
        assert cache.prune() == 1
        assert len(cache.store) == 1


class TestAccounting:
    def test_hit_miss_stats_and_hit_rate(self, cache):
        cache.resolve("acme", "LOW", PROMPT)  # miss
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        cache.resolve("acme", "LOW", PROMPT)  # hit
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["sets"] == 1
        assert stats["entries"] == 1
        assert stats["hit_rate_pct"] == 50.0

    def test_cache_hit_is_accounted_as_zero_cost_resolution(self, cache):
        # A hit returns the value; the facade turns this into a metering
        # record with outcome=cache_hit (asserted in test_limiter).
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        resolution = cache.resolve("acme", "LOW", PROMPT)
        assert resolution.hit is True
        assert resolution.value == VALUE_A


class TestIsolation:
    def test_tenant_isolation(self, cache):
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        assert cache.resolve("globex", "LOW", PROMPT).hit is False

    def test_tier_isolation(self, cache):
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        assert cache.resolve("acme", "HIGH", PROMPT).hit is False

    def test_task_type_isolation(self, cache):
        cache.store_result("acme", "LOW", PROMPT, VALUE_A, task_type="summarize")
        assert cache.resolve("acme", "LOW", PROMPT).hit is False


class TestEviction:
    def test_lru_evicts_oldest_when_full(self, clock):
        cache = SemanticCache(max_entries=2, clock=clock)
        k1 = cache.store_result("acme", "LOW", "one", VALUE_A)
        clock.advance(1)
        k2 = cache.store_result("acme", "LOW", "two", VALUE_B)
        clock.advance(1)
        k3 = cache.store_result("acme", "LOW", "three", "c")
        assert cache.get(k1) is None  # oldest evicted
        assert cache.get(k2) == VALUE_B
        assert cache.get(k3) == "c"
        assert cache.stats()["evictions"] == 1


class TestFileStore:
    def test_file_store_persists_across_reopen(self, tmp_path, clock):
        base = str(tmp_path / "semcache")
        store = FileCacheStore(base, clock=clock)
        cache = SemanticCache(store=store, clock=clock)
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)

        reopened = SemanticCache(store=FileCacheStore(base, clock=clock), clock=clock)
        resolution = reopened.resolve("acme", "LOW", PROMPT)
        assert resolution.hit is True
        assert resolution.value == VALUE_A

    def test_file_store_len_and_clear(self, tmp_path, clock):
        store = FileCacheStore(str(tmp_path / "semcache"), clock=clock)
        cache = SemanticCache(store=store, clock=clock)
        cache.store_result("acme", "LOW", PROMPT, VALUE_A)
        assert len(store) == 1
        cache.clear()
        assert len(store) == 0

    def test_memory_store_is_default(self, cache):
        assert isinstance(cache.store, MemoryCacheStore)
