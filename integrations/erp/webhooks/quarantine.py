"""The quarantine store: where a refused event goes instead of the ledger.

"Fail-closed" only means something if there is a place the rejected event
lands — otherwise "refused" is indistinguishable from "silently dropped", and
an auditor cannot tell a quarantined conversion from one that never arrived.
:class:`QuarantineStore` is that place: an append-only, in-memory record of
every event this lane refused, the code it was refused under, and the detail.

Like :mod:`idempotency`, this is the simplest store that is still correct; a
real deployment swaps it for a durable dead-letter queue behind the same
``add`` / ``all`` seam.

---knowledge---
module_id: integrations.erp.webhooks.quarantine
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [QuarantinedEvent, QuarantineStore]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class QuarantinedEvent:
    """One quarantined delivery: what arrived, and why it was refused."""

    code: str
    detail: str
    raw_payload: Any
    at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "at": self.at}


class QuarantineStore:
    """Thread-safe, append-only quarantine record."""

    def __init__(self) -> None:
        self._events: List[QuarantinedEvent] = []
        self._lock = threading.Lock()

    def add(self, event: QuarantinedEvent) -> None:
        with self._lock:
            self._events.append(event)

    def all(self) -> Tuple[QuarantinedEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
