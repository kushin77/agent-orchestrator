"""engine/memory - per-scope lifecycle policy: capacity, eviction, expiry.

Issue kushin77/agent-orchestrator#25, acceptance criterion "TTL + eviction
policies per scope". This module owns the *policy vocabulary* (what a scope
is allowed to hold and how long) plus the pure eviction primitive the store
applies inline on every write. The store is the stateful actor; this module
stays dependency-free so ``store`` can import it without a cycle.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from .model import MemoryEntry, MemoryScope


class EvictionPolicy(Enum):
    """Which entry a full scope container evicts first.

    Both policies pop the *head* of the store's per-container ``OrderedDict``;
    they differ in what the head means:

    ``LRU``  the store re-inserts an entry at the tail on every read, so the
             head is the least-recently-*accessed* entry (harvested
             vscode-memory ``ProjectLRU`` semantics). Default.
    ``FIFO`` the store never reorders on read, so the head is simply the
             oldest-*created* entry.
    """

    LRU = "lru"
    FIFO = "fifo"


@dataclass(frozen=True)
class ScopePolicy:
    """One scope class's retention budget.

    ``capacity``           max entries per *container* at this scope
                           (a container = tenant, or one tenant's agent, or
                           one tenant+agent's session).
    ``default_ttl_seconds`` applied when a write does not state a TTL; a
                           tenant-global default of ``None`` never expires
                           (org memory is erased only by GDPR forget or by
                           capacity eviction).
    """

    capacity: int
    default_ttl_seconds: Optional[int] = None


def default_policies() -> Dict[MemoryScope, ScopePolicy]:
    """Sane defaults keyed by scope class.

    Session memory is the most ephemeral (7-day TTL, smallest container),
    tenant-global org memory the most durable (no TTL - forgotten
    explicitly), agent memory in between (90-day TTL).
    """

    return {
        MemoryScope.TENANT: ScopePolicy(capacity=1000,
                                        default_ttl_seconds=None),
        MemoryScope.AGENT: ScopePolicy(capacity=500,
                                       default_ttl_seconds=90 * 24 * 3600),
        MemoryScope.SESSION: ScopePolicy(capacity=200,
                                         default_ttl_seconds=7 * 24 * 3600),
    }


def resolve_ttl(scope: MemoryScope, ttl_seconds: Optional[int],
                policies: Dict[MemoryScope, ScopePolicy]) -> Optional[int]:
    """An explicit TTL wins; otherwise the scope policy's default (may be
    None = never expires)."""
    if ttl_seconds is not None:
        return ttl_seconds
    policy = policies.get(scope)
    if policy is None:
        return None
    return policy.default_ttl_seconds


def evict_down_to(ordered: "OrderedDict[str, MemoryEntry]", *,
                  capacity: int, now: Optional[datetime] = None
                  ) -> List[MemoryEntry]:
    """Evict from the head until the container fits ``capacity``.

    Pure and deterministic: returns the evicted entries in eviction order.
    Whether the head means LRU or FIFO is decided by the store's read-side
    ordering discipline (see ``EvictionPolicy``).
    """

    evicted: List[MemoryEntry] = []
    while len(ordered) > max(0, capacity):
        victim_id = next(iter(ordered))
        victim = ordered.pop(victim_id)
        evicted.append(victim)
    return evicted
