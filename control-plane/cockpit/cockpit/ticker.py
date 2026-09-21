"""The streaming alert ticker — severity-coded, acked through the API (#566).

Two authorities are consumed, none invented:

* the severity scale is RC-2's own closed enum, the one the steering verb
  ``channel.escalate`` declares (``info | warn | critical``);
* the acknowledgement is a control verb: ``ack`` resolves to the DECLARED
  steering command ``SEND`` (the registry has no ``ack`` verb, and this
  package invents none) with the alert id in the message, delivered through
  the control API — so the audit record lands on RC-4's rails and the cockpit
  writes no local ack state. An alert renders acked only from the receipt the
  plane returned.


---knowledge---
module_id: control-plane.cockpit.cockpit.ticker
system: control-plane
app: cockpit
solution_class: enterprise
patterns: [declared-authority, one-receipt-per-action, no-local-state]
derives_from: null
owner_sme: frontend-sme
tier: L1
interfaces: [load_alerts, ack_call, render, Alert, SEVERITIES, ACK_FUNCTION]
invariants: "an alert renders acked only from the receipt the plane returned, so the cockpit writes no local ack state and lands the audit record on RC-4's rails"
gotchas: "the registry has no ack verb, so an acknowledgement resolves to the DECLARED steering command SEND with the alert id in the message"
related: ["#566"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from . import _paths
from .frame import paint, severity_color

#: RC-2's closed severity enum (``channel.escalate``'s ``values``), consumed.
SEVERITIES: tuple[str, ...] = ("info", "warn", "critical")

#: The declared command an acknowledgement resolves to.
ACK_FUNCTION = "SEND"

DEFAULT_FIXTURE = _paths.FIXTURES / "alerts.json"


class TickerError(RuntimeError):
    """The ticker cannot be rendered honestly."""


@dataclass(frozen=True)
class Alert:
    id: str
    severity: str
    text: str
    source: str


def load_alerts(path: Optional[Path | str] = None) -> list[Alert]:
    """The committed alert fixture; a severity outside the closed set is an error."""
    target = Path(path) if path is not None else DEFAULT_FIXTURE
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TickerError(f"{target} is unreadable: {exc!r}") from exc
    if not isinstance(document, Mapping):
        raise TickerError(f"{target} is not a mapping")
    alerts: list[Alert] = []
    for entry in document.get("alerts") or []:
        if not isinstance(entry, Mapping):
            continue
        severity = str(entry.get("severity", ""))
        if severity not in SEVERITIES:
            raise TickerError(
                f"{target} declares severity {severity!r}; the closed set is {list(SEVERITIES)}"
            )
        alerts.append(
            Alert(
                id=str(entry.get("id", "")),
                severity=severity,
                text=str(entry.get("text", "")),
                source=str(entry.get("source", "")),
            )
        )
    return alerts


def ack_call(alert: Alert) -> tuple[str, dict[str, Any]]:
    """The declared command an acknowledgement resolves to — never a local write."""
    return ACK_FUNCTION, {"message": f"ack {alert.id}"}


def render(
    alerts: Sequence[Alert],
    acked: Mapping[str, str],
    width: int = 96,
) -> str:
    """One severity-coded line per alert; acked alerts show the receipt id."""
    if not alerts:
        return "  NO_DATA  (no alerts are visible — absence is never a green state)"
    lines: list[str] = []
    for alert in alerts:
        tag = paint(f"[{alert.severity:<8}]", severity_color(alert.severity))
        receipt = acked.get(alert.id)
        state = f" acked ({receipt})" if receipt else ""
        lines.append(f"  {tag} {alert.id:<6} {alert.text:<56} {alert.source}{state}")
    return "\n".join(lines)
