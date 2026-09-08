"""Append-only agent lifecycle event log (registry/events, issue #10).

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
