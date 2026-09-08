"""DELIBERATELY LEAKY store — planted cross-tenant isolation bugs.

This is NEGATIVE-test material (a guard that must be CAUGHT): the scanner has
to report every planted pattern below.  The module mirrors a real #107-class
regression where a tenant-dimensioned store is reached by its inner id alone,
so a read can walk into another tenant's rows and a write can land outside
any tenant namespace.  Never use this shape in production code.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class LeakyStore:
    """A tenant-dimensioned store with three planted scope drops."""

    def __init__(self) -> None:
        # tenant_id -> {record_id -> record}  (tenant-dimensioned)
        self._records: Dict[str, Dict[str, Any]] = {}

    def put(self, tenant_id: str, record: Dict[str, Any]) -> None:
        """The one correct write: keys the index by tenant first."""
        self._records.setdefault(tenant_id, {})[record["record_id"]] = record

    def read_by_inner_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """PLANTED R1 (scope-drop read): reads the tenant-dimensioned index
        by the inner id alone — any tenant's row becomes reachable."""
        return self._records.get(record_id)

    def write_by_inner_id(self, record: Dict[str, Any]) -> None:
        """PLANTED R2 (scope-drop write): writes straight into the tenant
        dimension with an inner id — the row escapes its tenant."""
        self._records[record["record_id"]] = record

    def read_with_fallback(self, record_id: str,
                           default: Any) -> Dict[str, Any]:
        """PLANTED R3 (cross-tenant fallback): ``.get(inner_id, default)``
        can fall back across the tenant boundary."""
        return self._records.get(record_id, default)
