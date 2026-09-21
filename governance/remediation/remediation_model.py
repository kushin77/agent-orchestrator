"""Remediation domain model: turning a conformance finding into actionable work

---knowledge---
module_id: governance.remediation.remediation_model
system: governance
app: remediation
solution_class: pattern
patterns: []
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [severity_for, lane_for, sla_hours_for, dedup_key, RemediationIssue, RemediationReport]
invariants: ""
gotchas: ""
related: ["#140", "#142"]
do_not_duplicate: null
---knowledge---

(issue #142).

A `Finding` from `governance/conformance` (issue #140) says a rule was broken.
That is not yet work: nobody owns it, nothing says how urgent it is, and two
scans of the same broken rule would otherwise open two issues. This module
adds exactly that layer:

* **severity** — how bad, mapped from the finding's own severity plus its code
  (some codes are always critical regardless of the warning/error split, e.g.
  a secret-exposure pattern).
* **owner lane** — who is on the hook, derived from the finding's code/subject
  so the issue lands with the team that can fix it, not in a shared inbox.
* **SLA** — how long the lane has before the finding must be escalated to the
  governance board.
* **dedup key** — the same (code, subject) pair collapses repeat findings into
  one remediation issue with accumulated evidence and an occurrence count,
  instead of one issue per scan.

Everything here is pure data transformation: no GitHub I/O. That lives in
`github.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

SCHEMA_ID = "cmr.remediation/report-v1"

# -- severity ------------------------------------------------------------

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

SEVERITY_RANK = {
    SEVERITY_LOW: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_HIGH: 2,
    SEVERITY_CRITICAL: 3,
}

# Codes that are always treated as critical, no matter how the conformance
# checker classified them — secret exposure and an unmet IaC mandate are
# security-adjacent and must not wait behind a warning-severity default.
ALWAYS_CRITICAL_CODES = frozenset(
    {
        "secret-exposure",
        "iac-mandate-unmet",
    }
)

# SLA, in hours, before an unresolved finding of this severity must be
# escalated to the governance board (issue #142 acceptance criterion).
SLA_HOURS = {
    SEVERITY_CRITICAL: 24,
    SEVERITY_HIGH: 72,
    SEVERITY_MEDIUM: 168,  # 7 days
    SEVERITY_LOW: 336,  # 14 days
}

# An occurrence count at or above this threshold escalates a finding even when
# its severity alone would not — "prevent repeated violations" (issue #142
# scope) means a low-severity finding that keeps recurring still gets flagged.
ESCALATE_OCCURRENCE_THRESHOLD = 3

# -- owner lanes -----------------------------------------------------------

LANE_GOVERNANCE = "governance"
LANE_PLATFORM = "platform"
LANE_QA = "qa"
LANE_SECURITY = "security"

# Ordered (prefix, lane) table: the first code prefix that matches wins. Kept
# as a tuple of tuples (not a dict) so precedence is explicit and testable.
LANE_BY_CODE_PREFIX: Tuple[Tuple[str, str], ...] = (
    ("secret-exposure", LANE_SECURITY),
    ("iac-mandate-unmet", LANE_PLATFORM),
    ("dependency-missing", LANE_QA),
    ("policy-invalid", LANE_GOVERNANCE),
    ("class-", LANE_GOVERNANCE),
    ("classification-incomplete", LANE_GOVERNANCE),
    ("scope-mismatch", LANE_GOVERNANCE),
)

DEFAULT_LANE = LANE_GOVERNANCE

REMEDIATION_LABEL = "remediation:auto"
ESCALATION_LABEL = "remediation:escalated"


def severity_for(code: str, base_severity: str) -> str:
    """The remediation severity for a finding: escalate always-critical codes,
    otherwise map the conformance severity ("error"/"warning") onto the
    remediation scale.
    """
    if code in ALWAYS_CRITICAL_CODES:
        return SEVERITY_CRITICAL
    if base_severity == "error":
        return SEVERITY_HIGH
    return SEVERITY_MEDIUM


def lane_for(code: str, subject: str = "") -> str:
    """The owning lane for a finding's code (subject is reserved for future
    per-path routing, e.g. repo-scoped lanes; unused today but part of the
    routing contract, issue #142 scope "repo-level and org-wide")."""
    for prefix, lane in LANE_BY_CODE_PREFIX:
        if code == prefix or code.startswith(prefix):
            return lane
    return DEFAULT_LANE


def sla_hours_for(severity: str) -> int:
    return SLA_HOURS.get(severity, SLA_HOURS[SEVERITY_MEDIUM])


def dedup_key(code: str, subject: str) -> str:
    """The identity of "the same violation" across scans: same rule, same
    subject. Two different subjects failing the same rule are two issues;
    the same subject failing repeatedly across scans is one issue that
    accumulates evidence.
    """
    return "%s:%s" % (code, subject or "(repo)")


@dataclass
class RemediationIssue:
    """One remediation unit: a violation that has become actionable work."""

    key: str
    code: str
    subject: str
    title: str
    severity: str
    owner_lane: str
    sla_hours: int
    policy_ref: str
    corrective_steps: Tuple[str, ...]
    evidence: List[str] = field(default_factory=list)
    occurrences: int = 1
    scope: str = "repo"  # "repo" or "org"
    repo: str = ""

    @property
    def escalate(self) -> bool:
        """Escalate to the governance board when severity alone demands it,
        or when the finding keeps recurring (SLA/severity thresholds breached,
        issue #142 acceptance criterion)."""
        return (
            self.severity == SEVERITY_CRITICAL
            or self.occurrences >= ESCALATE_OCCURRENCE_THRESHOLD
        )

    @property
    def labels(self) -> Tuple[str, ...]:
        labels = [
            REMEDIATION_LABEL,
            "severity:%s" % self.severity,
            "lane:%s" % self.owner_lane,
            "scope:%s" % self.scope,
        ]
        if self.escalate:
            labels.append(ESCALATION_LABEL)
        return tuple(labels)

    def body(self) -> str:
        lines = [
            "**Policy violation detected by the automated remediation scanner "
            "(issue #142).**",
            "",
            "- Rule/policy: `%s`" % self.policy_ref,
            "- Severity: **%s**" % self.severity,
            "- Owner lane: `%s`" % self.owner_lane,
            "- SLA: %d hour(s) from first detection" % self.sla_hours,
            "- Occurrences observed: %d" % self.occurrences,
            "- Scope: %s" % self.scope,
        ]
        if self.repo:
            lines.append("- Repo: `%s`" % self.repo)
        lines.append("")
        lines.append("### Evidence")
        for item in self.evidence:
            lines.append("- %s" % item)
        lines.append("")
        lines.append("### Corrective steps")
        for step in self.corrective_steps:
            lines.append("- %s" % step)
        lines.append("")
        lines.append(
            "<!-- remediation-key: %s -->" % self.key
        )
        return "\n".join(lines)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "code": self.code,
            "subject": self.subject,
            "title": self.title,
            "severity": self.severity,
            "owner_lane": self.owner_lane,
            "sla_hours": self.sla_hours,
            "policy_ref": self.policy_ref,
            "corrective_steps": list(self.corrective_steps),
            "evidence": list(self.evidence),
            "occurrences": self.occurrences,
            "scope": self.scope,
            "repo": self.repo,
            "escalate": self.escalate,
            "labels": list(self.labels),
        }


@dataclass
class RemediationReport:
    """The result of one remediation-generation run."""

    generated_at: str
    scope: str
    scanned: int
    issues: List[RemediationIssue] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "generated_at": self.generated_at,
            "scope": self.scope,
            "scanned": self.scanned,
            "issue_count": len(self.issues),
            "escalated_count": sum(1 for i in self.issues if i.escalate),
            "issues": [i.as_dict() for i in self.issues],
        }
