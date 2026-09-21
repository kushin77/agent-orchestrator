"""The one ``Violation`` shape shared by the lane audit and its extensions.

---knowledge---
module_id: governance.isolation.violation
system: governance
app: isolation
solution_class: class
patterns: [lane-isolation, commit-trailer]
derives_from: governance/isolation/audit.py
owner_sme: platform-sme
tier: L1
interfaces: [Violation]
invariants: ""
gotchas: ""
related: ["#885"]
do_not_duplicate: null
---knowledge---

Split out from :mod:`governance.isolation.audit` so a module that needs to
report a named finding (e.g. :mod:`governance.isolation.speculative`) does not
have to import the audit module itself and risk a cycle back into it.
``governance.isolation.audit`` re-exports :class:`Violation` for every existing
caller — this is not a second implementation, just where the dataclass lives.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Every refusal code a module in this package may build a Violation with.
#: `governance/isolation/controls.yaml` declares the same set (`policy.load()`
#: refuses to load a declaration that drifts from it in either direction), so
#: a code emitted here but not declared there — or vice versa — is a load-time
#: refusal, not a silent gap (issue #885).
KNOWN_CODES = frozenset(
    {
        "branch-does-not-name-issue",
        "worktree-missing",
        "worktree-not-linked",
        "branch-mismatch",
        "identity-not-lane-local",
        "identity-mismatch",
        "identity-leaked-to-shared-config",
        "commit-missing-ticket-trailer",
        "commit-trailer-check-unavailable",
        "commit-authored-by-another-session",
        "commit-authorship-unmeasurable",
        "speculative-base-not-landed",
        "speculative-base-stale-merge-base",
        "speculative-base-unmeasurable",
        "lane-session-gone",
        "runtime-unregistered",
    }
)


@dataclass(frozen=True)
class Violation:
    """One broken isolation property, named so it can be quoted as evidence."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"
