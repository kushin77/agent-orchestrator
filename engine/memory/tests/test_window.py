"""Sliding-window memory policy tests (#674).

The window drops redundant context (duplicate / subsumed text) before it
costs a token, and what *stays* is byte-stable — cache friendliness holds.
"""

from __future__ import annotations

from engine.memory.enrich import ContextEnricher, EnrichmentPolicy
from engine.memory.model import MemoryScope
from engine.memory.prompt_cache import estimate_tokens
from engine.memory.window import WindowPolicy, slide_window

from conftest import T1, AGENT_A, SESS_1


class _Hit:
    """Minimal stand-in exposing the text the window dedupes on."""

    def __init__(self, text, key="k", scope=MemoryScope.TENANT):
        self.text = text
        self.key = key
        self.scope = scope
        self.kind = None
        self.score = 0.5


class TestSlideWindowUnit:
    def test_duplicate_text_dropped(self):
        hits = [
            _Hit("deploy pipeline ships merged commits", key="a"),
            _Hit("deploy pipeline ships merged commits", key="b"),
        ]
        kept, dropped = slide_window(hits, WindowPolicy())
        assert len(kept) == 1
        assert dropped == 1

    def test_repeated_prefix_dropped(self):
        hits = [
            _Hit("deploy pipeline ships merged commits to staging", key="long"),
            _Hit("deploy pipeline ships merged commits", key="short"),
        ]
        kept, dropped = slide_window(hits, WindowPolicy())
        assert len(kept) == 1
        assert kept[0].key == "long"
        assert dropped == 1

    def test_disabled_keeps_redundant(self):
        hits = [
            _Hit("same text", key="a"),
            _Hit("same text", key="b"),
        ]
        kept, dropped = slide_window(
            hits, WindowPolicy(dedupe_text=False, drop_subsumed=False))
        assert len(kept) == 2
        assert dropped == 0

    def test_dedupe_without_subsumed(self):
        hits = [
            _Hit("deploy pipeline ships merged commits to staging", key="long"),
            _Hit("deploy pipeline ships merged commits", key="short"),
        ]
        # subsumed off: the prefix is NOT dropped
        kept, dropped = slide_window(
            hits, WindowPolicy(dedupe_text=True, drop_subsumed=False))
        assert len(kept) == 2
        assert dropped == 0

    def test_subsumed_without_dedupe(self):
        hits = [
            _Hit("deploy pipeline ships merged commits to staging", key="long"),
            _Hit("deploy pipeline ships merged commits", key="short"),
            _Hit("deploy pipeline ships merged commits", key="dup"),
        ]
        # dedupe off, subsumed on: both the prefix and the exact dup drop
        kept, dropped = slide_window(
            hits, WindowPolicy(dedupe_text=False, drop_subsumed=True))
        assert len(kept) == 1
        assert dropped == 2


class TestEnrichWindow:
    def _seed_redundant(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="long",
                  text="deploy pipeline ships merged commits to staging")
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="prefix",
                  text="deploy pipeline ships merged commits")
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="dupe",
                  text="deploy pipeline ships merged commits to staging")

    def test_redundant_context_dropped_before_tokens(self, store):
        self._seed_redundant(store)
        report = ContextEnricher(store).enrich(
            "deploy pipeline staging", tenant_id=T1,
            min_relevance=0.0, max_tokens=2000)
        # three memories, but the window drops the prefix and the exact dupe
        assert report.injected_count == 1
        assert report.redundant_dropped == 2
        # the repeated prefix is not re-included in the block
        assert "to staging" in report.block_text
        assert report.block_text.count("ships merged commits") == 1

    def test_window_drop_is_reported_in_reason_codes(self, store):
        self._seed_redundant(store)
        report = ContextEnricher(store).enrich(
            "deploy pipeline staging", tenant_id=T1,
            min_relevance=0.0, max_tokens=2000)
        joined = " ".join(report.reason_codes)
        assert "window:redundant=2" in joined
        assert report.to_dict()["redundant_dropped"] == 2

    def test_what_stays_is_byte_stable(self, store):
        self._seed_redundant(store)
        enricher = ContextEnricher(store)
        first = enricher.enrich("deploy pipeline staging", tenant_id=T1,
                                min_relevance=0.0, max_tokens=2000)
        second = enricher.enrich("deploy pipeline staging", tenant_id=T1,
                                 min_relevance=0.0, max_tokens=2000)
        assert first.block_text == second.block_text
        assert first.cache_footprint == second.cache_footprint
        assert first.cache_footprint is not None

    def test_window_reduces_measured_tokens(self, store):
        self._seed_redundant(store)
        with_window = ContextEnricher(store).enrich(
            "deploy pipeline staging", tenant_id=T1,
            min_relevance=0.0, max_tokens=2000)
        without = ContextEnricher(
            store,
            policy=EnrichmentPolicy(
                window=WindowPolicy(dedupe_text=False,
                                    drop_subsumed=False)),
        ).enrich("deploy pipeline staging", tenant_id=T1,
                 min_relevance=0.0, max_tokens=2000)
        # the window demonstrably emits fewer estimated tokens (in-tree,
        # deterministic estimate — not a provider billing claim)
        assert with_window.redundant_dropped == 2
        assert without.redundant_dropped == 0
        assert with_window.token_count < without.token_count

    def test_distinct_memories_not_dropped(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                  text="deploy pipeline ships merged commits")
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="billing",
                  text="billing invoices are sent monthly")
        report = ContextEnricher(store).enrich(
            "deploy and billing", tenant_id=T1,
            min_relevance=0.0, max_tokens=2000)
        assert report.redundant_dropped == 0
        assert report.injected_count == 2
