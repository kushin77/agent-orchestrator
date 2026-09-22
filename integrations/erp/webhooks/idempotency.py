"""Idempotency store for conversion events (issue #671).

A CRM sender retries; a network partition duplicates a delivery; an at-least-
once queue redelivers. All three land the same event twice, and the acceptance
criterion is explicit: "duplicate event idempotent" — a replay must return the
*first* outcome, never post a second time.

The store is a plain in-memory mapping keyed by
:attr:`~.model.ConversionEvent.idempotency_key` (``tenant:eventId`` unless the
sender supplies its own key). It is intentionally the simplest thing that is
still correct: a real deployment swaps this for a durable store behind the same
two-method seam (``seen`` / ``record``) without touching :mod:`bridge`.

---knowledge---
module_id: integrations.erp.webhooks.idempotency
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [IdempotencyStore]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import threading
from typing import Dict, Optional

from .model import BridgeResult


class IdempotencyStore:
    """Thread-safe, in-memory idempotency ledger."""

    def __init__(self) -> None:
        self._results: Dict[str, BridgeResult] = {}
        self._lock = threading.Lock()

    def seen(self, idempotency_key: str) -> Optional[BridgeResult]:
        """The previously recorded result for this key, or ``None``."""
        with self._lock:
            return self._results.get(idempotency_key)

    def record(self, idempotency_key: str, result: BridgeResult) -> None:
        """Record ``result`` for ``idempotency_key`` (first write wins).

        A second ``record`` for a key that is already recorded does not
        overwrite it: the first accepted outcome is the outcome a replay must
        keep seeing, even if — hypothetically — a caller tried to record a
        different one under the same key.
        """
        with self._lock:
            self._results.setdefault(idempotency_key, result)

    def __len__(self) -> int:
        return len(self._results)
