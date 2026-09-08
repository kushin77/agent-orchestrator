"""engine/memory - scope-aware semantic retrieval.

Issue kushin77/agent-orchestrator#25, acceptance criterion "Vector store w/
semantic retrieval". Given a caller's session identity, the retriever returns
the semantically closest memories that the caller may actually see:

* only the caller's **tenant** is ever searched (no cross-tenant leak);
* the caller's **session** can see its own session memories, its own agent's
  memory, and tenant-global memory - never another session's or another
  agent's (see ``store.is_visible``);
* an optional scope allowlist (derived from the profile ``memoryScope``)
  narrows what is considered;
* ranking is cosine similarity over the configured embedder (offline
  ``BagOfWordsEmbedder`` by default - a model-backed embedder can be injected
  behind the same protocol);
* hits below ``min_score`` are *counted, not returned* - relevance is a gate,
  and the enrichment layer reports exactly how many were gated out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .embedding import Embedder, cosine_similarity
from .model import MemoryEntry, MemoryKind, MemoryScope
from .store import MemoryStore


@dataclass(frozen=True)
class Hit:
    """A ranked, injected-able memory."""

    memory_id: str
    scope: MemoryScope
    kind: MemoryKind
    key: str
    text: str
    tenant_id: str
    agent_id: Optional[str]
    session_id: Optional[str]
    score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "scope": self.scope.value,
            "kind": self.kind.value,
            "key": self.key,
            "text": self.text,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "score": round(self.score, 6),
        }


@dataclass
class RetrieverResult:
    """What a search considered and what survived the gates."""

    query: str
    hits: List[Hit] = field(default_factory=list)
    evaluated: int = 0
    below_relevance: int = 0
    scope_excluded: int = 0
    scopes_searched: Tuple[MemoryScope, ...] = ()

    @property
    def total_candidates(self) -> int:
        return self.evaluated + self.scope_excluded

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "hits": [h.to_dict() for h in self.hits],
            "evaluated": self.evaluated,
            "below_relevance": self.below_relevance,
            "scope_excluded": self.scope_excluded,
            "scopes_searched": [s.value for s in self.scopes_searched],
        }


_ALL_SCOPES: Tuple[MemoryScope, ...] = (
    MemoryScope.SESSION,
    MemoryScope.AGENT,
    MemoryScope.TENANT,
)


class Retriever:
    """Scope-aware semantic retriever over one ``MemoryStore``."""

    def __init__(self, store: MemoryStore,
                 embedder: Optional[Embedder] = None,
                 min_score: float = 0.10) -> None:
        self.store = store
        self.embedder: Embedder = embedder or store.embedder
        self.min_score = min_score

    def search(self, query: str, *, tenant_id: str,
               agent_id: Optional[str] = None,
               session_id: Optional[str] = None,
               scopes: Optional[Sequence[MemoryScope]] = None,
               kinds: Optional[Sequence[MemoryKind]] = None,
               limit: int = 8,
               min_score: Optional[float] = None,
               now: Optional[datetime] = None) -> RetrieverResult:
        """Return the top-``limit`` relevant, visible memories."""

        if not query or not query.strip():
            return RetrieverResult(query=query, scopes_searched=())
        allowed: Tuple[MemoryScope, ...] = tuple(scopes) if scopes else _ALL_SCOPES
        allowed_set: Set[MemoryScope] = set(allowed)
        threshold = self.min_score if min_score is None else min_score
        kind_set: Optional[Tuple[MemoryKind, ...]] = None
        if kinds is not None:
            kind_set = tuple(k if isinstance(k, MemoryKind)
                             else MemoryKind(k) for k in kinds)

        candidates: List[MemoryEntry] = []
        scope_excluded = 0
        for entry in self.store.entries_for_tenant(tenant_id, now=now):
            if entry.scope not in allowed_set:
                scope_excluded += 1
                continue
            if kind_set is not None and entry.kind not in kind_set:
                scope_excluded += 1
                continue
            if not self.store.is_visible(entry, tenant_id, agent_id,
                                         session_id):
                scope_excluded += 1
                continue
            candidates.append(entry)

        if not candidates:
            return RetrieverResult(query=query, evaluated=0,
                                   scope_excluded=scope_excluded,
                                   scopes_searched=allowed)

        query_vector = self.embedder.embed(query)
        scored: List[Tuple[float, MemoryEntry]] = []
        for entry in candidates:
            vector = entry.embedding if entry.embedding else \
                self.embedder.embed(entry.text)
            scored.append((cosine_similarity(query_vector, vector), entry))

        scored.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
        hits: List[Hit] = []
        below = 0
        for score, entry in scored:
            if score < threshold:
                below += 1
                continue
            hits.append(Hit(
                memory_id=entry.memory_id,
                scope=entry.scope,
                kind=entry.kind,
                key=entry.key,
                text=entry.text,
                tenant_id=entry.tenant_id,
                agent_id=entry.agent_id,
                session_id=entry.session_id,
                score=score,
            ))
            if len(hits) >= limit:
                break

        return RetrieverResult(
            query=query,
            hits=hits,
            evaluated=len(scored),
            below_relevance=below,
            scope_excluded=scope_excluded,
            scopes_searched=allowed,
        )
