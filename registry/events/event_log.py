"""Append-only agent lifecycle event log (registry/events, issue #10).

Audit trail for the Agent Identity + Registry service. Every mutating registry
operation (register / activate / pause / retire, plus task-route and session
issuance) appends one event record; the log is strictly append-only and
hash-chained, so an edit, deletion or reordering of any past record is detected
by ``verify``.

Design (adapted from the CMR ``registry/events`` pattern - JSON Lines, one
object per line, header comments; see docs/CANNIBALIZATION.md): each record
carries a monotonic ``seq``, an RFC 3339 ``ts``, a ``prevHash`` that links it to
the previous record, and a ``hash`` computed as the sha256 of the record's
canonical JSON (every field except ``hash``). The first record's ``prevHash``
is the fixed genesis hash, so the chain is anchored. ``verify`` recomputes
every hash and every link; passing the previously observed tail state also
detects truncation.

Field naming follows the repo wire convention (camelCase ``tenantId`` /
``agentId``; ``ts`` kept lowercase from the CMR event contract). The ``event``
enum is closed; the schema in ``event.schema.json`` mirrors the record shape.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Fixed anchor for the first record of the chain. Not a secret: it is a
# well-known constant that makes the chain start deterministic.
GENESIS_HASH = "0" * 64

# Closed set of registry lifecycle / audit event kinds this log accepts. A
# record whose kind is not listed here is refused (fail closed).
EVENT_KINDS = ("register", "activate", "pause", "retire", "route", "session")

# Canonical JSON serialization used for hashing: deterministic across runs.
_JSON_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}

# Header written at the top of a newly created log file.
_HEADER = (
    "# agent lifecycle registry event log - append-only, one JSON object per line\n"
    "# Schema: registry/events/event.schema.json\n"
    "# Lines starting with '#' are comments and are ignored by readers.\n"
)


class EventLogError(Exception):
    """Base class for event log errors."""


class EventLogIntegrityError(EventLogError):
    """Raised when an append-only integrity violation is detected."""


def now_utc() -> str:
    """RFC 3339 UTC timestamp, e.g. ``2026-09-08T12:00:00Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_record(record: Dict[str, Any]) -> str:
    """sha256 of the record's canonical JSON (the ``hash`` field excluded)."""
    body = {key: value for key, value in record.items() if key != "hash"}
    canonical = json.dumps(body, **_JSON_KW).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _chain_verify(records: List[Dict[str, Any]]) -> Tuple[int, str]:
    """Recompute the hash chain over ``records``.

    Returns the current tail ``(last_seq, last_hash)``. Raises
    ``EventLogIntegrityError`` describing the first violation found.
    """
    expected_seq = 1
    previous = GENESIS_HASH
    for record in records:
        seq = record.get("seq")
        if seq != expected_seq:
            raise EventLogIntegrityError(
                f"seq gap or duplicate at record {seq!r} (expected {expected_seq})"
            )
        if record.get("prevHash") != previous:
            raise EventLogIntegrityError(
                f"broken hash link at record {expected_seq} (prevHash mismatch)"
            )
        if record.get("hash") != _hash_record(record):
            raise EventLogIntegrityError(
                f"hash mismatch at record {expected_seq} (content was altered)"
            )
        previous = record["hash"]
        expected_seq += 1
    return (expected_seq - 1, previous)


class EventLog:
    """Append-only hash-chained event log, optionally file-backed.

    With a ``path``, every ``append`` is also written to a JSON Lines file in
    append mode (never rewritten), so the on-disk log mirrors the in-memory
    chain. Reopening an existing file loads and verifies its integrity.
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
                    raise EventLogIntegrityError(
                        f"malformed event line in {path}: {exc}"
                    ) from exc
        # Verify before adopting: an on-disk log that fails the chain is
        # refused outright rather than silently trusted.
        _chain_verify(records)
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
        """The backing JSON Lines file path, if any."""
        return self._path

    def __len__(self) -> int:
        return len(self._records)

    def append(
        self,
        event: str,
        *,
        ts: Optional[str] = None,
        status: Optional[str] = None,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        actor: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append one event record and return it (never mutates past records)."""
        if event not in EVENT_KINDS:
            raise EventLogError(
                f"unknown event kind {event!r} (closed set: {', '.join(EVENT_KINDS)})"
            )
        ts = ts or now_utc()
        seq = len(self._records) + 1
        previous = self._records[-1]["hash"] if self._records else GENESIS_HASH
        record: Dict[str, Any] = {
            "seq": seq,
            "ts": ts,
            "event": event,
            "status": status or event,
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
        """Snapshot of every record in append order (safe to mutate)."""
        return [dict(record) for record in self._records]

    def tail(self) -> Optional[Dict[str, Any]]:
        """The most recent record, or None for an empty log."""
        if not self._records:
            return None
        return dict(self._records[-1])

    def state(self) -> Tuple[int, str]:
        """Current chain state ``(last_seq, last_hash)``; genesis when empty."""
        return _chain_verify(self._records)

    def verify(self, expected: Optional[Tuple[int, str]] = None) -> Tuple[int, str]:
        """Verify chain integrity and return the tail state.

        Detects edits, deletions and reordering of any past record. When
        ``expected`` (a tail state previously returned by ``state``) is given,
        also detects truncation of trailing records.
        """
        tail = _chain_verify(self._records)
        if expected is not None and tail != expected:
            raise EventLogIntegrityError(
                f"log tail {tail} does not match expected state {expected} "
                "(log truncated or extended unexpectedly)"
            )
        return tail


def open_event_log(path: str) -> EventLog:
    """Open a file-backed append-only event log, creating the file if needed.

    An existing file is loaded and chain-verified; a file that fails integrity
    is refused (EventLogIntegrityError).
    """
    return EventLog(path=path)
