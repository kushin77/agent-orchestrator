"""telemetry/metering — live spend/usage feed for the portal (issue #886).

The metering ledger (``store.py``'s ``JsonlUsageStore``) and the rollup
report (``report.py``) are both pull-only: a caller must already know which
store file and which window to ask for.  This module is the one **live**
surface the portal actually polls — it re-reads the ledger on every call (no
caching, no snapshot staleness) and serves the current spend/usage rows in a
shape validated against ``feed.schema.json``, so a malformed row can never
reach a consumer silently.

Each row is one tenant's current cumulative usage/spend, derived by feeding
every stored ``UsageRecord`` through the same billable-aggregation rule
``report.py`` uses (a blocked/denied guard decision is not usage; an
unmetered record's tokens count but its cost never defaults to zero).

No-false-green: ``LiveUsageFeed.rows()`` validates every row it is about to
return and raises ``FeedValidationError`` — BY NAME — rather than emit a row
that does not match the schema (see ``tests/test_feed.py`` for the negative
control: a ledger record with a non-numeric cost is refused, never coerced).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from telemetry.metering.model import now_utc_iso
from telemetry.metering.store import JsonlUsageStore, UsageStore

FEED_SCHEMA_VERSION = 1

_SCHEMA_PATH = Path(__file__).with_name("feed.schema.json")


class FeedValidationError(ValueError):
    """A row failed schema validation before being served (no false green)."""


def _load_schema() -> Dict[str, Any]:
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def validate_row(row: Mapping[str, Any]) -> None:
    """Validate one feed row against ``feed.schema.json``.

    A hand-rolled structural check rather than a jsonschema dependency (this
    repo's other ``*.schema.json`` files are documentation-grade contracts
    checked the same lightweight way — see ``telemetry/ledger``) — every
    ``required`` key must be present with the declared JSON type, and no
    numeric field may be a bool (``True``/``False`` are ``int`` in Python,
    which would otherwise slip past a naive ``isinstance(x, (int, float))``
    check and silently corrupt a spend total).
    """
    schema = _load_schema()
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    missing = [key for key in required if key not in row]
    if missing:
        raise FeedValidationError(
            f"feed row missing required field(s): {', '.join(missing)}"
        )
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    for key, spec in properties.items():
        if key not in row:
            continue
        value = row[key]
        declared = spec.get("type")
        if declared is None:
            continue
        allowed = declared if isinstance(declared, list) else [declared]
        ok = False
        for kind in allowed:
            py_type = type_map.get(kind)
            if py_type is None:
                continue
            if kind == "number" and isinstance(value, bool):
                continue  # bool is not a number for this feed
            if isinstance(value, py_type):
                ok = True
                break
        if not ok:
            raise FeedValidationError(
                f"feed row field {key!r}={value!r} does not match declared "
                f"type {declared!r}"
            )
        minimum = spec.get("minimum")
        if minimum is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < minimum:
                raise FeedValidationError(
                    f"feed row field {key!r}={value!r} is below its declared "
                    f"minimum {minimum!r}"
                )


@dataclass(frozen=True)
class FeedRow:
    """One tenant's current cumulative usage/spend, the feed's unit."""

    schema_version: int
    tenant_id: str
    calls: int
    billable_calls: int
    unmetered_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    as_of: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "tenantId": self.tenant_id,
            "calls": self.calls,
            "billableCalls": self.billable_calls,
            "unmeteredCalls": self.unmetered_calls,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "costUsd": round(self.cost_usd, 8),
            "asOf": self.as_of,
        }


class LiveUsageFeed:
    """Reads the metering ledger live and serves current spend/usage rows.

    Backed by any ``UsageStore`` (in-memory in tests, ``JsonlUsageStore`` in
    production) — every ``rows()`` call re-scans the store, so the feed
    always reflects the ledger's current state, never a cached snapshot.
    """

    def __init__(self, store: UsageStore, *, as_of: Optional[str] = None) -> None:
        self._store = store
        self._as_of = as_of

    @classmethod
    def from_path(cls, path: Path, *, as_of: Optional[str] = None) -> "LiveUsageFeed":
        """Open the feed directly against a JSONL ledger file."""
        return cls(JsonlUsageStore(Path(path)), as_of=as_of)

    def _as_of_value(self) -> str:
        if self._as_of is not None:
            return self._as_of
        return now_utc_iso()

    def rows(self) -> List[Dict[str, Any]]:
        """Current per-tenant spend/usage rows, each schema-validated.

        Raises ``FeedValidationError`` rather than serve a row that fails
        validation — a caller never sees a malformed row.
        """
        totals: Dict[str, Dict[str, float]] = {}
        for record in self._store.read():
            if not record.billable:
                continue
            bucket = totals.setdefault(
                record.tenant_id,
                {
                    "calls": 0,
                    "billable_calls": 0,
                    "unmetered_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += record.input_tokens
            bucket["output_tokens"] += record.output_tokens
            if record.metered and record.cost_usd is not None:
                bucket["billable_calls"] += 1
                bucket["cost_usd"] += record.cost_usd
            else:
                bucket["unmetered_calls"] += 1

        as_of = self._as_of_value()
        out: List[Dict[str, Any]] = []
        for tenant_id, bucket in sorted(totals.items()):
            row = FeedRow(
                schema_version=FEED_SCHEMA_VERSION,
                tenant_id=tenant_id,
                calls=int(bucket["calls"]),
                billable_calls=int(bucket["billable_calls"]),
                unmetered_calls=int(bucket["unmetered_calls"]),
                input_tokens=int(bucket["input_tokens"]),
                output_tokens=int(bucket["output_tokens"]),
                cost_usd=float(bucket["cost_usd"]),
                as_of=as_of,
            ).to_dict()
            validate_row(row)
            out.append(row)
        return out
