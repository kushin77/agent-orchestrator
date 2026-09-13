"""Model for the governance board enforcement gate (issue #143).

The board does not invent new checks — it is the honest aggregator over the
gates issues #139-#142 already built (knowledge index, CMR conformance, RCA +
lessons, remediation dispatch). Its job is narrow and load-bearing: run every
required gate for real, refuse to report green unless every one of them did,
and give a repeated or severe violation a path to board escalation instead of
letting it sit as a lane-owned deviation forever.

Status is a tri-state, the same honesty contract every gate in this repo uses
(GR-28): ``ok`` / ``not-ok`` / ``cannot-assess``. ``cannot-assess`` is not a
pass — a gate that cannot run must never be reported the same as a gate that
ran and found nothing wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = "cmr.board/gate-report-v1"

STATUS_OK = "ok"
STATUS_NOT_OK = "not-ok"
STATUS_CANNOT_ASSESS = "cannot-assess"
STATUS_EXCEPTED = "excepted"  # failed, but covered by an active, board-approved exception

VALID_STATUSES = (STATUS_OK, STATUS_NOT_OK, STATUS_CANNOT_ASSESS, STATUS_EXCEPTED)

# Escalation thresholds (charter-defined, see CHARTER.md "Escalation").
REPEATED_VIOLATION_THRESHOLD = 3


class ExceptionInvalid(Exception):
    """An exception record in the registry is malformed or cannot be applied."""


@dataclass
class CheckResult:
    """The outcome of running one required gate."""

    name: str
    command: str
    status: str
    exit_code: int
    output_tail: str = ""
    exception_applied: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
        }
        if self.output_tail:
            d["output_tail"] = self.output_tail
        if self.exception_applied:
            d["exception_applied"] = self.exception_applied
        return d


@dataclass
class Exception_:
    """A board-approved, timeboxed exception for one named check.

    An exception downgrades a single check's failure to a recorded deviation
    until ``expires`` (an ISO date, exclusive). It never suppresses
    cannot-assess, and it never applies past its timebox — an expired
    exception is worth exactly nothing, which is the point: exceptions are not
    silent, permanent opt-outs.
    """

    check: str
    reason: str
    approved_by: str
    expires: str  # ISO date, YYYY-MM-DD

    def is_active(self, today: Optional[date] = None) -> bool:
        today = today or datetime.now(timezone.utc).date()
        try:
            expiry = date.fromisoformat(self.expires)
        except ValueError as exc:
            raise ExceptionInvalid(
                "exception for %r has an unparseable expires date: %r"
                % (self.check, self.expires)
            ) from exc
        return today < expiry

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "reason": self.reason,
            "approved_by": self.approved_by,
            "expires": self.expires,
        }


@dataclass
class BoardReport:
    schema: str = SCHEMA
    generated_at: str = ""
    status: str = STATUS_CANNOT_ASSESS
    checks: List[CheckResult] = field(default_factory=list)
    expired_exceptions: List[str] = field(default_factory=list)
    escalations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "status": self.status,
            "checks": [c.to_dict() for c in self.checks],
            "expired_exceptions": list(self.expired_exceptions),
            "escalations": list(self.escalations),
        }


def aggregate_status(checks: Sequence[CheckResult]) -> str:
    """Roll up per-check status into one gate status.

    Any not-ok makes the gate not-ok. Any cannot-assess (with no not-ok
    present) makes the gate cannot-assess: an unassessable required check is
    never reported as a pass (no-false-green doctrine). A check that failed
    but is covered by an active, board-approved, timeboxed exception
    (``excepted``) does not fail the gate on its own — it is still visible in
    the report and in ``make verify`` output, which is what keeps it from
    being a silent, vacuous pass.
    """
    if not checks:
        return STATUS_CANNOT_ASSESS
    if any(c.status == STATUS_NOT_OK for c in checks):
        return STATUS_NOT_OK
    if any(c.status == STATUS_CANNOT_ASSESS for c in checks):
        return STATUS_CANNOT_ASSESS
    return STATUS_OK
