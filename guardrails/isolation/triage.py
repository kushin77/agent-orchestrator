"""Security-finding triage gate (issue #30, acceptance criterion 4).

Every isolation finding funnels through this gate.  Severity maps to a
deterministic action — there is no silent path and no "decide later":

===============  ==========  =============================================
severity         action      meaning
===============  ==========  =============================================
critical / high  BLOCK       auto-BLOCK: the surface is not deployable /
                             the cadence scan fails the gate
medium           SME_REVIEW  queued for an SME reviewer before any change
low              LOG         recorded for observability only
===============  ==========  =============================================

``SME_REVIEW`` is the SME-reviewer queue: the finding is *not* auto-blocked
but it is also never auto-repaired or auto-dismissed — a named reviewer must
resolve it.  The action vocabulary aligns with the policy gate engine's
BLOCK / WARN / LOG decision levels (AO-GR-19), where SME_REVIEW is the
human-in-the-loop WARN.  An unrecognized severity raises rather than
defaulting silently (fail closed).
"""

from __future__ import annotations

import enum
from typing import Dict, List

from .model import Finding, Severity


class TriageAction(enum.Enum):
    BLOCK = "BLOCK"
    SME_REVIEW = "SME_REVIEW"
    LOG = "LOG"

    @property
    def blocks(self) -> bool:
        """BLOCK stops the surface; SME_REVIEW/LOG never auto-deploy."""
        return self is TriageAction.BLOCK

    @property
    def requires_reviewer(self) -> bool:
        return self is TriageAction.SME_REVIEW


#: severity -> triage action (worst-first mapping; no silent defaults).
_SEVERITY_ACTION = {
    Severity.CRITICAL: TriageAction.BLOCK,
    Severity.HIGH: TriageAction.BLOCK,
    Severity.MEDIUM: TriageAction.SME_REVIEW,
    Severity.LOW: TriageAction.LOG,
}

# Accepted aliases so callers can ask by name (BLOCK/WARN/LOG vocabulary).
_ACTION_ALIASES = {
    "BLOCK": TriageAction.BLOCK,
    "AUTO-BLOCK": TriageAction.BLOCK,
    "SME_REVIEW": TriageAction.SME_REVIEW,
    "SME": TriageAction.SME_REVIEW,
    "REVIEW": TriageAction.SME_REVIEW,
    "WARN": TriageAction.SME_REVIEW,
    "LOG": TriageAction.LOG,
}


def action_for_severity(severity: Severity) -> TriageAction:
    """Map a severity to its triage action.  Unknown severity raises."""
    try:
        return _SEVERITY_ACTION[severity]
    except KeyError as exc:
        raise ValueError(
            f"cannot triage severity {severity!r} (expected critical/high/"
            f"medium/low)"
        ) from exc


def triage_finding(finding: Finding) -> TriageAction:
    """Triage a single finding."""
    return action_for_severity(finding.severity)


def partition(findings: List[Finding]) -> Dict[TriageAction, List[Finding]]:
    """Split findings into BLOCK / SME_REVIEW / LOG buckets (stable order)."""
    buckets: Dict[TriageAction, List[Finding]] = {
        TriageAction.BLOCK: [],
        TriageAction.SME_REVIEW: [],
        TriageAction.LOG: [],
    }
    for finding in findings:
        buckets[triage_finding(finding)].append(finding)
    return buckets


def gate_blocks(findings: List[Finding]) -> bool:
    """True when any finding auto-BLOCKs the surface (fail closed)."""
    return any(triage_finding(f).blocks for f in findings)
