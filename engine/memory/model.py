"""engine/memory - scoped memory model: scopes, kinds, entries, time.

Issue kushin77/agent-orchestrator#25 ("21 Scoped agent memory store
(tenant/agent/session + TTL)"), engine lane, phase 3. The organizational
memory of each tenant's agent org.

This module owns the *shape* of a memory entry and the scope vocabulary. It
is a pure data module: no storage, no retrieval, no enrichment - those live
in ``store`` / ``retrieval`` / ``enrich``. The scope enum mirrors the three
levels of the harvested vscode-memory ``MemoryType`` (repository / session /
user) mapped onto a multi-tenant control plane:

================  ============================  ================================
Store scope       vscode-memory ``MemoryType``  Meaning
================  ============================  ================================
``TENANT``        ``repository``                Tenant-global / org memory
``AGENT``         ``user``                      One agent's long-term memory
``SESSION``       ``session``                   One run/conversation memory
================  ============================  ================================

``registry/profiles`` (issue #9, contract-frozen) declares each AgentProfile's
``memoryScope`` as a subset of ``[user, session, repository]``. That enum is
*consumed*, never redefined, here: ``MemoryScope.from_profile_value`` maps a
profile value to the store scope it grants read/write over.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# time helpers (single source of truth so tests can pin ``now``)
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    """The store's clock. Tests inject fixed instants via ``now=`` instead."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """ISO-8601 UTC string for this instant."""
    return utcnow().isoformat()


def to_utc(value: datetime) -> datetime:
    """Coerce a naive/aware datetime to an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string; naive strings are treated as UTC."""
    return to_utc(datetime.fromisoformat(value))


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

# Mapping from the contract-frozen profile ``memoryScope`` enum values
# (registry/profiles/agent-profile.schema.json, issue #9) to store scopes.
# Module-level on purpose: an ``Enum`` turns ANY class-body value into a
# member, which would pollute ``for s in MemoryScope`` iteration.
_PROFILE_SCOPE_MAP = {
    "repository": "tenant",  # shared org/project knowledge -> tenant-global
    "user": "agent",  # persistent personal memory -> one agent's memory
    "session": "session",  # conversation scope -> run session
}


class MemoryScope(Enum):
    """Where a memory lives. Strictly nested: tenant > agent > session."""

    TENANT = "tenant"
    AGENT = "agent"
    SESSION = "session"

    @classmethod
    def from_profile_value(cls, value: str) -> "MemoryScope":
        """Map a frozen profile ``memoryScope`` value to a store scope.

        Raises ``ValueError`` for anything outside the frozen profile enum so
        a schema change on either side cannot silently widen access.
        """
        try:
            return cls(_PROFILE_SCOPE_MAP[value])
        except KeyError:
            allowed = ", ".join(sorted(_PROFILE_SCOPE_MAP))
            raise ValueError(
                f"unknown profile memoryScope value {value!r}; "
                f"expected one of: {allowed}"
            ) from None

    @classmethod
    def from_profile_values(cls, values: List[str]) -> Tuple["MemoryScope", ...]:
        """Map a profile ``memoryScope`` list to a deduplicated scope tuple."""
        if not values:
            return tuple(cls)
        seen = []
        for value in values:
            scope = cls.from_profile_value(value)
            if scope not in seen:
                seen.append(scope)
        return tuple(seen)

    @property
    def rank(self) -> int:
        """Specificity rank used for deterministic block ordering."""
        return {MemoryScope.SESSION: 0, MemoryScope.AGENT: 1,
                MemoryScope.TENANT: 2}[self]


class MemoryKind(Enum):
    """The two memory families the issue names: semantic + episodic.

    ``SEMANTIC``  durable knowledge ("the tenant's release process is X").
    ``EPISODIC``  event records ("session S ran deploy Y").
    """

    SEMANTIC = "semantic"
    EPISODIC = "episodic"


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #


def _stable_hash(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:20]


def memory_id_for(tenant_id: str, agent_id: Optional[str],
                  session_id: Optional[str], key: str) -> str:
    """Deterministic, content-addressed id for ``(container, key)``.

    The id is a pure hash of the *identity* (tenant + agent + session + key)
    - never a wall-clock salt - so it is stable across processes, across a
    file-store reload, and safe to expose beside the prompt-cache machinery
    (a content hash cannot break prefix stability the way a timestamp can).
    """
    return _stable_hash(tenant_id, agent_id or "", session_id or "", key)


def scope_for(tenant_id: str, agent_id: Optional[str],
              session_id: Optional[str]) -> MemoryScope:
    """Derive the memory scope implied by which ids are present.

    * no agent and no session        -> TENANT (tenant-global)
    * an agent and no session        -> AGENT  (agent long-term)
    * a session (agent optional)     -> SESSION
    """
    if session_id is not None:
        return MemoryScope.SESSION
    if agent_id is not None:
        return MemoryScope.AGENT
    return MemoryScope.TENANT


def validate_container(tenant_id: str, scope: MemoryScope,
                       agent_id: Optional[str],
                       session_id: Optional[str]) -> None:
    """Reject id/scope combinations that cannot name a container."""
    if not tenant_id:
        raise ValueError("tenant_id is required for every memory")
    if scope is MemoryScope.AGENT and not agent_id:
        raise ValueError("AGENT-scope memory requires an agent_id")
    if scope is MemoryScope.SESSION and session_id is None:
        raise ValueError("SESSION-scope memory requires a session_id")
    if scope is MemoryScope.TENANT and (agent_id or session_id):
        raise ValueError(
            "TENANT-scope memory must not carry agent_id/session_id"
        )


def container_id(scope: MemoryScope, tenant_id: str,
                 agent_id: Optional[str],
                 session_id: Optional[str]) -> str:
    """Canonical container key used for per-container capacity/eviction."""
    if scope is MemoryScope.TENANT:
        return f"tenant:{tenant_id}"
    if scope is MemoryScope.AGENT:
        return f"agent:{tenant_id}:{agent_id}"
    return f"session:{tenant_id}:{agent_id or ''}:{session_id}"


# --------------------------------------------------------------------------- #
# text normalization
# --------------------------------------------------------------------------- #

_WS_RUN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Collapse whitespace runs to single spaces and strip the edges.

    Whitespace is presentation, characters are content (codeidx canonical
    discipline, .research/fleet/code-indexing/docs/prompt-cache.md): two
    texts that differ only in whitespace are the same logical memory.
    """
    if not text:
        return ""
    return _WS_RUN.sub(" ", text).strip()


# --------------------------------------------------------------------------- #
# the entry
# --------------------------------------------------------------------------- #


@dataclass
class MemoryEntry:
    """A single scoped memory with TTL, access tracking and embedding.

    ``memory_id`` is derived from the container + key (see ``memory_id_for``),
    so storing the same container/key twice is an idempotent upsert rather
    than unbounded growth.
    """

    tenant_id: str
    scope: MemoryScope
    key: str
    text: str
    kind: MemoryKind = MemoryKind.SEMANTIC
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    memory_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=iso_now)
    updated_at: Optional[str] = None
    ttl_seconds: Optional[int] = None
    access_count: int = 0
    last_accessed: Optional[str] = None
    embedding: Optional[List[float]] = None

    def __post_init__(self) -> None:
        validate_container(self.tenant_id, self.scope, self.agent_id,
                           self.session_id)
        self.text = normalize_text(self.text)
        if self.memory_id is None:
            self.memory_id = memory_id_for(self.tenant_id, self.agent_id,
                                           self.session_id, self.key)

    # -- lifecycle --------------------------------------------------------- #

    def expires_at(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """The instant this entry expires, or None for a non-expiring entry."""
        if not self.ttl_seconds:
            return None
        return parse_iso(self.created_at) + timedelta(
            seconds=self.ttl_seconds
        )

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """True once ``now`` has passed ``created_at + ttl_seconds``.

        A zero/absent TTL never expires (tenant-global org memory persists
        until explicitly forgotten - GDPR path - or evicted by capacity).
        """
        if not self.ttl_seconds:
            return False
        return (now or utcnow()) >= self.expires_at()

    def touch(self, now: Optional[datetime] = None) -> None:
        """Record an access (used by the LRU ordering and stats)."""
        instant = (now or utcnow()).isoformat()
        self.access_count += 1
        self.last_accessed = instant

    # -- serialization ----------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        """Deterministic dict form (values sorted by key on serialization)."""
        return {
            "memory_id": self.memory_id,
            "tenant_id": self.tenant_id,
            "scope": self.scope.value,
            "kind": self.kind.value,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "key": self.key,
            "text": self.text,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ttl_seconds": self.ttl_seconds,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "embedding": list(self.embedding) if self.embedding else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """Rebuild an entry from ``to_dict`` output."""
        payload = dict(data)
        payload["scope"] = MemoryScope(payload["scope"])
        payload["kind"] = MemoryKind(payload["kind"])
        if payload.get("embedding") is not None:
            payload["embedding"] = list(payload["embedding"])
        return cls(**payload)


class MemoryIsolationError(PermissionError):
    """A scope/tenant boundary was crossed on a direct memory access.

    Raised (and counted in ``store.stats()``) instead of silently returning
    None so a cross-tenant or cross-session leak is loud, never quiet.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
