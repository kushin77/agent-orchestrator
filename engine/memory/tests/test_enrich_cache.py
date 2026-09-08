"""Context-enrichment + prompt-cache compatibility tests.

Enrichment gates (only relevant, bounded, measured tokens) and the
deterministic, cache-safe rendering that keeps memory from defeating
provider prefix caching.
"""

from __future__ import annotations

import pytest

from engine.memory.enrich import ContextEnricher
from engine.memory.model import MemoryScope
from engine.memory.prompt_cache import (PrefixError, assemble_prefix,
                                        estimate_tokens, render_memory_block,
                                        scan_dynamic, validate_static_region)
from engine.memory.store import InMemoryStore

from conftest import T1, AGENT_A, SESS_1


class TestRelevanceGate:
    def test_only_relevant_memory_injected(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                  text="deploy pipeline ships merged commits to staging")
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="billing",
                  text="billing invoices are sent on the first of the month")
        report = ContextEnricher(store).enrich(
            "how do we deploy to staging", tenant_id=T1,
            min_relevance=0.10)
        keys = {h.key for h in report.hits}
        assert "deploy" in keys
        assert "billing" not in keys

    def test_irrelevant_only_injects_nothing(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="billing",
                  text="invoice payment terms are net thirty days")
        report = ContextEnricher(store).enrich(
            "how do we ship the codebase to production", tenant_id=T1,
            min_relevance=0.10)
        assert report.hits == []
        assert report.block_text == ""
        assert report.cache_footprint is None
        assert report.injected_count == 0


class TestBudgetGate:
    def test_block_bounded_to_max_tokens(self, store):
        # Several long tenant memories; tiny budget must trim from low score.
        for i in range(6):
            store.put(tenant_id=T1, scope=MemoryScope.TENANT,
                      key=f"note{i}",
                      text=("engineering topic note number %d with plenty of "
                            "extra words padding the length so tokens "
                            "accumulate quickly and the budget bites") % i)
        report = ContextEnricher(store).enrich(
            "engineering topic", tenant_id=T1, max_tokens=60)
        assert report.token_count <= report.budget_tokens
        assert report.budget_dropped >= 0
        # With a big budget nothing is dropped; with a tiny one something is.
        big = ContextEnricher(store).enrich("engineering topic",
                                            tenant_id=T1, max_tokens=2000)
        assert big.budget_dropped == 0
        assert big.injected_count > 0

    def test_reason_codes_are_mechanical(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                  text="deploy pipeline for releases")
        report = ContextEnricher(store).enrich("deploy releases",
                                               tenant_id=T1)
        joined = " ".join(report.reason_codes)
        assert "inject:" in joined
        assert "budget:max=" in joined
        assert "budget:used=" in joined
        assert "relevance:below=" in joined
        assert "scopes:" in joined
        assert "tenant" in joined


class TestProfileScopeMapping:
    def test_profile_session_only_injects_session(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="tenant org fact about planning")
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="agent", text="agent fact about planning")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="sess",
                  text="session fact about planning")
        report = ContextEnricher(store).enrich(
            "planning", tenant_id=T1, agent_id=AGENT_A, session_id=SESS_1,
            profile_scopes=["session"])
        assert report.hits
        assert all(h.scope is MemoryScope.SESSION for h in report.hits)

    def test_profile_user_maps_to_agent(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="org fact about review style")
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="agent", text="agent fact about review style")
        report = ContextEnricher(store).enrich(
            "review style", tenant_id=T1, agent_id=AGENT_A,
            profile_scopes=["user"])  # frozen profile value -> AGENT scope
        assert report.hits
        assert all(h.scope is MemoryScope.AGENT for h in report.hits)
        assert "profile_scopes:user" in " ".join(report.reason_codes)

    def test_profile_repository_maps_to_tenant(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="org fact about oncall rotation")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="sess",
                  text="session fact about oncall rotation")
        report = ContextEnricher(store).enrich(
            "oncall rotation", tenant_id=T1, agent_id=AGENT_A,
            session_id=SESS_1, profile_scopes=["repository"])
        assert report.hits
        assert all(h.scope is MemoryScope.TENANT for h in report.hits)


class TestDeterministicRender:
    def _seed(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                  text="deploy  pipeline   ships  merged commits")
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="style", text="typed returns preferred")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="todo", text="ship release today")

    def test_block_bytes_are_pure_function_of_logical_set(self):
        # Same memories stored in a different order (even across processes of
        # the same code path) must render identical bytes.
        store_a = InMemoryStore()
        store_b = InMemoryStore()
        self._seed(store_a)
        # store_b gets the same three entries in reverse insertion order
        store_b.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                    session_id=SESS_1, key="todo", text="ship release today")
        store_b.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                    key="style", text="typed returns preferred")
        store_b.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                    text="deploy  pipeline   ships  merged commits")

        from engine.memory.enrich import ContextEnricher
        rep_a = ContextEnricher(store_a).enrich(
            "deploy", tenant_id=T1, agent_id=AGENT_A, session_id=SESS_1,
            min_relevance=0.0, max_tokens=2000)
        rep_b = ContextEnricher(store_b).enrich(
            "deploy", tenant_id=T1, agent_id=AGENT_A, session_id=SESS_1,
            min_relevance=0.0, max_tokens=2000)
        assert rep_a.block_text == rep_b.block_text
        assert rep_a.cache_footprint == rep_b.cache_footprint

    def test_repeated_enrichment_is_byte_stable(self, store):
        self._seed(store)
        enricher = ContextEnricher(store)
        first = enricher.enrich("deploy", tenant_id=T1, agent_id=AGENT_A,
                                session_id=SESS_1, min_relevance=0.0,
                                max_tokens=2000)
        second = enricher.enrich("deploy", tenant_id=T1, agent_id=AGENT_A,
                                 session_id=SESS_1, min_relevance=0.0,
                                 max_tokens=2000)
        assert first.block_text == second.block_text
        assert first.cache_footprint == second.cache_footprint

    def test_render_emits_no_run_metadata(self, store):
        self._seed(store)
        block = render_memory_block(store.list_entries(tenant_id=T1))
        # entry-own timestamps / container ids must never leak into the block
        assert "created_at" not in block
        assert "last_accessed" not in block
        assert "memory_id" not in block
        assert "updated_at" not in block

    def test_whitespace_is_presentation(self):
        block = render_memory_block([
            _FakeHit(MemoryScope.TENANT, "deploy",
                     "deploy  pipeline   ships  merged  commits"),
        ])
        twin = render_memory_block([
            _FakeHit(MemoryScope.TENANT, "deploy",
                     "deploy pipeline ships merged commits"),
        ])
        assert block == twin

    def test_order_is_presentation(self):
        a = render_memory_block([
            _FakeHit(MemoryScope.TENANT, "zebra", "z text"),
            _FakeHit(MemoryScope.TENANT, "alpha", "a text"),
        ])
        b = render_memory_block([
            _FakeHit(MemoryScope.TENANT, "alpha", "a text"),
            _FakeHit(MemoryScope.TENANT, "zebra", "z text"),
        ])
        assert a == b


class _FakeHit:
    """Minimal stand-in exposing the renderer's required fields."""

    def __init__(self, scope, key, text):
        self.scope = scope
        self.key = key
        self.text = text
        self.kind = None


class TestTokenEstimate:
    def test_estimate_nonzero_and_monotonic(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("word") >= 1
        assert estimate_tokens("longer text here") > estimate_tokens("a")


class TestPrefixDiscipline:
    def test_assemble_static_first_delta_last(self):
        prefix = assemble_prefix(
            system_text="You are a helpful ops agent.",
            memory_block="- [tenant:semantic] deploy: ships weekly",
            user_delta="What is the cadence?",
        )
        assert prefix.static_text.startswith("You are a helpful ops agent.")
        assert "deploy" in prefix.static_text
        assert prefix.delta_text == "What is the cadence?"
        assert prefix.render().startswith(prefix.static_text)

    def test_render_is_deterministic(self):
        kwargs = dict(system_text="SYS", memory_block="- [tenant] x: y",
                      user_delta="USER")
        assert assemble_prefix(**kwargs).render() == assemble_prefix(
            **kwargs).render()

    def test_delta_then_static_rejected(self):
        from engine.memory.prompt_cache import validate_block_order
        with pytest.raises(PrefixError):
            validate_block_order([("user", "delta first"),
                                  ("system", "static second")])
        with pytest.raises(PrefixError):
            validate_block_order([("system", "ok"),
                                  ("user", "delta"),
                                  ("context", "late static")])
        validate_block_order([("system", "s"), ("context", "c"),
                              ("user", "u")])

    def test_dynamic_token_in_static_region_rejected(self):
        with pytest.raises(PrefixError):
            assemble_prefix(
                system_text="You are a helpful agent.",
                memory_block="- [session:semantic] run: run_id abc123 "
                             "finished at 2026-09-08T10:00:00",
                user_delta="hello")
        # the same content as a user delta (dynamic tail) is fine
        prefix = assemble_prefix(
            system_text="You are a helpful agent.",
            memory_block="",
            user_delta="run_id abc123 finished at 2026-09-08T10:00:00")
        assert prefix.delta_text

    def test_validate_static_region_finds_dynamic_tokens(self):
        assert scan_dynamic("session_id: s-1")
        assert scan_dynamic("at 2026-09-08T10:00:00Z")
        assert scan_dynamic("trace 9f8e7d6c-5b4a-4c3d-9e8f-1a2b3c4d5e6f")
        with pytest.raises(PrefixError):
            validate_static_region("prefix with hostname xyz")

    def test_clean_static_region_passes(self):
        assert scan_dynamic("release cadence is every two weeks") == []
        validate_static_region("release cadence is every two weeks")
