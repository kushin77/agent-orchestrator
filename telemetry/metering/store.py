"""telemetry/metering — durable usage store + idempotent ingest (issue #33).

The store is the durable aggregation source the rollups/report read: an
append-only JSON Lines file of canonical ``UsageRecord``s (one record per
line, camelCase keys, ``_schemaVersion`` envelope marker like the sibling
observability/ledger stores).  Because aggregation always re-scans the full
append log, no count is ever lost across process restarts or instances that
share the same store file (the fleet "durable SUM over the append-only
ledger" pattern, mirrored from capital-underwriting's ``aiTotalTokens``
durable column).

Idempotent ingest: every record carries a deterministic ``source_key``
derived from the source record (see ``intake.py``).  The store keeps a
seen-index of ingested keys (built by scanning the file, so it survives
restarts) and refuses to append a duplicate — replaying the same feed never
double-counts (negative-tested in ``tests/test_store.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from telemetry.metering.model import SCHEMA_VERSION, UsageRecord


class UsageStore:
    """Append-only usage store interface (durable aggregation source)."""

    def append(self, record: UsageRecord) -> None:
        """Persist one canonical record."""
        raise NotImplementedError

    def seen(self, source_key: str) -> bool:
        """True when a record with ``source_key`` is already stored."""
        raise NotImplementedError

    def read(self) -> List[UsageRecord]:
        """Every stored record, in append order."""
        raise NotImplementedError

    def count(self) -> int:
        """Number of stored records."""
        return len(self.read())


class MemoryUsageStore(UsageStore):
    """In-memory store (tests and offline runs)."""

    def __init__(self) -> None:
        self._records: List[UsageRecord] = []
        self._seen: Dict[str, int] = {}

    def append(self, record: UsageRecord) -> None:
        if self.seen(record.source_key):
            raise ValueError(f"duplicate ingest of source_key {record.source_key}")
        self._seen[record.source_key] = len(self._records)
        self._records.append(record)

    def seen(self, source_key: str) -> bool:
        return source_key in self._seen

    def read(self) -> List[UsageRecord]:
        return list(self._records)

    def count(self) -> int:
        return len(self._records)


class JsonlUsageStore(UsageStore):
    """Append-only JSONL usage store with a restart-surviving seen-index.

    The file itself is the source of truth; the seen-index is rebuilt by
    scanning stored ``sourceKey`` fields on first use, so a fresh process
    (or a second instance sharing the same file) inherits every key already
    ingested and never double-counts a replay.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: Optional[Dict[str, int]] = None

    def _ensure_seen(self) -> Dict[str, int]:
        if self._seen is None:
            index: Dict[str, int] = {}
            if self.path.is_file():
                with open(self.path, "r", encoding="utf-8") as fh:
                    for line_no, line in enumerate(fh):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # corrupt tail line: ignored for dedup
                        key = payload.get("sourceKey")
                        if key:
                            index[str(key)] = line_no
            self._seen = index
        return self._seen

    def append(self, record: UsageRecord) -> None:
        seen = self._ensure_seen()
        if record.source_key in seen:
            raise ValueError(f"duplicate ingest of source_key {record.source_key}")
        payload = record.to_dict()
        payload["_schemaVersion"] = SCHEMA_VERSION
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
        seen[record.source_key] = self.count() - 1

    def seen(self, source_key: str) -> bool:
        return source_key in self._ensure_seen()

    def read(self) -> List[UsageRecord]:
        records: List[UsageRecord] = []
        if not self.path.is_file():
            return records
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("kind") != "usage":
                    continue
                try:
                    records.append(UsageRecord.from_dict(payload))
                except (KeyError, ValueError, TypeError):
                    continue
        return records

    def count(self) -> int:
        return len(self.read())


def load_records(path: Path) -> List[UsageRecord]:
    """Load every canonical usage record from a JSONL file (no dedup)."""
    return JsonlUsageStore(path).read()


def append_records(path: Path, records: List[UsageRecord]) -> int:
    """Append records directly, skipping any whose source_key already exists.

    Returns the number of records actually written (replay-safe).
    """
    store = JsonlUsageStore(path)
    written = 0
    for record in records:
        if not store.seen(record.source_key):
            store.append(record)
            written += 1
    return written
