"""Finding and verdict models for the isolation lane (issue #30).

A *finding* is a concrete, evidence-backed observation of a tenant-isolation
anti-pattern (a cross-tenant read/write vector, a cross-tenant fallback,
shared mutable state across tenant namespaces, or a data-integrity violation
in a tenant-scoped dataset).  Every finding carries enough evidence to
reproduce it (target, class/method, line, source snippet, rule, severity,
fix) so a triage gate can act on mechanism, not on prose.

An *index verdict* is the honest tri-state (OK / NOT-OK / CANNOT-ASSESS)
result of scanning one tenant-store surface (an entity index inside a class,
or a module-level container).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

from .tristate import TriState


class Severity(enum.Enum):
    """Finding severity.  Order matters for triage (worst first)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
}


class FindingCategory(enum.Enum):
    """The class of anti-pattern a finding describes."""

    # -- code-scanner categories (static AST analysis) ---------------------
    SCOPE_DROP_READ = "scope_drop_read"
    SCOPE_DROP_WRITE = "scope_drop_write"
    CROSS_TENANT_FALLBACK = "cross_tenant_fallback"
    SHARED_MUTABLE_STATE = "shared_mutable_state"
    # -- data-integrity categories (dataset scan / runtime probes) ---------
    ORPHANED_RECORD = "orphaned_record"
    FALLBACK_TENANT_PILEUP = "fallback_tenant_pileup"
    CROSS_TENANT_DUPLICATE = "cross_tenant_duplicate"
    DENORMALIZED_RECORD = "denormalized_record"
    PROBE_FAILURE = "probe_failure"


@dataclass(frozen=True)
class Finding:
    """One concrete isolation finding, with reproduction evidence."""

    rule_id: str
    category: FindingCategory
    severity: Severity
    message: str
    target: str  # file path (repo-relative for code findings)
    scope: str = ""  # e.g. "Class.method" or "tenant t1 / record agent-1"
    line: int = 0
    evidence: str = ""  # source/data snippet that reproduces the finding
    fix: str = ""
    tenant: Optional[str] = None  # populated on per-tenant cadence scans

    @property
    def key(self) -> tuple:
        """Deterministic identity for de-duplication / idempotent replanning."""
        return (self.rule_id, self.target, self.scope, self.line)


@dataclass
class IndexVerdict:
    """Tri-state outcome of scanning one tenant-store surface."""

    verdict: TriState
    target: str  # repo-relative file path
    owner: str = ""  # Class.attribute or "<module>.<name>"
    reason: str = ""

    @property
    def label(self) -> str:
        return str(self.verdict.value)


@dataclass
class ScanReport:
    """Aggregate of a code or dataset scan: findings + verdicts + coverage."""

    findings: list[Finding] = field(default_factory=list)
    verdicts: list[IndexVerdict] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    files_not_targets: list[str] = field(default_factory=list)

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)

    def add_verdict(self, verdict: IndexVerdict) -> None:
        self.verdicts.append(verdict)

    # -- honest aggregation ------------------------------------------------

    @property
    def has_findings(self) -> bool:
        return len(self.findings) > 0

    @property
    def modeled_verdicts(self) -> list[IndexVerdict]:
        """Verdicts that actually assessed a tenant-store surface."""
        return [v for v in self.verdicts if v.verdict is not TriState.CANNOT_ASSESS]

    def aggregate(self) -> TriState:
        """Fail-closed tri-state over the modeled surfaces.

        A single NOT-OK verdict or finding fails the scan; otherwise any
        CANNOT-ASSESS (unmodeled) surface keeps the aggregate from reading
        OK; only an all-OK set reads OK.  CANNOT-ASSESS is never a pass.
        """
        if self.has_findings or any(
            v.verdict is TriState.NOT_OK for v in self.verdicts
        ):
            return TriState.NOT_OK
        if any(v.verdict is TriState.CANNOT_ASSESS for v in self.verdicts):
            return TriState.CANNOT_ASSESS
        return TriState.OK
