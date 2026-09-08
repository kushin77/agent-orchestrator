"""engine/memory - scoped agent memory store (tenant/agent/session + TTL).

Issue kushin77/agent-orchestrator#25 ("21 Scoped agent memory store"),
engine lane, phase 3. The "organizational memory" of each tenant's agent
org: semantic + episodic memory scoped to tenant / agent / session with
strict isolation, TTL + per-scope eviction, offline semantic retrieval,
bounded/measured context enrichment that stays provider-cache compatible,
and GDPR-ready forget/export.

Public surface
--------------

Vocabulary
    ``MemoryScope`` (tenant/agent/session; maps the frozen profile
    ``memoryScope`` values via ``from_profile_value``), ``MemoryKind``
    (semantic/episodic), ``MemoryEntry``, ``MemoryIsolationError``.

Storage
    ``MemoryStore`` (in-memory, the seam), ``FileStore`` (JSON persistence),
    ``ScopePolicy`` / ``default_policies`` / ``EvictionPolicy`` (lifecycle).

Retrieval + enrichment
    ``Retriever`` / ``Hit`` (scope-aware semantic search),
    ``ContextEnricher`` / ``EnrichmentPolicy`` / ``EnrichmentReport``
    (bounded, measured, gated context injection), and the prompt-cache
    discipline helpers ``render_memory_block`` / ``estimate_tokens`` /
    ``assemble_prefix``.

Data-subject rights
    ``export_memory`` / ``forget`` (GDPR-ready).

Offline embeddings
    ``BagOfWordsEmbedder`` / ``cosine_similarity``.

Contract doc: ``engine/memory/README.md``. Owner lane: engine
(``docs/EXECUTION-PLAN.md``). Import as ``engine.memory.*`` from the repo
root (engine/ is a PEP-420 namespace package).
"""

from .embedding import (BagOfWordsEmbedder, Embedder, cosine_similarity,
                        tokenize)
from .enrich import (ContextEnricher, EnrichmentPolicy, EnrichmentReport)
from .gdpr import export_memory, forget, ForgetReport
from .lifecycle import (EvictionPolicy, ScopePolicy, default_policies)
from .model import (MemoryEntry, MemoryIsolationError, MemoryKind,
                    MemoryScope, normalize_text)
from .prompt_cache import (Prefix, assemble_prefix, estimate_tokens,
                           footprint, render_memory_block, scan_dynamic,
                           validate_static_region)
from .retrieval import Hit, Retriever, RetrieverResult
from .store import FileStore, InMemoryStore, MemoryStore

__all__ = [
    # vocabulary
    "MemoryScope",
    "MemoryKind",
    "MemoryEntry",
    "MemoryIsolationError",
    "normalize_text",
    # storage + lifecycle
    "MemoryStore",
    "InMemoryStore",
    "FileStore",
    "ScopePolicy",
    "EvictionPolicy",
    "default_policies",
    # retrieval + enrichment + cache discipline
    "Retriever",
    "RetrieverResult",
    "Hit",
    "ContextEnricher",
    "EnrichmentPolicy",
    "EnrichmentReport",
    "render_memory_block",
    "estimate_tokens",
    "footprint",
    "scan_dynamic",
    "validate_static_region",
    "assemble_prefix",
    "Prefix",
    # data-subject rights
    "export_memory",
    "forget",
    "ForgetReport",
    # offline embeddings
    "Embedder",
    "BagOfWordsEmbedder",
    "cosine_similarity",
    "tokenize",
]
