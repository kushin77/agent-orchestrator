"""portal.server.auditlog — per-tenant tamper-evident audit chain.

The Audit view and every mutation use this append-only, hash-chained ledger.
The record shape and the verify semantics consume the frozen telemetry/ledger
(issue #31) vocabulary — ``schemaVersion``/``seq``/``ts``/``tenantId``/
``actor`` (``kind:id``)/``action``/``resource``/``prevHash``/``hash`` — and
never redefine it. ``verify`` recomputes every link and returns a ledger-style
verdict: ``OK`` or ``NOT-OK`` with the first broken record. Tampering with any
field, deleting a record or reordering records is detected.

This is the offline console's own chain (stdlib sha256) so the whole portal is
self-contained; a production deployment maps the same seam onto the real
``telemetry/ledger`` store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1
GENESIS_PREV_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    """Deterministic JSON over the record payload (hash field excluded)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """One chained audit record (telemetry/ledger wire vocabulary)."""

    seq: int
    ts: str
    tenant_id: str
    actor: str  # `kind:id`
    action: str
    resource: Optional[str] = None
    detail: Optional[str] = None
    prev_hash: str = GENESIS_PREV_HASH
    hash: str = ""

    def payload(self) -> dict[str, Any]:
        """Wire payload WITHOUT the ``hash`` field (what the hash covers)."""
        return {
            "schemaVersion": SCHEMA_VERSION,
            "seq": self.seq,
            "ts": self.ts,
            "tenantId": self.tenant_id,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "detail": self.detail,
            "prevHash": self.prev_hash,
        }

    def as_json(self) -> dict[str, Any]:
        data = self.payload()
        data["hash"] = self.hash
        return data

    def recompute_hash(self) -> str:
        return _sha256(_canonical(self.payload()))


class AuditLedger:
    """Append-only per-tenant hash chain."""

    def __init__(self, tenant_id: str, seed: Optional[Iterable[AuditRecord]] = None) -> None:
        if not tenant_id:
            raise ValueError("audit ledger requires a tenant id")
        self.tenant_id = tenant_id
        self._records: list[AuditRecord] = []
        if seed:
            for record in seed:
                self._append_record(record)

    # -- append -------------------------------------------------------------
    def _append_record(self, record: AuditRecord) -> AuditRecord:
        if record.tenant_id != self.tenant_id:
            raise ValueError(
                f"record tenant {record.tenant_id!r} does not match chain tenant "
                f"{self.tenant_id!r}"
            )
        expected_seq = len(self._records) + 1
        if record.seq != expected_seq:
            raise ValueError(
                f"seed record seq {record.seq} != expected {expected_seq}"
            )
        if self._records:
            if record.prev_hash != self._records[-1].hash:
                raise ValueError("seed record does not link to the chain tail")
        elif record.prev_hash != GENESIS_PREV_HASH:
            raise ValueError("first record must use the genesis prev hash")
        if not record.hash:
            raise ValueError("seed record must carry a computed hash")
        if record.hash != record.recompute_hash():
            raise ValueError("seed record hash does not match its payload")
        self._records.append(record)
        return record

    def append(
        self,
        actor: str,
        action: str,
        *,
        tenant_id: Optional[str] = None,
        resource: Optional[str] = None,
        detail: Optional[str] = None,
        ts: str = "",
    ) -> AuditRecord:
        if tenant_id is not None and tenant_id != self.tenant_id:
            raise ValueError(
                f"append tenant {tenant_id!r} does not match chain tenant "
                f"{self.tenant_id!r}"
            )
        tenant = self.tenant_id
        prev_hash = self._records[-1].hash if self._records else GENESIS_PREV_HASH
        seq = len(self._records) + 1
        record = AuditRecord(
            seq=seq,
            ts=ts or _utcnow(),
            tenant_id=tenant,
            actor=actor,
            action=action,
            resource=resource,
            detail=detail,
            prev_hash=prev_hash,
            hash="",
        )
        fixed = AuditRecord(
            seq=record.seq,
            ts=record.ts,
            tenant_id=record.tenant_id,
            actor=record.actor,
            action=record.action,
            resource=record.resource,
            detail=record.detail,
            prev_hash=record.prev_hash,
            hash=record.recompute_hash(),
        )
        self._records.append(fixed)
        return fixed

    # -- read ---------------------------------------------------------------
    def records(self) -> list[AuditRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> AuditRecord:
        return self._records[index]

    # -- verify chain -------------------------------------------------------
    def verify(self) -> dict[str, Any]:
        """Recompute the chain; ``status`` is ``OK`` or ``NOT-OK`` (first bad
        record reported in ``detail``)."""
        prev = GENESIS_PREV_HASH
        for index, record in enumerate(self._records):
            if record.prev_hash != prev:
                return {
                    "status": "NOT-OK",
                    "seq": record.seq,
                    "detail": f"record {record.seq} prevHash link broken",
                }
            recomputed = record.recompute_hash()
            if recomputed != record.hash:
                return {
                    "status": "NOT-OK",
                    "seq": record.seq,
                    "detail": f"record {record.seq} hash mismatch",
                }
            if record.tenant_id != self.tenant_id:
                return {
                    "status": "NOT-OK",
                    "seq": record.seq,
                    "detail": f"record {record.seq} tenant binding broken",
                }
            prev = record.hash
        return {"status": "OK", "seq": len(self._records), "detail": "chain verifies"}

    # -- test helper (tamper detection) -------------------------------------
    def tamper(self, seq: int, *, field: str, value: Any) -> None:
        """Mutate one field of one record in place (used to prove NOT-OK)."""
        if not (1 <= seq <= len(self._records)):
            raise IndexError(f"no record at seq {seq}")
        record = self._records[seq - 1]
        data = record.as_json()
        if field not in data:
            raise KeyError(field)
        data[field] = value
        self._records[seq - 1] = AuditRecord(
            seq=int(data["seq"]),
            ts=str(data["ts"]),
            tenant_id=str(data["tenantId"]),
            actor=str(data["actor"]),
            action=str(data["action"]),
            resource=data["resource"],
            detail=data["detail"],
            prev_hash=str(data["prevHash"]),
            hash=str(data["hash"]),
        )


def _utcnow() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
