"""One refusal shape, shared by every module of the notice rule (issue #1269).

---knowledge---
module_id: governance.notices.finding
system: governance
app: notices
solution_class: class
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Finding]
invariants: ""
gotchas: ""
related: ["#1269"]
do_not_duplicate: null
---knowledge---

A refusal is NAMED: ``str(finding)`` is ``code:subject`` and nothing else, so the
refusal a gate greps is the refusal a human reads -- a reason appended to the name
would make every needle depend on prose. The prose rides in ``reason`` and reaches
a report through ``line()``.

Three modules report findings (the records, the ledger, the controls), and they
share this one shape rather than three that drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One refusal, by name."""

    code: str
    subject: str
    reason: str = ""

    def __str__(self) -> str:
        return "%s:%s" % (self.code, self.subject)

    def line(self) -> str:
        """The report line: the refusal by name, then why it is refused."""
        return "%s -- %s" % (self, self.reason) if self.reason else str(self)
