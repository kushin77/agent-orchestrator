#!/usr/bin/env python3
"""Per-run telemetry record schema + JSONL append helper (issue #232, micro-task 0 of #219).

A "run" is one subagent's pass at one issue. This module defines the record
shape and a single append primitive; it does not decide when a run starts or
ends — callers (the sister/brain loop) own that.

Record shape (all fields required unless noted):
    run_id      str   unique id for this run (uuid4 recommended)
    issue       str   issue number/id the run worked, e.g. "232"
    agent       str   subagent identifier, e.g. "subagent-67e1e8c2"
    status      str   one of RUN_STATUSES
    started_at  str   ISO-8601 UTC timestamp
    finished_at str   ISO-8601 UTC timestamp, or None if still running
    detail      str   optional free-text note (default "")

Usage:
    import telemetry
    record = telemetry.build_record(run_id="...", issue="232", agent="subagent-x",
                                     status="done", started_at="...", finished_at="...")
    telemetry.append_record(telemetry.RUNS_LOG, record)
"""

from __future__ import annotations

import json
from pathlib import Path

import runtime

ROOT = Path(__file__).resolve().parent.parent
RUNS_LOG = runtime.FLEET_DIR / "runs.jsonl"

RUN_STATUSES = ("started", "done", "failed")

REQUIRED_FIELDS = ("run_id", "issue", "agent", "status", "started_at", "finished_at")


class TelemetryError(ValueError):
    """Raised when a record fails schema validation."""


def build_record(
    *,
    run_id: str,
    issue: str,
    agent: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    detail: str = "",
) -> dict:
    """Build a per-run telemetry record, validating required fields and status."""
    if status not in RUN_STATUSES:
        raise TelemetryError(f"status must be one of {RUN_STATUSES!r}, got {status!r}")
    record = {
        "run_id": run_id,
        "issue": issue,
        "agent": agent,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "detail": detail,
    }
    validate_record(record)
    return record


def validate_record(record: dict) -> None:
    """Raise TelemetryError if `record` is missing a required field or has a bad status."""
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise TelemetryError(f"record missing required fields: {missing!r}")
    if record["status"] not in RUN_STATUSES:
        raise TelemetryError(f"status must be one of {RUN_STATUSES!r}, got {record['status']!r}")


def append_record(path: Path, record: dict) -> None:
    """Validate `record` and append it as one JSON line to `path`, creating parents."""
    validate_record(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_records(path: Path) -> list[dict]:
    """Read all records from `path`, or return [] if it does not exist."""
    path = Path(path)
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
