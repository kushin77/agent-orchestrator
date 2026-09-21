"""Knowledge-surface controls: pin-drift policy + vendor-compliance gaps.

---knowledge---
module_id: governance.knowledge.knowledge_controls
system: governance
app: knowledge
solution_class: pattern
patterns: [no-false-green, bounded-work]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [VendorComplianceGap, measure_vendor_compliance_gaps]
invariants: ""
gotchas: ""
related: ["#125", "#132", "#133", "#878", "#887"]
do_not_duplicate: null
---knowledge---

Issue #887 (lane L8/#878). Two controls live here:

1. **Pin-drift policy** — the knowledge surface must refuse to serve a
   catalogue built against a drifted CMR pin (:data:`REQUIRE_NO_DRIFT`);
   :mod:`live_sync` is the enforcement point, this module is the policy
   declaration the gate and the indexer both read.

2. **Vendor-compliance gap registry** — issues #132 (shared-services) and
   #133 (googleworkspace), parented under CMR's vendor-compliance epic
   (#125). Closing those issues is out of scope for this lane (a cross-repo
   remediation, not a knowledge-indexer change), but *measuring* the gap
   mechanically is in scope: :func:`measure_vendor_compliance_gaps` reads
   the CMR hub's own generated evidence
   (``vendor/CMR/docs/hygiene-report.md``,
   ``vendor/CMR/guardrails/sweep/report.md``) — never re-derives it — and
   reports a WARN per named gap still open. When the hub's own reports stop
   naming a repo, the gap is closed; nothing here closes the GitHub issue
   (that is a residual for the epic owner).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_CMR = REPO_ROOT / "vendor" / "CMR"
HYGIENE_REPORT = VENDOR_CMR / "docs" / "hygiene-report.md"
SWEEP_REPORT = VENDOR_CMR / "guardrails" / "sweep" / "report.md"

# Pin-drift policy: the knowledge surface must never serve a catalogue built
# against a drifted pin. Read by scripts/check-cmr-knowledge.sh.
REQUIRE_NO_DRIFT = True

CODE_VENDOR_COMPLIANCE_GAP = "VENDOR-COMPLIANCE-GAP"

# The two open vendor-compliance gaps this lane is asked to report (#132,
# #133), parented under epic #125. Closed vocabulary: adding a repo here
# means adding it to the issue registry too, not guessing at drift.
VENDOR_COMPLIANCE_ISSUES: Tuple[Tuple[str, int], ...] = (
    ("shared-services", 132),
    ("googleworkspace", 133),
)


@dataclass(frozen=True)
class VendorComplianceGap:
    repo: str
    issue: int
    findings: Tuple[str, ...]

    @property
    def open(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "issue": self.issue,
            "open": self.open,
            "findings": list(self.findings),
        }


def _grep_repo_lines(text: str, repo: str) -> List[str]:
    """Markdown table rows / list items naming ``repo`` (e.g. ``owner/repo``).

    Matches ``repo`` as a whole path segment — bounded by start-of-string,
    whitespace, ``/``, backtick or pipe on the left, and by the same set (or
    a trailing ``s``-less word char) on the right — so ``shared-services``
    does not also match inside an unrelated longer name.
    """
    pattern = re.compile(r"(?:^|[\s`|/])" + re.escape(repo) + r"(?:$|[\s`|,.:)])")
    return [line.strip() for line in text.splitlines() if pattern.search(line)]


def measure_vendor_compliance_gaps() -> List[VendorComplianceGap]:
    """Measure #132/#133 mechanically from the CMR hub's own reports.

    Reads ``hygiene-report.md`` and ``sweep/report.md`` — generated,
    machine-produced evidence the CMR hub already ships — and returns one
    :class:`VendorComplianceGap` per registered repo, each carrying the
    lines that still name it as a compliance breach or drift finding. An
    unreachable ``vendor/CMR`` (not checked out) or missing report is
    reported as a gap with a single "unavailable" finding — never silently
    treated as closed (no-false-green).
    """
    gaps: List[VendorComplianceGap] = []

    hygiene_text = HYGIENE_REPORT.read_text(encoding="utf-8") if HYGIENE_REPORT.is_file() else None
    sweep_text = SWEEP_REPORT.read_text(encoding="utf-8") if SWEEP_REPORT.is_file() else None

    for repo, issue in VENDOR_COMPLIANCE_ISSUES:
        findings: List[str] = []
        if hygiene_text is None and sweep_text is None:
            findings.append(
                f"unavailable: neither {HYGIENE_REPORT} nor "
                f"{SWEEP_REPORT} present (vendor/CMR not checked out?)"
            )
        else:
            if hygiene_text is not None:
                findings.extend(
                    f"hygiene-report.md: {line}" for line in _grep_repo_lines(hygiene_text, repo)
                )
            if sweep_text is not None:
                findings.extend(
                    f"sweep/report.md: {line}"
                    for line in _grep_repo_lines(sweep_text, repo)
                    # onboarding status rows for a *clean* vendor entry are not a
                    # compliance breach — only drift/guardrail/guard-hold findings are.
                    if not re.search(r"adopted-clean", line)
                )
        gaps.append(VendorComplianceGap(repo=repo, issue=issue, findings=tuple(findings)))

    return gaps
