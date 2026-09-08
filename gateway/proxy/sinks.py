"""Call-record sinks for the gateway proxy (issue #16, criterion 5).

Every dispatch emits ONE full ``GatewayCallRecord`` to the injected audit and
metering sinks — whichever way it landed (success, cache hit, budget block,
rate limit, refused, cannot-assess, no healthy route, failure, denial).  The
sinks are the Phase-5 telemetry-store seam: the observability pillar
(phase 5) consumes these JSONL records for full-trace per-tenant/per-agent
cost and audit attribution.

Record shapes follow the fleet append-only conventions already merged:

- the audit sink writes one JSON object per line (registry/events style),
- the metering sink writes the same one-record-per-line model-call-audit
  shape (finops ``JsonlMeteringSink`` / leaderboard ``finops-router.sh``
  pattern).

A hook/sink that raises propagates to the dispatcher (fail closed): an audit
or metering record is never silently lost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from proxy.model import GatewayCallRecord


@runtime_checkable
class CallRecordSink(Protocol):
    """A consumer of full gateway call records (audit or metering)."""

    def record(self, record: GatewayCallRecord) -> None:
        """Persist one full gateway call record."""
        ...


class ListCallRecordSink:
    """In-memory sink capturing records (tests and offline runs)."""

    def __init__(self) -> None:
        self.records: list[GatewayCallRecord] = []

    def record(self, record: GatewayCallRecord) -> None:
        self.records.append(record)

    def __len__(self) -> int:
        return len(self.records)


class JsonlCallRecordSink:
    """Append-only JSONL sink (one full call record per line)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, record: GatewayCallRecord) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    def read_records(self) -> list[dict]:
        """Read back and parse every record (evidence/reporting)."""
        if not self.path.is_file():
            return []
        records: list[dict] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


class NoopCallRecordSink:
    """Discards records (default when no audit/metering store is wired)."""

    def record(self, record: GatewayCallRecord) -> None:
        return None
