"""Scratch sample for the code-review-sme live check (issue #1536). Never merged.

---knowledge---
module_id: engine-livecheck-1536-sample
system: agent-orchestrator
app: engine
solution_class: template
patterns: [knowledge-header]
derives_from: null
owner_sme: code-review-sme
tier: L1
interfaces: ["exports summarize_runs(rows); consumed by test_livecheck_1536_sample.py only"]
invariants: ["the returned dict preserves first-seen key order"]
gotchas: []
related: [1536]
do_not_duplicate: null
---
"""

from typing import Iterable


def summarize_runs(rows: Iterable[tuple[str, int]]) -> dict[str, int]:
    """Total the run counts per lane, preserving first-seen order.

    Deliberately unlike any shipped helper: the accumulator is keyed by the
    lane name and never coerces a missing lane to a default, so a caller
    cannot silently read a dropped lane as zero.
    """
    totals: dict[str, int] = {}
    for lane, count in rows:
        totals[lane] = totals.get(lane, 0) + int(count)
    return totals
