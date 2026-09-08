"""Semantic cache: prompt-fingerprint dedup with TTL, LRU and accounting.

A cache HIT is zero-cost (the provider is never called) and IS accounted: the
facade (limits.limiter) emits a metering record with outcome=cache_hit,
cached=True, zero_cost=True on every hit, and the cache additionally keeps its
own hit/miss/set/eviction counters for observability.

Storage sits behind a CacheStore seam with an in-memory and a file-backed
implementation, so a later phase can point the same API at Redis/ValKey
without touching callers (mirrors the cannibalized fleet semantic-cache which
used a file dir of keyed entries with TTL eviction).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from limits.fingerprint import cache_key as build_cache_key

_MISS = object()


@dataclass
class CacheEntry:
    """One stored value plus the timestamps that drive TTL and LRU eviction."""

    value: str
    stored_at: float
    expires_at: float
    accessed_at: float

    def expired(self, now: float) -> bool:
        return now > self.expires_at

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "stored_at": self.stored_at,
            "expires_at": self.expires_at,
            "accessed_at": self.accessed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CacheEntry":
        return cls(
            value=data["value"],
            stored_at=float(data["stored_at"]),
            expires_at=float(data["expires_at"]),
            accessed_at=float(data.get("accessed_at", data["stored_at"])),
        )


class CacheStore(Protocol):
    """Persistence seam: a keyed map of CacheEntry with iteration + length."""

    def get(self, key: str) -> CacheEntry | None: ...
    def set(self, key: str, entry: CacheEntry) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def keys(self) -> list[str]: ...
    def __len__(self) -> int: ...


class MemoryCacheStore:
    """In-memory store (the default; also the base for unit tests)."""

    def __init__(self) -> None:
        self._data: dict[str, CacheEntry] = {}

    def get(self, key: str) -> CacheEntry | None:
        return self._data.get(key)

    def set(self, key: str, entry: CacheEntry) -> None:
        self._data[key] = entry

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def keys(self) -> list[str]:
        return list(self._data)

    def __len__(self) -> int:
        return len(self._data)


class FileCacheStore:
    """File-backed store: one JSON file per key under <key[:2]>/<key>.json.

    Writes are atomic (temp file + os.replace) and the directory is sharded by
    the first two hex chars so no single directory grows unbounded.  This is
    the durable seam; re-opening a SemanticCache over the same directory sees
    the entries persisted by a previous process.
    """

    def __init__(self, base_dir: str | os.PathLike, clock=time.time) -> None:
        self.base_dir = Path(base_dir)
        self.clock = clock
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.base_dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> CacheEntry | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return CacheEntry.from_dict(json.load(fh))
        except (OSError, ValueError, KeyError):
            return None

    def set(self, key: str, entry: CacheEntry) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entry.to_dict(), fh)
        os.replace(tmp, path)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def clear(self) -> None:
        for sub in self.base_dir.iterdir():
            if sub.is_dir():
                for f in sub.glob("*.json"):
                    f.unlink()

    def keys(self) -> list[str]:
        keys: list[str] = []
        for sub in self.base_dir.iterdir():
            if sub.is_dir():
                for f in sub.glob("*.json"):
                    keys.append(f.stem)
        return keys

    def __len__(self) -> int:
        return len(self.keys())


@dataclass(frozen=True)
class CacheResolution:
    """Result of resolving a prompt against the semantic cache."""

    hit: bool
    key: str
    value: str | None = None


class SemanticCache:
    """TTL cache keyed by (tenant, model tier, normalized prompt fingerprint).

    ``resolve()`` is the high-level path used by the facade: it builds the
    scope-bound cache key, returns the value on a hit and None on a miss, and
    accounts both into ``stats()``.  ``get/set`` are the low-level keyed API.
    """

    def __init__(
        self,
        store: CacheStore | None = None,
        *,
        default_ttl: float = 3600.0,
        max_entries: int = 10_000,
        clock=time.time,
        lowercase: bool = False,
        strip_punctuation: bool = False,
        schema_version: int = 1,
    ) -> None:
        self.store = store if store is not None else MemoryCacheStore()
        self.default_ttl = float(default_ttl)
        self.max_entries = int(max_entries)
        self.clock = clock
        self.lowercase = lowercase
        self.strip_punctuation = strip_punctuation
        self.schema_version = schema_version
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "evictions": 0,
            "bytes": 0,
        }

    # --- low-level keyed API -------------------------------------------------
    def get(self, key: str) -> str | None:
        """Return the value for ``key`` or None (expired entries are evicted)."""
        now = self.clock()
        entry = self.store.get(key)
        if entry is None:
            self._stats["misses"] += 1
            return None
        if entry.expired(now):
            self.store.delete(key)
            self._stats["evictions"] += 1
            self._stats["misses"] += 1
            return None
        entry.accessed_at = now
        self.store.set(key, entry)
        self._stats["hits"] += 1
        return entry.value

    def set(self, key: str, value: str, ttl: float | None = None) -> None:
        """Store ``value`` under ``key`` with a (default) TTL."""
        now = self.clock()
        ttl = self.default_ttl if ttl is None else float(ttl)
        self._evict_if_full(now)
        entry = CacheEntry(
            value=value,
            stored_at=now,
            expires_at=now + ttl,
            accessed_at=now,
        )
        self.store.set(key, entry)
        self._stats["sets"] += 1
        self._stats["bytes"] += len(value.encode("utf-8"))

    def delete(self, key: str) -> None:
        """Remove a single entry (no stats mutation: deletes are not sets)."""
        self.store.delete(key)

    def clear(self) -> None:
        self.store.clear()

    def prune(self) -> int:
        """Drop expired entries; returns how many were evicted."""
        now = self.clock()
        removed = 0
        for key in self.store.keys():
            entry = self.store.get(key)
            if entry is not None and entry.expired(now):
                self.store.delete(key)
                self._stats["evictions"] += 1
                removed += 1
        return removed

    # --- high-level prompt API ----------------------------------------------
    def resolve(
        self,
        tenant: str,
        model_tier: str,
        prompt: str,
        *,
        task_type: str | None = None,
    ) -> CacheResolution:
        """Resolve a prompt; hit=True (with value) only if all scopes match."""
        key = self._key(tenant, model_tier, prompt, task_type=task_type)
        value = self.get(key)
        return CacheResolution(hit=value is not None, key=key, value=value)

    def store_result(
        self,
        tenant: str,
        model_tier: str,
        prompt: str,
        value: str,
        *,
        task_type: str | None = None,
        ttl: float | None = None,
    ) -> str:
        """Cache a provider result so an identical later call is a zero-cost hit."""
        key = self._key(tenant, model_tier, prompt, task_type=task_type)
        self.set(key, value, ttl=ttl)
        return key

    def stats(self) -> dict:
        """Hit/miss/set/eviction counters plus entries and hit rate."""
        entries = len(self.store)
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = round(100.0 * self._stats["hits"] / total, 2) if total else 0.0
        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "sets": self._stats["sets"],
            "evictions": self._stats["evictions"],
            "bytes": self._stats["bytes"],
            "entries": entries,
            "hit_rate_pct": hit_rate,
        }

    def _key(
        self,
        tenant: str,
        model_tier: str,
        prompt: str,
        *,
        task_type: str | None,
    ) -> str:
        return build_cache_key(
            tenant,
            model_tier,
            prompt,
            task_type=task_type,
            lowercase=self.lowercase,
            strip_punctuation=self.strip_punctuation,
            schema_version=self.schema_version,
        )

    def _evict_if_full(self, now: float) -> None:
        if self.max_entries <= 0 or len(self.store) < self.max_entries:
            return
        # Expired entries first, then the least-recently-accessed entry.
        self.prune()
        if len(self.store) < self.max_entries:
            return
        oldest_key: str | None = None
        oldest_access = float("inf")
        for key in self.store.keys():
            entry = self.store.get(key)
            if entry is None:
                continue
            if entry.accessed_at < oldest_access:
                oldest_access = entry.accessed_at
                oldest_key = key
        if oldest_key is not None:
            self.store.delete(oldest_key)
            self._stats["evictions"] += 1
