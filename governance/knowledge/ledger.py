"""Audit ledger for knowledge-surface CMR pin sync events (issue #887).

---knowledge---
module_id: governance.knowledge.ledger
system: governance
app: knowledge
solution_class: pattern
patterns: [append-only-ledger, no-false-green]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [LedgerError, LedgerEvent, now_iso, validate_event, append_event, read_ledger]
invariants: ""
gotchas: ""
related: ["#887"]
do_not_duplicate: null
---knowledge---

Every :mod:`live_sync` check (drift-free or drifted) and every
vendor-compliance gap measurement (:mod:`knowledge_controls`) is appended
here as one JSON line — an append-only record of what the knowledge surface
observed and when, so "was the pin drift gate actually exercised on
<date>" is answerable from the tree instead of trusted on faith.

Format: JSON Lines, one event object per line, newest last. Each event is
validated against :data:`EVENT_FIELDS` before it is written — a malformed
event is refused, never silently appended (no-false-green).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent / "sync-ledger.jsonl"

EVENT_FIELDS: Tuple[str, ...] = ("timestamp", "kind", "status", "detail")

# Closed vocabulary: an event kind not drawn from this tuple is invalid.
KIND_PIN_DRIFT_CHECK = "pin-drift-check"
KIND_VENDOR_COMPLIANCE_GAP = "vendor-compliance-gap"
EVENT_KINDS: Tuple[str, ...] = (KIND_PIN_DRIFT_CHECK, KIND_VENDOR_COMPLIANCE_GAP)

# Closed vocabulary of event statuses.
STATUS_OK = "ok"
STATUS_DRIFT = "drift"
STATUS_WARN = "warn"
STATUS_CANNOT_ASSESS = "cannot-assess"
EVENT_STATUSES: Tuple[str, ...] = (STATUS_OK, STATUS_DRIFT, STATUS_WARN, STATUS_CANNOT_ASSESS)


class LedgerError(ValueError):
    """Raised when an event fails shape or vocabulary validation."""


@dataclass(frozen=True)
class LedgerEvent:
    timestamp: str
    kind: str
    status: str
    detail: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_event(event: Mapping[str, Any]) -> None:
    missing = [name for name in EVENT_FIELDS if name not in event]
    if missing:
        raise LedgerError(f"event missing field(s): {', '.join(missing)}")
    if event["kind"] not in EVENT_KINDS:
        raise LedgerError(f"unknown event kind: {event['kind']!r}")
    if event["status"] not in EVENT_STATUSES:
        raise LedgerError(f"unknown event status: {event['status']!r}")
    if not isinstance(event["timestamp"], str) or not event["timestamp"]:
        raise LedgerError("event timestamp must be a non-empty string")


def append_event(
    kind: str,
    status: str,
    detail: str,
    *,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    timestamp: str = "",
) -> LedgerEvent:
    """Append one validated event to the ledger and return it.

    Raises :class:`LedgerError` — and writes nothing — if the event fails
    validation (closed kind/status vocabulary, non-empty timestamp).
    """
    event = LedgerEvent(
        timestamp=timestamp or now_iso(),
        kind=kind,
        status=status,
        detail=detail,
    )
    validate_event(event.as_dict())
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.as_dict(), sort_keys=True) + "\n")
    return event


def read_ledger(ledger_path: Path = DEFAULT_LEDGER_PATH) -> List[LedgerEvent]:
    """Read and validate every event in the ledger, in file order.

    Raises :class:`LedgerError` on the first malformed line — a ledger that
    silently skips bad entries is not an audit trail.
    """
    if not ledger_path.is_file():
        return []
    events: List[LedgerEvent] = []
    with ledger_path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"{ledger_path}:{lineno}: invalid JSON: {exc}") from exc
            validate_event(raw)
            events.append(LedgerEvent(**{k: raw[k] for k in EVENT_FIELDS}))
    return events
