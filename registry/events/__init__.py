"""Append-only agent lifecycle event log (registry/events, issue #10).

---knowledge---
module_id: registry.events
system: registry
app: events
solution_class: class
patterns: [package-contract, public-surface]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [EVENT_KINDS, GENESIS_HASH, EventLog, EventLogError, EventLogIntegrityError, now_utc, open_event_log]
invariants: "the package re-exports the log surface and nothing else; the record schema stays a file, not a second declaration"
gotchas: ""
related: ["#10"]
do_not_duplicate: null
---knowledge---

Public surface
--------------

- ``event_log`` - the append-only hash-chained log: ``EventLog``,
  ``open_event_log``, ``verify``/``state``/``tail``, chain-integrity checks.
- ``event.schema.json`` - JSON Schema for one event record.

The package is importable as ``events`` when ``registry/`` is on ``sys.path``
(the tests arrange this in ``tests/conftest.py``).
"""

from events.event_log import (
    EVENT_KINDS,
    GENESIS_HASH,
    EventLog,
    EventLogError,
    EventLogIntegrityError,
    now_utc,
    open_event_log,
)

__all__ = [
    "EVENT_KINDS",
    "GENESIS_HASH",
    "EventLog",
    "EventLogError",
    "EventLogIntegrityError",
    "now_utc",
    "open_event_log",
]
