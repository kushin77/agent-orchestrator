"""Health + degradation events flowing to audit and alert sinks (issue #18).

Every meaningful change in model health emits one structured ``HealthEvent``:

- verdict transitions while serving (``healthy`` -> ``degraded`` /
  ``degraded`` -> ``healthy``),
- state-machine transitions (``quarantined`` when the trip threshold is
  exceeded, ``recovery_probe_started`` when the cool-off elapses and a probe
  is authorized, ``recovered`` when probe successes restore the model,
  ``quarantine_reasserted`` when a probe fails),
- operator overrides (``operator_marked_*`` when a model is force-marked
  healthy/unhealthy — e.g. an Ollama daemon that an out-of-band check found
  down).

The monitor fans each event out to two injected sink sets (the Phase-5
observability/alerting hook, mirroring the providers ``EventRouter`` and the
finops ``MeteringSink`` seam):

- **audit sinks** receive every event (full-trace audit trail);
- **alert sinks** receive only ``severity >= alert_severity`` (default
  ``warning``) — quarantine and re-assertion are ``critical``, degradation is
  ``warning``, recovery/probe/operator-info events are ``info``.

Sinks implement the ``HealthSink`` protocol (``record(event)``). A sink that
raises propagates to the caller (fail closed): a lost audit/alert record is
never silent. Events carry no key material by construction.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# Event kinds (closed, documented vocabulary).
KIND_HEALTHY = "healthy"                      # degraded verdict cleared
KIND_DEGRADED = "degraded"                    # degrade threshold crossed
KIND_QUARANTINED = "quarantined"              # trip threshold crossed (hard stop)
KIND_QUARANTINE_REASSERTED = "quarantine_reasserted"  # recovery probe failed
KIND_RECOVERY_PROBE_STARTED = "recovery_probe_started"  # cool-off elapsed
KIND_RECOVERED = "recovered"                  # probe successes restored health
KIND_OPERATOR_MARKED_UNHEALTHY = "operator_marked_unhealthy"
KIND_OPERATOR_MARKED_HEALTHY = "operator_marked_healthy"

# Severity ladder (alert sinks receive severity >= the configured threshold).
SEV_INFO = "info"
SEV_WARNING = "warning"
SEV_CRITICAL = "critical"

#: Public severity ranking used to decide which events reach alert sinks.
SEVERITY_RANK: dict[str, int] = {SEV_INFO: 0, SEV_WARNING: 1, SEV_CRITICAL: 2}


def _severity_for(kind: str) -> str:
    if kind in (KIND_QUARANTINED, KIND_QUARANTINE_REASSERTED,
                KIND_OPERATOR_MARKED_UNHEALTHY):
        return SEV_CRITICAL
    if kind == KIND_DEGRADED:
        return SEV_WARNING
    return SEV_INFO


@dataclass(frozen=True)
class HealthEvent:
    """One structured model-health transition, for audit and alert sinks."""

    kind: str
    provider: str
    model: str
    state: str = "healthy"            # healthy | quarantined | probing
    verdict: str = "healthy"          # healthy | degraded | unhealthy | unknown
    failure_rate_pct: float = 0.0
    window_samples: int = 0
    latency_p95_ms: float | None = None
    error_class: str | None = None
    severity: str = SEV_INFO
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "state": self.state,
            "verdict": self.verdict,
            "failure_rate_pct": self.failure_rate_pct,
            "window_samples": self.window_samples,
            "latency_p95_ms": self.latency_p95_ms,
            "error_class": self.error_class,
            "severity": self.severity,
            "detail": self.detail,
            "ts": self.ts,
        }


@runtime_checkable
class HealthSink(Protocol):
    """Interface the monitor calls to persist a health event."""

    def record(self, event: HealthEvent) -> None:
        """Persist one health event (audit or alert)."""
        ...


class ListHealthSink:
    """In-memory sink capturing events (used by tests and offline runs)."""

    def __init__(self) -> None:
        self.events: list[HealthEvent] = []

    def record(self, event: HealthEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


class JsonlHealthSink:
    """Append-only JSONL file sink (fleet audit-log pattern, one line each)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: HealthEvent) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    def read_events(self) -> list[dict[str, Any]]:
        """Read back and parse every line (reports/evidence)."""
        if not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events


class NoopHealthSink:
    """Discards events (default when no audit/alert sink is configured)."""

    def record(self, event: HealthEvent) -> None:
        return None
