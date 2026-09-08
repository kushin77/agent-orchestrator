"""Semantic retrieval tests: ranking, scope awareness, gates."""

from __future__ import annotations

from engine.memory.model import MemoryScope
from engine.memory.retrieval import Retriever

from conftest import T1, T2, AGENT_A, SESS_1, SESS_2


class TestSemanticRanking:
    def test_top_hit_is_most_relevant(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                  text="deploy pipeline ships every merged commit to staging")
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="pricing",
                  text="customer pricing tiers are bronze silver gold")
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="oncall",
                  text="oncall rotation rotates weekly across the squad")

        result = Retriever(store).search(
            "how do we deploy the pipeline after a merge", tenant_id=T1)
        assert result.hits
        assert result.hits[0].key == "deploy"
        assert result.hits[0].score > 0

    def test_ranking_scores_are_descending(self, store):
        for key, text in [
            ("one", "alpha bravo charlie delta"),
            ("two", "alpha bravo charlie"),
            ("three", "alpha bravo"),
        ]:
            store.put(tenant_id=T1, scope=MemoryScope.TENANT, key=key,
                      text=text)
        result = Retriever(store).search("alpha bravo charlie delta",
                                         tenant_id=T1)
        scores = [h.score for h in result.hits]
        assert scores == sorted(scores, reverse=True)
        assert result.hits[0].key == "one"

    def test_empty_query_returns_nothing(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="some fact")
        result = Retriever(store).search("   ", tenant_id=T1)
        assert result.hits == []


class TestScopeAwareRetrieval:
    def test_allowed_scope_restriction(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="tenant fact about release")
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="agent", text="agent fact about release")
        result = Retriever(store).search(
            "release", tenant_id=T1, agent_id=AGENT_A, session_id=SESS_1,
            scopes=[MemoryScope.TENANT])
        assert result.hits
        assert all(h.scope is MemoryScope.TENANT for h in result.hits)

    def test_session_sees_own_hierarchy_only(self, store):
        # tenant + agent A + session 1 + session 2 entries all present.
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="org",
                  text="organization policy on vacations")
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="agent-a", text="agent a preference: quiet hours")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="s1", text="session one todo list")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_2, key="s2", text="session two todo list")

        # min_score=0.0 isolates the SCOPE filter from the relevance filter.
        result = Retriever(store).search("todo list", tenant_id=T1,
                                         agent_id=AGENT_A, session_id=SESS_1,
                                         min_score=0.0)
        keys = {h.key for h in result.hits}
        assert "s1" in keys
        assert "s2" not in keys
        assert "org" in keys
        assert result.scope_excluded >= 1

    def test_other_tenant_agent_invisible(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.AGENT, agent_id=AGENT_A,
                  key="note", text="acme's agent note")
        store.put(tenant_id=T2, scope=MemoryScope.TENANT, key="other",
                  text="globex unrelated fact")
        result = Retriever(store).search("acme agent note", tenant_id=T2,
                                         agent_id=AGENT_A, min_score=0.0)
        assert all(h.tenant_id == T2 for h in result.hits)
        assert not any(h.key == "note" for h in result.hits)


class TestRelevanceGate:
    def test_below_min_score_counted_not_returned(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                  text="deployment automation for releases")
        result = Retriever(store).search(
            "deployment automation", tenant_id=T1,
            min_score=0.999)  # nothing can reach this
        assert result.hits == []
        assert result.below_relevance > 0

    def test_zero_threshold_returns_candidates(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.TENANT, key="deploy",
                  text="deployment automation for releases")
        result = Retriever(store).search("deployment automation",
                                         tenant_id=T1, min_score=0.0)
        assert any(h.key == "deploy" for h in result.hits)

    def test_limit_respected(self, store):
        for i in range(10):
            store.put(tenant_id=T1, scope=MemoryScope.TENANT,
                      key=f"k{i}", text=f"shared topic note number {i}")
        result = Retriever(store).search("shared topic", tenant_id=T1,
                                         limit=3)
        assert len(result.hits) <= 3

    def test_kinds_filter(self, store):
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="e", text="ran deploy at noon",
                  kind="episodic")
        store.put(tenant_id=T1, scope=MemoryScope.SESSION, agent_id=AGENT_A,
                  session_id=SESS_1, key="s", text="deploy policy is calm",
                  kind="semantic")
        result = Retriever(store).search(
            "deploy", tenant_id=T1, agent_id=AGENT_A, session_id=SESS_1,
            kinds=["episodic"])
        assert all(h.kind.value == "episodic" for h in result.hits)
        assert {h.key for h in result.hits} == {"e"}
