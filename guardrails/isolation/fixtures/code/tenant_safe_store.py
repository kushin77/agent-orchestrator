"""A correctly tenant-scoped store (positive fixture).

The scanner must report ``OK`` on this module: ``_records`` is a
tenant-dimensioned entity index and every access carries the tenant
dimension first.  Mirrors the platform's ``registry/service/store.py``
``RegistryStore`` shape.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class TenantSafeStore:
    """Tenant-keyed store; no cross-tenant access path exists."""

    def __init__(self) -> None:
        # tenant_id -> {record_id -> record}
        self._records: Dict[str, Dict[str, Any]] = {}

    def put(self, tenant_id: str, record: Dict[str, Any]) -> None:
        """Insert a record under its tenant (first key dimension = tenant)."""
        self._records.setdefault(tenant_id, {})[record["record_id"]] = record

    def get(self, tenant_id: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Read scoped to the tenant; a foreign row is simply absent."""
        return self._records.get(tenant_id, {}).get(record_id)

    def has(self, tenant_id: str, record_id: str) -> bool:
        """Membership test scoped to the tenant (no cross-tenant existence)."""
        return record_id in self._records.get(tenant_id, {})

    def delete(self, tenant_id: str, record_id: str) -> bool:
        """Delete only the calling tenant's own row."""
        bucket = self._records.get(tenant_id)
        if bucket is None or record_id not in bucket:
            return False
        del bucket[record_id]
        return True
