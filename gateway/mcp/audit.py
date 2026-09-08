"""Append-only, hash-chained tool-call audit ledger (gateway/mcp, issue #20).

The audit record shape is consumed from the registry/events contract (issue
#10, ``registry/events/event_log.py``): one JSON object per line, monotonic
``seq``, RFC 3339 ``ts``, a ``prevHash`` chaining each record to its
predecessor, and a ``hash`` = sha256 of the record's canonical JSON (every
field except ``hash``). The first record's ``prevHash`` is the fixed genesis
hash. Any edit, deletion or reordering breaks ``verify``.

Each tool call - allowed or denied - appends exactly one record answering
who/what/tenant/result:

- who:    ``actor`` (the session subject) + ``agentId``
- what:   ``detail.tool`` / ``detail.permission`` (+ ``detail.arguments``)
- tenant: ``tenantId``
- result: ``status`` (``ok`` / ``error`` / denial reason) + ``detail.result``

The closed event kinds here are the tool-call pair ``tool_call`` and
``tool_call_denied``; the registry lifecycle log keeps its own closed enum
(register/activate/...). The gateway writes through an injected
:class:`AuditSink` so a deployment may substitute any append-only ledger with
the same shape.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple

GENESIS_HASH = "0" * 64

# Closed kinds this ledger accepts (the gateway's tool-call audit vocabulary).
EVENT_KINDS = ("tool_call", "tool_call_denied")

# Status vocabulary per kind.
_STATUS_FOR_KIND = {
    "tool_call": ("ok", "error"),
    "tool_call_denied": ("context", "authn", "authz", "allowlist", "rate", "unknown"),
}

_JSON_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}

_HEADER = (
    "# gateway/mcp tool-call audit ledger - append-only, one JSON object per line\n"
    "# Schema: gateway/mcp/audit.schema.json\n"
    "# Lines starting with '#' are comments and are ignored by readers.\n"
)


def now_utc() -> str:
    """RFC 3339 UTC timestamp, e.g. ``2026-09-08T12:00:00Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_record(record: Dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "hash"}
    canonical = json.dumps(body, **_JSON_KW).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _chain_verify(records: List[Dict[str, Any]]) -> Tuple[int, str]:
    """Recompute the chain; return ``(last_seq, last_hash)`` at the tail.

    Raises :class:`AuditLogIntegrityError` describing the first violation.
    """
    expected_seq = 1
    previous = GENESIS_HASH
    for record in records:
        seq = record.get("seq")
        if seq != expected_seq:
            raise AuditLogIntegrityError(
                f"seq gap or duplicate at record {seq!r} (expected {expected_seq})"
            )
        if record.get("prevHash") != previous:
            raise AuditLogIntegrityError(
                f"broken hash link at record {expected_seq} (prevHash mismatch)"
            )
        if record.get("hash") != _hash_record(record):
            raise AuditLogIntegrityError(
                f"hash mismatch at record {expected_seq} (content was altered)"
            )
        previous = record["hash"]
        expected_seq += 1
    return (expected_seq - 1, previous)


class AuditLogIntegrityError(Exception):
    """Raised when an append-only integrity violation is detected."""


class AuditLogError(Exception):
    """Base class for audit log errors."""


class AuditSink(Protocol):
    """The injected audit ledger the gateway writes to."""

    def append(
        self,
        event: str,
        *,
        status: Optional[str] = None,
        ts: Optional[str] = None,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        actor: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...

    def events(self) -> List[Dict[str, Any]]: ...


class HashChainAuditLog:
    """Append-only hash-chained tool-call ledger, optionally JSONL file-backed.

    With a ``path`` every append is also written in append mode (never
    rewritten); reopening loads and chain-verifies the file. A malformed line
    or a broken chain is a hard :class:`AuditLogIntegrityError` - a gate that
    cannot fail is a formality.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._records: List[Dict[str, Any]] = []
        if path is not None and os.path.exists(path):
            self._load(path)

    # ------------------------------------------------------------------ #
    # persistence helpers
    # ------------------------------------------------------------------ #
    def _load(self, path: str) -> None:
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                try:
                    records.append(json.loads(stripped))
                except ValueError as exc:
                    raise AuditLogIntegrityError(
                        f"malformed audit line in {path}: {exc}"
                    ) from exc
        try:
            _chain_verify(records)
        except Exception as exc:
            raise AuditLogIntegrityError(f"broken audit chain in {path}: {exc}") from exc
        self._records = records

    def _append_line(self, record: Dict[str, Any]) -> None:
        if self._path is None:
            return
        if not os.path.exists(self._path):
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(_HEADER)
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, **_JSON_KW) + "\n")

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    @property
    def path(self) -> Optional[str]:
        return self._path

    def __len__(self) -> int:
        return len(self._records)

    def append(
        self,
        event: str,
        *,
        status: Optional[str] = None,
        ts: Optional[str] = None,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        actor: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if event not in EVENT_KINDS:
            raise AuditLogError(
                f"unknown event kind {event!r} (closed set: {', '.join(EVENT_KINDS)})"
            )
        allowed_statuses = _STATUS_FOR_KIND[event]
        if status is not None and status not in allowed_statuses:
            raise AuditLogError(
                f"status {status!r} is not valid for event {event!r} "
                f"(closed set: {', '.join(allowed_statuses)})"
            )
        ts = ts or now_utc()
        seq = len(self._records) + 1
        previous = self._records[-1]["hash"] if self._records else GENESIS_HASH
        record: Dict[str, Any] = {
            "seq": seq,
            "ts": ts,
            "event": event,
            "status": status if status is not None else event,
            "tenantId": tenant_id,
            "agentId": agent_id,
            "actor": actor,
            "detail": detail,
        }
        record["prevHash"] = previous
        record["hash"] = _hash_record(record)
        self._records.append(record)
        self._append_line(record)
        return dict(record)

    def events(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._records]

    def tail(self) -> Optional[Dict[str, Any]]:
        if not self._records:
            return None
        return dict(self._records[-1])

    def state(self) -> Tuple[int, str]:
        return _chain_verify(self._records)

    def verify(self, expected: Optional[Tuple[int, str]] = None) -> Tuple[int, str]:
        """Recompute + optionally compare the chain to a prior ``state()``.

        Passing the previously observed tail also detects truncation (silently
        dropping trailing records is otherwise undetectable from the file).
        """
        state = self.state()
        if expected is not None and state != expected:
            raise AuditLogIntegrityError(
                f"audit log truncated: state {state} != expected {expected}"
            )
        return state
