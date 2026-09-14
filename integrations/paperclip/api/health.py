"""``/api/health`` — a real health read, not a hard-coded ``ok`` (#413).

A health endpoint that always answers ``ok`` is a green lie: it cannot tell an
operator whether the surface can actually read the state it claims to serve.
This module names the dependencies the surface really has and reads them:

* **claim ledger** — ``.board/claims.jsonl`` (``governance/dispatch/claims.py``,
  ``DEFAULT_LEDGER``) must be *readable*: present and every non-empty line a
  JSON object. An unreadable ledger is the "ledgers readable" dependency.
* **ticket projection** — ``.board/snapshot.json`` (``governance/dispatch/
  snapshot.py``, ``DEFAULT_PATH``) must be *fresh*: its ``generated_at`` within
  the fleet's own staleness ceiling, ``SNAPSHOT_STALENESS_MINUTES``
  (``governance/policy/lease.py`` — the value is imported, never restated).

The verdict is a three-way state: ``ok`` when every dependency is ok,
``degraded`` when a dependency is present but stale, and ``unhealthy`` when one
is missing or unreadable — the last reports HTTP ``503`` rather than a green
lie. :func:`check_report` is the independent layer: it recomputes the states
from the probes and refuses a report that claims a dependency is ok while the
probe says otherwise, so a weakened ``health()`` cannot pass the gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from governance.policy.lease import SNAPSHOT_STALENESS_MINUTES

#: The claim ledger the surface's tickets are built from (governance/dispatch).
DEFAULT_LEDGER = Path(".board") / "claims.jsonl"

#: The board snapshot (the ticket projection) — governance/dispatch/snapshot.py.
DEFAULT_SNAPSHOT = Path(".board") / "snapshot.json"

#: Dependency states, closed.
STATE_OK = "ok"
STATE_STALE = "stale"
STATE_MISSING = "missing"
STATE_UNREADABLE = "unreadable"

#: Overall verdicts, closed.
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNHEALTHY = "unhealthy"

#: HTTP status per verdict. An unreachable dependency is a 503, never a 200.
HTTP_BY_STATUS = {STATUS_OK: 200, STATUS_DEGRADED: 200, STATUS_UNHEALTHY: 503}


@dataclass(frozen=True)
class Dependency:
    """A dependency the surface names and reads."""

    name: str
    kind: str  # "ledger" | "projection"
    path: str


@dataclass(frozen=True)
class DependencyState:
    """One probe's reading of one dependency."""

    name: str
    state: str
    detail: str


@dataclass(frozen=True)
class HealthReport:
    """The health verdict and the per-dependency readings behind it."""

    status: str
    http_status: int
    dependencies: Tuple[DependencyState, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "dependencies": [
                {"name": d.name, "state": d.state, "detail": d.detail}
                for d in self.dependencies
            ],
        }


#: The dependencies the surface declares, in the order it reports them.
DEPENDENCIES: Tuple[Dependency, ...] = (
    Dependency("claim_ledger", "ledger", DEFAULT_LEDGER.as_posix()),
    Dependency("ticket_projection", "projection", DEFAULT_SNAPSHOT.as_posix()),
)

Probe = Callable[[Path, Optional[datetime]], DependencyState]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp; naive input is read as UTC."""
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def probe_claim_ledger(root: Path, now: Optional[datetime] = None) -> DependencyState:
    """Read the claim ledger: present and every non-empty line a JSON object."""
    path = Path(root) / DEFAULT_LEDGER
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return DependencyState("claim_ledger", STATE_MISSING, f"{DEFAULT_LEDGER.as_posix()} is not readable")
    events = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except ValueError:
            return DependencyState(
                "claim_ledger", STATE_UNREADABLE, f"line {number} is not JSON"
            )
        events += 1
    return DependencyState("claim_ledger", STATE_OK, f"{events} claim event(s) readable")


def probe_ticket_projection(root: Path, now: Optional[datetime] = None) -> DependencyState:
    """Read the board snapshot: present and its ``generated_at`` fresh."""
    path = Path(root) / DEFAULT_SNAPSHOT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return DependencyState(
            "ticket_projection", STATE_MISSING, f"{DEFAULT_SNAPSHOT.as_posix()} is not readable"
        )
    except ValueError:
        return DependencyState("ticket_projection", STATE_UNREADABLE, "the snapshot is not JSON")
    if not isinstance(data, dict) or not str(data.get("generated_at") or "").strip():
        return DependencyState("ticket_projection", STATE_UNREADABLE, "the snapshot names no generated_at")
    try:
        generated = _parse_iso(str(data["generated_at"]))
    except ValueError:
        return DependencyState(
            "ticket_projection", STATE_UNREADABLE, "generated_at is not an ISO-8601 timestamp"
        )
    age_minutes = max(0.0, ((now or _now()) - generated).total_seconds() / 60.0)
    if age_minutes > SNAPSHOT_STALENESS_MINUTES:
        return DependencyState(
            "ticket_projection",
            STATE_STALE,
            f"the projection is {age_minutes:.1f} min old (ceiling {SNAPSHOT_STALENESS_MINUTES} min)",
        )
    return DependencyState(
        "ticket_projection", STATE_OK, f"the projection is {age_minutes:.1f} min old"
    )


#: The default probes — the real dependencies, read from the tree.
DEFAULT_PROBES: Tuple[Probe, ...] = (probe_claim_ledger, probe_ticket_projection)


def health(
    root: Path,
    *,
    now: Optional[datetime] = None,
    probes: Optional[Sequence[Probe]] = None,
) -> HealthReport:
    """Read every dependency and return the verdict (no hard-coded ok)."""
    active = tuple(probes) if probes is not None else DEFAULT_PROBES
    states = tuple(probe(Path(root), now) for probe in active)
    if any(s.state in (STATE_MISSING, STATE_UNREADABLE) for s in states):
        status = STATUS_UNHEALTHY
    elif any(s.state == STATE_STALE for s in states):
        status = STATUS_DEGRADED
    else:
        status = STATUS_OK
    return HealthReport(status=status, http_status=HTTP_BY_STATUS[status], dependencies=states)


def check_report(
    report: HealthReport,
    root: Path,
    *,
    now: Optional[datetime] = None,
    probes: Optional[Sequence[Probe]] = None,
) -> List[str]:
    """Cross-check a report against fresh probe readings; return all findings.

    The independent layer of the gate: even if :func:`health` were weakened to
    always answer ``ok``, this recomputes the states from the probes and refuses
    a report that claims a dependency is ok while the probe says otherwise —
    naming the dependency.
    """
    active = tuple(probes) if probes is not None else DEFAULT_PROBES
    actual = {state.name: state for state in (probe(Path(root), now) for probe in active)}
    findings: List[str] = []
    for reported in report.dependencies:
        truth = actual.get(reported.name)
        if truth is None:
            findings.append(f"the health report names dependency '{reported.name}' that has no probe")
            continue
        if reported.state == STATE_OK and truth.state != STATE_OK:
            findings.append(
                f"health reports dependency '{reported.name}' ok while it is {truth.state}"
            )
        if reported.state != truth.state:
            findings.append(
                f"health reports dependency '{reported.name}' as {reported.state} but it is {truth.state}"
            )
    if report.status == STATUS_OK and any(s.state != STATE_OK for s in actual.values()):
        findings.append("health reports overall ok while a named dependency is not ok")
    return findings
