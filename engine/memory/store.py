"""engine/memory - the scoped memory store.

Issue kushin77/agent-orchestrator#25, engine lane. A memory store scoped by
tenant / agent / session with strict isolation, TTL expiry, per-scope
capacity eviction, and deterministic file persistence. Fully offline: the
default store is in-memory; ``FileStore`` persists to JSON with the stdlib.

Access model (the isolation contract, documented in ``README.md``):

* **runtime path** - ``get`` requires *full scope cover*: a caller holding
  ``(tenant_id, agent_id, session_id)`` may read a session memory only when
  it names that exact session, an agent memory only when it names that
  agent, and a tenant-global memory from anywhere inside the tenant. Any
  boundary crossing raises ``MemoryIsolationError`` and is counted.
* **tenant-authority path** - ``delete``/``list_entries``/``erase`` operate
  on the data-subject boundary (the tenant): they require a tenant match and
  back the GDPR forget/export surface in ``gdpr.py``.

Semantics of a write: storing the same ``(container, key)`` again is an
idempotent upsert (same deterministic ``memory_id``), so a re-store refreshes
content/TTL/embedding instead of duplicating (unbounded growth prevention).
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .embedding import BagOfWordsEmbedder, Embedder
from .lifecycle import (EvictionPolicy, ScopePolicy, default_policies,
                        evict_down_to, resolve_ttl)
from .model import (MemoryEntry, MemoryIsolationError, MemoryKind,
                    MemoryScope, container_id, memory_id_for, normalize_text,
                    scope_for, utcnow, validate_container)


class MemoryStore:
    """In-memory scoped store; the seam every consumer programs against.

    Construct with optional ``policies`` (per-scope capacity + default TTL),
    ``eviction`` (LRU default) and ``embedder`` (defaults to the offline
    ``BagOfWordsEmbedder``).
    """

    def __init__(self,
                 policies: Optional[Dict[MemoryScope, ScopePolicy]] = None,
                 eviction: EvictionPolicy = EvictionPolicy.LRU,
                 embedder: Optional[Embedder] = None) -> None:
        self.policies: Dict[MemoryScope, ScopePolicy] = dict(
            default_policies() if policies is None else policies
        )
        self.eviction = eviction
        self.embedder: Embedder = embedder or BagOfWordsEmbedder()
        self._by_id: Dict[str, MemoryEntry] = {}
        self._containers: Dict[str, "OrderedDict[str, MemoryEntry]"] = {}
        self._stats: Dict[str, int] = {
            "stored": 0,
            "updated": 0,
            "evicted": 0,
            "expired_removed": 0,
            "cross_scope_violations": 0,
        }

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #

    def put(self, *, tenant_id: str, key: str, text: str,
            scope: Optional[MemoryScope] = None,
            agent_id: Optional[str] = None,
            session_id: Optional[str] = None,
            kind: MemoryKind = MemoryKind.SEMANTIC,
            metadata: Optional[Dict[str, Any]] = None,
            ttl_seconds: Optional[int] = None,
            embedder: Optional[Embedder] = None,
            now: Optional[datetime] = None,
            created_at: Optional[str] = None) -> MemoryEntry:
        """Store a memory; idempotent upsert per ``(container, key)``.

        When ``scope`` is omitted it is derived from which of ``agent_id`` /
        ``session_id`` are present (``model.scope_for``). ``created_at``
        overrides the wall clock (backfill/import/testing); an existing entry
        keeps its original ``created_at``. Returns the entry.
        """

        if scope is None:
            scope = scope_for(tenant_id, agent_id, session_id)
        validate_container(tenant_id, scope, agent_id, session_id)
        if not key:
            raise ValueError("key is required")
        if text is None:
            raise ValueError("text is required")
        if isinstance(kind, str):
            kind = MemoryKind(kind)

        resolved_ttl = resolve_ttl(scope, ttl_seconds, self.policies)
        cid = container_id(scope, tenant_id, agent_id, session_id)
        mid = memory_id_for(tenant_id, agent_id, session_id, key)
        vector = list((embedder or self.embedder).embed(text))
        instant = (now or utcnow()).isoformat()
        normal = normalize_text(text)

        existing = self._by_id.get(mid)
        if existing is not None:
            existing.scope = scope
            existing.text = normal
            existing.kind = kind
            existing.metadata = dict(metadata or {})
            existing.ttl_seconds = resolved_ttl
            existing.updated_at = instant
            existing.embedding = vector
            container = self._containers.setdefault(cid,
                                                    OrderedDict())
            container.pop(mid, None)
            container[mid] = existing
            self._stats["updated"] += 1
            self._save()
            return existing

        entry = MemoryEntry(
            tenant_id=tenant_id,
            scope=scope,
            agent_id=agent_id,
            session_id=session_id,
            memory_id=mid,
            key=key,
            text=normal,
            kind=kind,
            metadata=dict(metadata or {}),
            created_at=created_at or instant,
            updated_at=created_at or instant,
            ttl_seconds=resolved_ttl,
            embedding=vector,
        )
        self._by_id[mid] = entry
        container = self._containers.setdefault(cid, OrderedDict())
        container[mid] = entry
        self._stats["stored"] += 1

        policy = self.policies.get(scope, ScopePolicy(capacity=1000))
        evicted = evict_down_to(container, capacity=policy.capacity, now=now)
        for victim in evicted:
            self._by_id.pop(victim.memory_id, None)
            self._stats["evicted"] += 1

        self._save()
        return entry

    # ------------------------------------------------------------------ #
    # runtime reads (full scope cover; loud on violation)
    # ------------------------------------------------------------------ #

    def _covers(self, entry: MemoryEntry, tenant_id: str,
                agent_id: Optional[str],
                session_id: Optional[str]) -> bool:
        """Whether ``(tenant_id, agent_id, session_id)`` may read ``entry``."""
        if entry.tenant_id != tenant_id:
            return False
        if entry.scope is MemoryScope.TENANT:
            return True
        if entry.scope is MemoryScope.AGENT:
            return agent_id is not None and entry.agent_id == agent_id
        # SESSION-scope memory: exact session identity, agent must match too.
        if session_id is None or entry.session_id != session_id:
            return False
        return entry.agent_id is None or entry.agent_id == agent_id

    def is_visible(self, entry: MemoryEntry, tenant_id: str,
                   agent_id: Optional[str] = None,
                   session_id: Optional[str] = None) -> bool:
        """Non-raising visibility test (used by the scope-aware retriever)."""

        return self._covers(entry, tenant_id, agent_id, session_id)

    def _container_of(self, entry: MemoryEntry) -> str:
        return container_id(entry.scope, entry.tenant_id, entry.agent_id,
                            entry.session_id)

    def get(self, memory_id: str, *, tenant_id: str,
            agent_id: Optional[str] = None,
            session_id: Optional[str] = None,
            now: Optional[datetime] = None) -> Optional[MemoryEntry]:
        """Read one entry by id under the runtime isolation contract.

        Returns ``None`` for a missing or expired entry; raises
        ``MemoryIsolationError`` when the caller does not cover the entry's
        scope. A successful read touches the entry (LRU ordering + stats).
        """

        entry = self._by_id.get(memory_id)
        if entry is None:
            return None
        if not self._covers(entry, tenant_id, agent_id, session_id):
            self._stats["cross_scope_violations"] += 1
            raise MemoryIsolationError(
                f"memory {memory_id} (tenant={entry.tenant_id!r}, "
                f"scope={entry.scope.value}, agent={entry.agent_id!r}, "
                f"session={entry.session_id!r}) is not covered by caller "
                f"tenant={tenant_id!r} agent={agent_id!r} "
                f"session={session_id!r}"
            )
        if entry.is_expired(now):
            self._remove(entry)
            self._stats["expired_removed"] += 1
            self._save()
            return None
        entry.touch(now)
        if self.eviction is EvictionPolicy.LRU:
            container = self._containers.get(self._container_of(entry))
            if container is not None:
                container.pop(entry.memory_id, None)
                container[entry.memory_id] = entry
        return entry

    def entries_for_tenant(self, tenant_id: str,
                           now: Optional[datetime] = None
                           ) -> List[MemoryEntry]:
        """Non-expired entries owned by ``tenant_id`` across all scopes.

        The candidate set for scope-aware semantic retrieval: it is filtered
        to one tenant (never another) and never returns an expired entry; the
        *scope* filter is applied by the retriever against the caller's
        session identity.
        """

        return [
            e for e in self._by_id.values()
            if e.tenant_id == tenant_id and not e.is_expired(now)
        ]

    # ------------------------------------------------------------------ #
    # tenant-authority data path (GDPR export/erase surface)
    # ------------------------------------------------------------------ #

    def list_entries(self, *, tenant_id: str,
                     agent_id: Optional[str] = None,
                     session_id: Optional[str] = None,
                     scope: Optional[MemoryScope] = None,
                     kinds: Optional[Tuple[MemoryKind, ...]] = None,
                     ) -> List[MemoryEntry]:
        """Tenant-scoped listing used by export/forget. Requires tenant match
        only (the data-subject boundary is the tenant)."""

        result: List[MemoryEntry] = []
        for entry in self._by_id.values():
            if entry.tenant_id != tenant_id:
                continue
            if agent_id is not None and entry.agent_id != agent_id:
                continue
            if session_id is not None and entry.session_id != session_id:
                continue
            if scope is not None and entry.scope is not scope:
                continue
            if kinds is not None and entry.kind not in kinds:
                continue
            result.append(entry)
        return result

    def delete(self, memory_id: str, *, tenant_id: str,
               agent_id: Optional[str] = None,
               session_id: Optional[str] = None) -> bool:
        """Delete one entry (runtime path): full scope cover required."""

        entry = self._by_id.get(memory_id)
        if entry is None:
            return False
        if not self._covers(entry, tenant_id, agent_id, session_id):
            self._stats["cross_scope_violations"] += 1
            raise MemoryIsolationError(
                f"delete denied for memory {memory_id}: not covered by "
                f"caller tenant={tenant_id!r} agent={agent_id!r} "
                f"session={session_id!r}"
            )
        self._remove(entry)
        self._save()
        return True

    def erase(self, memory_id: str, *, tenant_id: str) -> bool:
        """Erase one entry on tenant authority only (GDPR data-subject
        erasure). Does not require an agent/session identity, but never
        crosses the tenant boundary."""

        entry = self._by_id.get(memory_id)
        if entry is None:
            return False
        if entry.tenant_id != tenant_id:
            self._stats["cross_scope_violations"] += 1
            raise MemoryIsolationError(
                f"erase denied for memory {memory_id}: owned by tenant "
                f"{entry.tenant_id!r}, caller holds {tenant_id!r}"
            )
        self._remove(entry)
        self._save()
        return True

    # ------------------------------------------------------------------ #
    # expiry / housekeeping
    # ------------------------------------------------------------------ #

    def prune_expired(self, now: Optional[datetime] = None) -> int:
        """Delete every expired entry across all scopes; returns the count."""

        expired = [e for e in self._by_id.values() if e.is_expired(now)]
        for entry in expired:
            self._remove(entry)
        if expired:
            self._stats["expired_removed"] += len(expired)
            self._save()
        return len(expired)

    def _remove(self, entry: MemoryEntry) -> None:
        self._by_id.pop(entry.memory_id, None)
        container = self._containers.get(self._container_of(entry))
        if container is not None:
            container.pop(entry.memory_id, None)
            if not container:
                self._containers.pop(self._container_of(entry), None)

    # ------------------------------------------------------------------ #
    # inspection
    # ------------------------------------------------------------------ #

    def count(self, tenant_id: Optional[str] = None) -> int:
        if tenant_id is None:
            return len(self._by_id)
        return sum(1 for e in self._by_id.values()
                   if e.tenant_id == tenant_id)

    def stats(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Live counters plus per-scope occupancy (optionally one tenant)."""

        if tenant_id is None:
            entries = list(self._by_id.values())
        else:
            entries = [e for e in self._by_id.values()
                       if e.tenant_id == tenant_id]
        per_scope = {s.value: 0 for s in MemoryScope}
        for entry in entries:
            per_scope[entry.scope.value] += 1
        return {
            **dict(self._stats),
            "total_entries": len(entries),
            "per_scope": per_scope,
            "tenant_id": tenant_id,
        }

    # ------------------------------------------------------------------ #
    # persistence (deterministic JSON; FileStore overrides _save/_load)
    # ------------------------------------------------------------------ #

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic serializable snapshot of every entry."""

        entries = sorted((e.to_dict() for e in self._by_id.values()),
                         key=lambda d: d["memory_id"])
        return {"format": 1, "entries": entries}

    def restore(self, data: Dict[str, Any]) -> None:
        """Rebuild state from a ``snapshot()`` payload."""

        if data.get("format") != 1:
            raise ValueError(f"unsupported snapshot format: {data.get('format')!r}")
        self._by_id.clear()
        self._containers.clear()
        for raw in data["entries"]:
            entry = MemoryEntry.from_dict(raw)
            self._by_id[entry.memory_id] = entry
            cid = self._container_of(entry)
            self._containers.setdefault(cid, OrderedDict())[entry.memory_id] = entry

    def _save(self) -> None:  # no-op for the in-memory store
        return None


class FileStore(MemoryStore):
    """A ``MemoryStore`` that persists to a JSON file after every mutation.

    Writes are atomic (temp file + ``os.replace``) and deterministic
    (``sort_keys=True``), so a reload after any crash leaves a valid file.
    """

    def __init__(self, path: Union[str, Path], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.path = Path(path)
        self._load()

    def _save(self) -> None:
        payload = json.dumps(self.snapshot(), sort_keys=True, indent=2)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            self.restore(json.load(handle))


# The seam-parity alias used across sibling lanes ("store.py exposes
# InMemoryStore"): in-memory is the default, so it is just ``MemoryStore``.
InMemoryStore = MemoryStore
