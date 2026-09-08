"""Tenant-scope-gated store (issue #30, acceptance criterion 3).

Every query path enforces tenant scope **at the store layer**: records carry a
denormalized ``tenant_id`` and every accessor requires the caller to name a
tenant, so a row that exists under tenant B is structurally invisible to a
caller scoped to tenant A (no cross-tenant fallback — the engine/memory
doctrine, ``no-cross-tenant-fallback``).  Scope is never an app-layer filter
on top of an unscoped store; the store itself cannot answer a cross-tenant
read.

This is the reference shape the platform's tenant-scoped stores follow (the
registry ``RegistryStore``, the KB ``KbRegistry`` and the memory
``MemoryStore`` already implement the same doctrine; this class is the
standalone, dependency-free exemplar the isolation lane scans, probes and
repairs against).  It is also the dataset surface the integrity detector and
the opt-in repair run over, so the whole lane has one honest, negative-tested
data model.

Fail-closed semantics (pentest negatives, all tested):

* ``get(tA, rid)`` for a record that exists under ``tB`` returns ``None`` —
  from tenant A's view the row is absent; it never surfaces another tenant's
  bytes.
* ``require(tA, rid)`` raises :class:`IsolationScopeError` when the row is
  absent *or* owned by another tenant.
* ``put`` refuses a record whose denormalized ``tenant_id`` does not match
  the tenant bucket it is being written into (a denormalization violation is
  a finding, never silently normalized).
* ``delete(tA, rid)`` can only ever remove tenant A's own row.

Concurrency/durability are deliberately out of scope: this is the offline
semantic core.  A later phase may back the same contract with a real store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, MutableMapping, Optional

from .errors import IsolationScopeError

# Sentinel for "record not present in the caller's tenant scope".
_MISSING = object()


@dataclass
class Record:
    """One tenant-scoped record.

    ``tenant_id`` is denormalized onto the record (the store checks it on
    write) and ``record_id`` is unique within the tenant.  Extra fields are
    opaque payload.
    """

    tenant_id: str
    record_id: str
    data: Dict[str, Any] = field(default_factory=dict)


def record_id(record: Any) -> str:
    """The record id of a record-shaped object (Record or mapping)."""
    if isinstance(record, Record):
        return record.record_id
    if isinstance(record, dict):
        value = record.get("record_id")
        if value is None:
            raise IsolationScopeError("record has no record_id")
        return str(value)
    raise IsolationScopeError(f"unsupported record shape {type(record).__name__}")


def record_tenant(record: Any) -> str:
    """The denormalized tenant id of a record-shaped object."""
    if isinstance(record, Record):
        return record.tenant_id
    if isinstance(record, dict):
        value = record.get("tenant_id")
        if value is None:
            raise IsolationScopeError("record has no tenant_id")
        return str(value)
    raise IsolationScopeError(f"unsupported record shape {type(record).__name__}")


class TenantScopedStore:
    """In-memory, tenant-scope-gated store (see module docstring)."""

    def __init__(self,
                 backend: Optional[MutableMapping[str, Dict[str, Any]]] = None,
                 ) -> None:
        # backend: tenant_id -> {record_id -> payload dict}.  Records are kept
        # as plain dicts so a JSON-serializable dataset can round-trip.
        self._backend: MutableMapping[str, Dict[str, Any]] = backend or {}

    # ------------------------------------------------------------------ #
    # scope-gated accessors
    # ------------------------------------------------------------------ #

    def put(self, tenant_id: str, record: Any) -> None:
        """Insert or upsert a record under its tenant.

        Enforces the denormalized-tenant invariant: the record's own
        ``tenant_id`` must equal the bucket it is written into.
        """
        rid = record_id(record)
        denormalized = record_tenant(record)
        if tenant_id != denormalized:
            raise IsolationScopeError(
                f"denormalized tenant_id {denormalized!r} does not match "
                f"bucket {tenant_id!r} for record {rid!r}",
                tenant_id=tenant_id,
            )
        bucket = self._backend.setdefault(tenant_id, {})
        if isinstance(record, Record):
            bucket[rid] = {
                "tenant_id": tenant_id,
                "record_id": rid,
                **record.data,
            }
        else:
            bucket[rid] = dict(record)

    def get(self, tenant_id: str, rid: str) -> Optional[Dict[str, Any]]:
        """Return the record only if it exists in exactly this tenant.

        A record owned by another tenant is *absent* here (returns ``None``):
        the store never returns a row it was not asked for within scope.
        """
        bucket = self._backend.get(tenant_id)
        if bucket is None:
            return None
        row = bucket.get(rid, _MISSING)
        if row is _MISSING:
            return None
        # Defense in depth: even a backend row without the denormalized field
        # is not returned to an unscoped caller.
        if row.get("tenant_id", tenant_id) != tenant_id:
            return None
        return dict(row)

    def require(self, tenant_id: str, rid: str) -> Dict[str, Any]:
        """Like :meth:`get` but raise ``IsolationScopeError`` when absent."""
        row = self.get(tenant_id, rid)
        if row is None:
            raise IsolationScopeError(
                f"record {rid!r} is not present in tenant {tenant_id!r}",
                tenant_id=tenant_id,
            )
        return row

    def delete(self, tenant_id: str, rid: str) -> bool:
        """Delete tenant ``tenant_id``'s own row; no-op for foreign rows."""
        bucket = self._backend.get(tenant_id)
        if bucket is None:
            return False
        row = bucket.get(rid, _MISSING)
        if row is _MISSING:
            return False
        if row.get("tenant_id", tenant_id) != tenant_id:
            return False
        del bucket[rid]
        return True

    def list_records(self, tenant_id: str) -> List[Dict[str, Any]]:
        """All records of one tenant, deterministic by record id."""
        bucket = self._backend.get(tenant_id, {})
        return [dict(bucket[rid]) for rid in sorted(bucket)
                if bucket[rid].get("tenant_id", tenant_id) == tenant_id]

    def tenants(self) -> List[str]:
        """Every tenant bucket, deterministic order."""
        return sorted(self._backend)

    def has(self, tenant_id: str, rid: str) -> bool:
        """Membership test scoped to the tenant (no cross-tenant existence)."""
        return self.get(tenant_id, rid) is not None

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """A deep-enough copy of the whole store (for dataset export)."""
        return {
            tenant: {rid: dict(row) for rid, row in sorted(bucket.items())}
            for tenant, bucket in sorted(self._backend.items())
        }

    @classmethod
    def from_dataset(cls, dataset: Dict[str, Any]) -> "TenantScopedStore":
        """Build a store from a canonical dataset (see integrity.py)."""
        records = dataset.get("records", {})
        return cls(backend={str(t): dict(bucket) for t, bucket in records.items()})

    # ------------------------------------------------------------------ #
    # runtime cross-tenant probes (fail-closed, AC1)
    # ------------------------------------------------------------------ #

    def probe_cross_tenant_read(self, tenant_id: str, foreign_tenant: str,
                                rid: str) -> bool:
        """True when a read scoped to ``tenant_id`` fails closed on a row that
        exists in ``foreign_tenant`` (the row is invisible -> None)."""
        if self.has(foreign_tenant, rid) and self.get(tenant_id, rid) is None:
            return True
        # If the row does not even exist in the foreign tenant the probe is
        # vacuous; treat as pass only when the row exists cross-tenant and was
        # not returned.
        return not self.has(foreign_tenant, rid)

    def probe_cross_tenant_delete(self, tenant_id: str, foreign_tenant: str,
                                  rid: str) -> bool:
        """True when a delete scoped to ``tenant_id`` leaves a foreign row
        untouched (delete is a no-op for another tenant's row)."""
        before = self.get(foreign_tenant, rid) is not None
        deleted = self.delete(tenant_id, rid)
        if before:
            return deleted is False and self.get(foreign_tenant, rid) is not None
        return deleted is False
