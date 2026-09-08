"""engine/memory - forget / export APIs for tenant data-subject rights.

Issue kushin77/agent-orchestrator#25, acceptance criterion "Forget/export
APIs for tenant data-subject rights (GDPR-ready)".

The data-subject boundary in this product is the **tenant**: an operator
holding a tenant may export every memory that tenant owns and erase it.
These helpers are the audit-friendly surface over the store's
tenant-authority path (``list_entries`` + ``erase``); both return plain,
JSON-serializable structures and never cross the tenant boundary.

* ``export_memory`` - snapshot (export) of a tenant's memories, optionally
  narrowed by agent/session/scope/kind. Deterministically ordered.
* ``forget`` - erase a tenant's memories under the same filters, with a
  ``dry_run`` that reports what *would* be erased without deleting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from .model import MemoryKind, MemoryScope, parse_iso, to_utc
from .store import MemoryStore


@dataclass(frozen=True)
class ForgetReport:
    """Result of a GDPR forget operation."""

    tenant_id: str
    matched: int
    deleted: int
    dry_run: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "matched": self.matched,
            "deleted": self.deleted,
            "dry_run": self.dry_run,
        }


def _filters(agent_id: Optional[str], session_id: Optional[str],
             scope: Optional[MemoryScope],
             kinds: Optional[Sequence[MemoryKind]]) -> Dict[str, Any]:
    return {
        "agent_id": agent_id,
        "session_id": session_id,
        "scope": scope.value if scope else None,
        "kinds": [k.value for k in kinds] if kinds else None,
    }


def export_memory(store: MemoryStore, *, tenant_id: str,
                  agent_id: Optional[str] = None,
                  session_id: Optional[str] = None,
                  scope: Optional[MemoryScope] = None,
                  kinds: Optional[Sequence[MemoryKind]] = None) -> Dict[str, Any]:
    """Return a JSON-serializable export of matching memories for a tenant.

    Entries are sorted by ``memory_id`` so two exports of identical state are
    byte-identical (audit-friendly). Includes the filter used and a count so
    a data-subject request can be proven answered in full.
    """

    entries = store.list_entries(tenant_id=tenant_id, agent_id=agent_id,
                                 session_id=session_id, scope=scope,
                                 kinds=tuple(kinds) if kinds else None)
    payload = [e.to_dict() for e in entries]
    payload.sort(key=lambda d: d["memory_id"])
    return {
        "tenant_id": tenant_id,
        "filter": _filters(agent_id, session_id, scope, kinds),
        "count": len(payload),
        "entries": payload,
    }


def forget(store: MemoryStore, *, tenant_id: str,
           agent_id: Optional[str] = None,
           session_id: Optional[str] = None,
           scope: Optional[MemoryScope] = None,
           kinds: Optional[Sequence[MemoryKind]] = None,
           older_than: Optional[Any] = None,
           dry_run: bool = False) -> ForgetReport:
    """Erase matching memories on tenant authority (GDPR data-subject right).

    ``older_than`` accepts an ISO-8601 string or datetime; only entries
    created strictly before it are matched. With ``dry_run=True`` nothing is
    deleted and ``deleted`` is 0 while ``matched`` reports the would-be count.
    """

    entries = store.list_entries(tenant_id=tenant_id, agent_id=agent_id,
                                 session_id=session_id, scope=scope,
                                 kinds=tuple(kinds) if kinds else None)
    if older_than is not None:
        cutoff = to_utc(older_than) if isinstance(older_than, datetime) \
            else parse_iso(older_than)
        entries = [e for e in entries
                   if parse_iso(e.created_at) < cutoff]

    matched = len(entries)
    deleted = 0
    if not dry_run:
        for entry in entries:
            if store.erase(entry.memory_id, tenant_id=tenant_id):
                deleted += 1

    return ForgetReport(tenant_id=tenant_id, matched=matched,
                        deleted=deleted, dry_run=dry_run)
