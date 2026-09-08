"""engine/queue.dedup — at-least-once idempotency (no duplicate side effects).

Issue #22 acceptance: *at-least-once with idempotency keys*. A redelivered
task — the worker crashed after the side effect but before the ack — must not
run its side effect twice. The queue therefore keys on an explicit
``idempotency_key``; re-enqueueing under an already-recorded key returns the
existing task instead of enqueueing a duplicate.

Exact-fingerprint idea adapted from the issue-aggregator ``deduplication.py``
(strategy 1, normalized fingerprinting); the semantic/trigram strategies there
are ML/Postgres-bound and out of scope for an offline queue.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

from engine.queue.model import Task


def canonical_json(value: Any) -> str:
    """Stable, sort-keyed JSON rendering of a payload (dicts/JSON types)."""
    return json.dumps(
        value, sort_keys=True, default=str, separators=(",", ":")
    )


def fingerprint_payload(payload: Any) -> str:
    """Stable sha256 fingerprint of a payload (exact-duplicate detection)."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def find_by_idempotency_key(
    tasks: Mapping[str, Task], idempotency_key: Optional[str]
) -> Optional[Task]:
    """Return the existing task carrying ``idempotency_key``, if any.

    ``None`` when the key is falsy or unseen. Scans live tasks only — this is
    an offline authority; any task (including terminal ones) with a matching
    key suppresses a duplicate enqueue.
    """
    if not idempotency_key:
        return None
    for task in tasks.values():
        if task.idempotency_key == idempotency_key:
            return task
    return None
