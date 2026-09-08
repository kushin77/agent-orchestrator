#!/usr/bin/env python3
"""Append-only, hash-chained event log for the agent-pack registry (#40).

Records every pack lifecycle transition and every tenant install/upgrade/
rollback/drift/consume action as one JSON object per line (JSON Lines,
optional file backing). The record shape is governed by
``pack-event.schema.json``; this module enforces the closed event enum and the
hash chain in code.

Integrity (consumed from the CMR registry/events + the repo registry/events
audit-log pattern, issue #10):

- **Append-only.** Records are never rewritten, deleted or reordered; a new
  record always chains to the previous one.
- **Hash chain.** ``hash = sha256(canonical JSON of every field except hash)``
  and ``prevHash`` of record *n* equals ``hash`` of record *n-1* (the first
  record's ``prevHash`` is the fixed all-zero genesis hash). Any edit,
  deletion or reordering of a past record breaks ``verify``.
- **Truncation.** Capture ``log.state()`` -> ``(last_seq, last_hash)`` and
  re-verify against it after reopening.
- **Malformed line is a hard failure.** Opening a log whose lines do not
  parse or whose chain does not verify raises ``PackEventIntegrityError``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from packs.attestation import canonical_bytes

GENESIS_HASH = "0" * 64

# Closed pack registry event kinds (pack-event.schema.json).
EVENT_KINDS = (
    "plan",
    "publish",
    "pause",
    "resume",
    "retire",
    "install",
    "upgrade",
    "rollback",
    "drift",
    "consume",
)

PACK_ID_RE = None  # resolved lazily in _check (keeps module import light)


class PackEventIntegrityError(Exception):
    """The log is malformed, tampered, or its chain does not verify."""


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_hash(record):
    body = {k: v for k, v in record.items() if k != "hash"}
    return __import__("hashlib").sha256(canonical_bytes(body)).hexdigest()


class PackEventLog:
    """Append-only pack event log (optionally file-backed JSON Lines)."""

    def __init__(self, path=None):
        self._records = []
        self._path = path
        if path is not None and os.path.exists(path):
            self._load(path)

    # -- loading / integrity ------------------------------------------------
    def _load(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        for lineno, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise PackEventIntegrityError(
                    "line %d is not valid JSON: %s" % (lineno, exc))
            errors = self._record_errors(record)
            if errors:
                raise PackEventIntegrityError(
                    "line %d invalid: %s" % (lineno, "; ".join(errors)))
            expected = _record_hash(record)
            if record["hash"] != expected:
                raise PackEventIntegrityError(
                    "line %d hash mismatch (tampered record)" % lineno)
            self._records.append(record)
        self.verify()

    @staticmethod
    def _record_errors(record):
        errors = []
        if not isinstance(record, dict):
            return ["record must be an object"]
        for field in ("seq", "ts", "event", "status", "pack", "version",
                      "tenantId", "actor", "detail", "prevHash", "hash"):
            if field not in record:
                errors.append("missing required field '%s'" % field)
        if not isinstance(record.get("seq"), int) or record["seq"] < 1:
            errors.append("seq must be an integer >= 1")
        if record.get("event") not in EVENT_KINDS:
            errors.append("event '%s' is not a closed pack event kind"
                          % record.get("event"))
        for field in ("ts", "status", "pack", "prevHash", "hash"):
            value = record.get(field)
            if not isinstance(value, str) or not value:
                errors.append("'%s' must be a non-empty string" % field)
        return errors

    # -- append --------------------------------------------------------------
    def append(self, event, status, pack, version=None, tenant_id=None,
               actor=None, detail=None):
        """Append one event record; returns it. Refuses unknown kinds."""
        if event not in EVENT_KINDS:
            raise PackEventIntegrityError(
                "append: '%s' is not a closed pack event kind" % event)
        seq = len(self._records) + 1
        prev_hash = self._records[-1]["hash"] if self._records else GENESIS_HASH
        record = {
            "seq": seq,
            "ts": now_utc(),
            "event": event,
            "status": status,
            "pack": pack,
            "version": version,
            "tenantId": tenant_id,
            "actor": actor,
            "detail": detail,
            "prevHash": prev_hash,
        }
        record["hash"] = _record_hash(record)
        self._records.append(record)
        if self._path is not None:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    # -- reads ---------------------------------------------------------------
    def records(self):
        return list(self._records)

    def state(self):
        """(last_seq, last_hash) for truncation detection."""
        if not self._records:
            return (0, GENESIS_HASH)
        return (self._records[-1]["seq"], self._records[-1]["hash"])

    def verify(self, expected=None):
        """Walk the whole chain; raise PackEventIntegrityError on tamper.

        ``expected`` = (seq, hash) captured earlier, used to detect trailing
        truncation.
        """
        prev_hash = GENESIS_HASH
        for i, record in enumerate(self._records, start=1):
            if record["seq"] != i:
                raise PackEventIntegrityError(
                    "seq gap/duplicate at position %d (found %d)"
                    % (i, record["seq"]))
            if record["prevHash"] != prev_hash:
                raise PackEventIntegrityError(
                    "record %d prevHash does not chain (tamper/reorder)"
                    % i)
            if record["hash"] != _record_hash(record):
                raise PackEventIntegrityError(
                    "record %d hash mismatch (tampered record)" % i)
            prev_hash = record["hash"]
        if expected is not None:
            tail = (self._records[-1]["seq"],
                    self._records[-1]["hash"]) if self._records \
                else (0, GENESIS_HASH)
            if tail != tuple(expected):
                raise PackEventIntegrityError(
                    "log tail %s does not match expected %s (truncation)"
                    % (tail, tuple(expected)))


def open_pack_event_log(path=None):
    """Open (creating if needed) a file-backed pack event log."""
    if path is not None and not os.path.exists(path):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass
    return PackEventLog(path=path)
