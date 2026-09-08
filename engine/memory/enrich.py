"""engine/memory - context enrichment pipeline (bounded, measured, gated).

Issue kushin77/agent-orchestrator#25, acceptance criterion "Context
enrichment pipeline: only inject relevant memory into prompts (bounded,
measured tokens)".

``ContextEnricher.enrich`` turns a caller session + a query into a
*cache-compatible* memory block for a prompt, enforcing three gates and
reporting every number:

1. **scope gate** - only memories the profile may use (its frozen
   ``memoryScope`` mapped through ``MemoryScope.from_profile_values``) and
   the caller's session may see are even candidates;
2. **relevance gate** - entries scoring below ``min_relevance`` are counted
   and dropped ("only inject relevant memory");
3. **budget gate** - the rendered block is trimmed from the lowest score up
   until it fits ``max_tokens`` (measured with ``estimate_tokens``), and the
   measured usage is reported.

The emitted block is deterministic (``prompt_cache.render_memory_block``):
the same logical memory set injects the same bytes, so provider prefix
caching is not defeated by memory (see ``prompt_cache``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .model import MemoryKind, MemoryScope
from .prompt_cache import estimate_tokens, footprint, render_memory_block
from .retrieval import Hit, Retriever, RetrieverResult
from .store import MemoryStore

#: Default allowed scopes when no profile constraint is supplied.
_ALL_SCOPES: Tuple[MemoryScope, ...] = (
    MemoryScope.SESSION,
    MemoryScope.AGENT,
    MemoryScope.TENANT,
)


@dataclass(frozen=True)
class EnrichmentPolicy:
    """Tunables for the enrichment gates."""

    max_tokens: int = 1200
    min_relevance: float = 0.10
    top_k: int = 8
    block_header: str = "Scoped memory (tenant/agent/session):"


@dataclass
class EnrichmentReport:
    """What enrichment injected, dropped, and measured."""

    query: str
    tenant_id: str
    agent_id: Optional[str]
    session_id: Optional[str]
    hits: List[Hit] = field(default_factory=list)
    block_text: str = ""
    token_count: int = 0
    budget_tokens: int = 0
    evaluated: int = 0
    below_relevance: int = 0
    budget_dropped: int = 0
    scope_excluded: int = 0
    cache_footprint: Optional[str] = None
    scopes_allowed: Tuple[MemoryScope, ...] = ()
    profile_scopes: Tuple[str, ...] = ()
    reason_codes: List[str] = field(default_factory=list)

    @property
    def injected_count(self) -> int:
        return len(self.hits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "injected_count": self.injected_count,
            "token_count": self.token_count,
            "budget_tokens": self.budget_tokens,
            "evaluated": self.evaluated,
            "below_relevance": self.below_relevance,
            "budget_dropped": self.budget_dropped,
            "scope_excluded": self.scope_excluded,
            "cache_footprint": self.cache_footprint,
            "scopes_allowed": [s.value for s in self.scopes_allowed],
            "reason_codes": self.reason_codes,
            "hits": [h.to_dict() for h in self.hits],
        }


class ContextEnricher:
    """Assemble a bounded, measured, cache-safe memory block for a prompt."""

    def __init__(self, store: MemoryStore,
                 retriever: Optional[Retriever] = None,
                 policy: Optional[EnrichmentPolicy] = None) -> None:
        self.store = store
        self.retriever = retriever or Retriever(store)
        self.policy = policy or EnrichmentPolicy()

    def enrich(self, query: str, *, tenant_id: str,
               agent_id: Optional[str] = None,
               session_id: Optional[str] = None,
               profile_scopes: Optional[Sequence[str]] = None,
               kinds: Optional[Sequence[MemoryKind]] = None,
               max_tokens: Optional[int] = None,
               min_relevance: Optional[float] = None,
               top_k: Optional[int] = None,
               now: Optional[datetime] = None) -> EnrichmentReport:
        """Build the injection block for ``query`` under the caller session.

        ``profile_scopes`` takes the *frozen* profile ``memoryScope`` values
        (``["user", "session", "repository"]``) and maps them to store scopes,
        so an AgentProfile's declared memory rights drive what is injected.
        """

        budget = self.policy.max_tokens if max_tokens is None else max_tokens
        threshold = (self.policy.min_relevance if min_relevance is None
                     else min_relevance)
        consider = self.policy.top_k if top_k is None else top_k
        header = self.policy.block_header

        reason_codes: List[str] = []
        if profile_scopes:
            allowed = MemoryScope.from_profile_values(list(profile_scopes))
            reason_codes.append("profile_scopes:" + ",".join(
                sorted(profile_scopes)))
        else:
            allowed = _ALL_SCOPES
        reason_codes.append("scopes:" + ",".join(s.value for s in allowed))

        result: RetrieverResult = self.retriever.search(
            query,
            tenant_id=tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            scopes=allowed,
            kinds=kinds,
            limit=consider,
            min_score=threshold,
            now=now,
        )

        # Budget gate: keep hits in score order while the rendered block fits.
        kept: List[Hit] = []
        dropped = 0
        if result.hits:
            block_header = header if header else ""
            for hit in result.hits:
                candidate = render_memory_block(kept + [hit],
                                                header=block_header)
                if estimate_tokens(candidate) > budget:
                    dropped += 1
                    continue
                kept.append(hit)

        block_text = render_memory_block(kept, header=header) if kept else ""
        reason_codes.append("relevance:below=" + str(result.below_relevance))
        reason_codes.append("budget:max=" + str(budget))
        reason_codes.append("budget:used=" + str(estimate_tokens(block_text)))
        reason_codes.append("budget:dropped=" + str(dropped))
        reason_codes.append("inject:" + str(len(kept)))

        return EnrichmentReport(
            query=query,
            tenant_id=tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            hits=kept,
            block_text=block_text,
            token_count=estimate_tokens(block_text),
            budget_tokens=budget,
            evaluated=result.evaluated,
            below_relevance=result.below_relevance,
            budget_dropped=dropped,
            scope_excluded=result.scope_excluded,
            cache_footprint=footprint(block_text) if block_text else None,
            scopes_allowed=allowed,
            profile_scopes=tuple(sorted(profile_scopes)) if profile_scopes
            else (),
            reason_codes=reason_codes,
        )
