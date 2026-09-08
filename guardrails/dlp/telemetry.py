"""guardrails.dlp.telemetry — security telemetry surfaced to the security view.

Every security-relevant decision on the DLP/egress path emits one structured
event: injection attempts, scrub blocks, egress denials, output quarantines and
tamper detections. :meth:`SecurityTelemetry.security_view` is the query the
security console/view consumes — it returns the events that need eyes, most
recent first.

All events are offline (in-memory and/or JSONL). Nothing here calls home.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional

# Event types the security view considers security-relevant by default.
SECURITY_EVENT_TYPES = frozenset(
    {
        "injection_attempt",
        "scrub_blocked",
        "egress_denied",
        "output_quarantined",
        "tamper_detected",
    }
)

_ALL_EVENT_TYPES = SECURITY_EVENT_TYPES | frozenset({"call_sent", "audit_failed"})


@dataclass(frozen=True)
class SecurityEvent:
    """One structured security event."""

    ts: float
    event_type: str
    severity: str  # "info" | "warning" | "critical"
    tenant_id: str
    agent_id: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ts": self.ts,
            "event_type": self.event_type,
            "severity": self.severity,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "detail": self.detail,
        }


@dataclass
class SecurityTelemetry:
    """Offline event sink + security-view query."""

    path: Optional[str] = None
    _events: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

    def emit(
        self,
        event_type: str,
        *,
        tenant_id: str,
        agent_id: str = "",
        severity: str = "info",
        **detail: Any,
    ) -> SecurityEvent:
        """Record one event and return it."""
        if event_type not in _ALL_EVENT_TYPES:
            raise ValueError(f"unknown event type: {event_type!r}")
        if severity not in ("info", "warning", "critical"):
            raise ValueError(f"unknown severity: {severity!r}")
        event = SecurityEvent(
            ts=time.time(),
            event_type=event_type,
            severity=severity,
            tenant_id=tenant_id,
            agent_id=agent_id,
            detail=dict(detail),
        )
        self._events.append(event)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.as_dict()) + "\n")
        return event

    def security_view(
        self,
        *,
        tenant_id: Optional[str] = None,
        event_types: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
    ) -> list:
        """Return security-relevant events, newest first, optional filters."""
        kinds = set(event_types) if event_types is not None else set(SECURITY_EVENT_TYPES)
        events = [e for e in self._events if e.event_type in kinds]
        if tenant_id is not None:
            events = [e for e in events if e.tenant_id == tenant_id]
        events.sort(key=lambda e: e.ts, reverse=True)
        if limit is not None:
            events = events[:limit]
        return events

    def counts(self) -> Dict[str, int]:
        """Per-event-type counts across all recorded events."""
        out: Dict[str, int] = {}
        for e in self._events:
            out[e.event_type] = out.get(e.event_type, 0) + 1
        return out

    def all_events(self) -> list:
        return list(self._events)
