"""Report rendering for isolation scans (issue #30).

Deterministic text/JSON renderers so a cadence scan can be read by a human,
filed as evidence, and consumed by the triage gate.  Findings always carry
their evidence snippet; verdict tables always show the honest tri-state and
enumerate CANNOT-ASSESS surfaces (never hidden, never a pass).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .model import Finding, IndexVerdict, ScanReport, Severity
from .triage import TriageAction, partition
from .tristate import TriState


def _severity_label(severity: Severity) -> str:
    return severity.value.upper()


def finding_to_dict(finding: Finding) -> Dict[str, Any]:
    return {
        "rule": finding.rule_id,
        "category": finding.category.value,
        "severity": finding.severity.value,
        "message": finding.message,
        "target": finding.target,
        "scope": finding.scope,
        "line": finding.line,
        "tenant": finding.tenant,
        "evidence": finding.evidence,
        "fix": finding.fix,
    }


def finding_from_dict(payload: Dict[str, Any]) -> Finding:
    """Rebuild a :class:`Finding` from :func:`finding_to_dict` JSON."""
    from .model import FindingCategory, Severity

    return Finding(
        rule_id=payload.get("rule") or payload.get("rule_id") or "",
        category=FindingCategory(payload["category"]),
        severity=Severity(payload["severity"]),
        message=payload["message"],
        target=payload.get("target", ""),
        scope=payload.get("scope", ""),
        line=payload.get("line", 0),
        tenant=payload.get("tenant"),
        evidence=payload.get("evidence", ""),
        fix=payload.get("fix", ""),
    )


def verdict_to_dict(verdict: IndexVerdict) -> Dict[str, Any]:
    return {
        "verdict": verdict.verdict.value,
        "target": verdict.target,
        "owner": verdict.owner,
        "reason": verdict.reason,
    }


def summary_to_dict(report: ScanReport) -> Dict[str, Any]:
    """Machine-readable scan summary (verdict counts + honest aggregate)."""
    counts: Dict[str, int] = {label: 0 for label in
                              (TriState.OK, TriState.NOT_OK,
                               TriState.CANNOT_ASSESS)}
    for verdict in report.verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    triage = partition(report.findings)
    return {
        "files_scanned": len(report.files_scanned),
        "files_no_store_surface": len(report.files_not_targets),
        "findings": len(report.findings),
        "blocking_findings": len(triage[TriageAction.BLOCK]),
        "sme_review_findings": len(triage[TriageAction.SME_REVIEW]),
        "log_findings": len(triage[TriageAction.LOG]),
        "verdicts": {str(key.value): value for key, value in counts.items()},
        "aggregate": report.aggregate().value,
    }


def render_text(report: ScanReport, *, title: str = "Isolation integrity scan",
                detail: bool = True) -> str:
    """Human-readable report (findings + verdict table + honest summary)."""
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    triage = partition(report.findings)

    lines.append(f"## Findings ({len(report.findings)})")
    if not report.findings:
        lines.append("  none")
    for finding in sorted(report.findings,
                          key=lambda f: (f.target, f.line, f.rule_id)):
        lines.append(f"  [{finding.rule_id}] {finding.severity.value.upper()} "
                     f"{finding.target} :: {finding.scope or '-'}")
        lines.append(f"      {finding.message}")
        if detail and finding.evidence:
            lines.append(f"      evidence: {_one_line(finding.evidence)}")
        if detail and finding.fix:
            lines.append(f"      fix: {_one_line(finding.fix)}")
    lines.append("")

    lines.append("## Triage")
    lines.append(f"  auto-BLOCK: {len(triage[TriageAction.BLOCK])}   "
                 f"SME-reviewer queue: {len(triage[TriageAction.SME_REVIEW])}   "
                 f"LOG: {len(triage[TriageAction.LOG])}")
    lines.append("")

    lines.append("## Store-surface verdicts")
    if not report.verdicts:
        lines.append("  (no tenant-store surfaces modeled)")
    for verdict in sorted(report.verdicts,
                          key=lambda v: (v.target, v.owner)):
        lines.append(f"  [{verdict.verdict.value:>12}] {verdict.target}"
                     f" :: {verdict.owner}")
        lines.append(f"      {_one_line(verdict.reason)}")
    lines.append("")

    summary = summary_to_dict(report)
    lines.append("## Summary")
    lines.append(f"  files scanned: {summary['files_scanned']}   "
                 f"(no store surface: {summary['files_no_store_surface']})")
    lines.append(f"  verdicts OK / NOT-OK / CANNOT-ASSESS: "
                 f"{summary['verdicts']['OK']} / "
                 f"{summary['verdicts']['NOT-OK']} / "
                 f"{summary['verdicts']['CANNOT-ASSESS']}")
    lines.append(f"  honest aggregate: {summary['aggregate']}   "
                 f"(CANNOT-ASSESS never reads as a pass)")
    lines.append("")
    return "\n".join(lines)


def render_json(report: ScanReport, *, title: str = "Isolation integrity "
                                                        "scan") -> str:
    """Machine-readable report (findings + verdicts + summary)."""
    return json.dumps({
        "title": title,
        "findings": [finding_to_dict(f) for f in sorted(
            report.findings, key=lambda f: (f.target, f.line, f.rule_id))],
        "verdicts": [verdict_to_dict(v) for v in sorted(
            report.verdicts, key=lambda v: (v.target, v.owner))],
        "summary": summary_to_dict(report),
    }, indent=2, sort_keys=True)


def _one_line(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text
