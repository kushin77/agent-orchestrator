"""The live stage projection — every in-scope item, where it really sits (issue #885).

---knowledge---
module_id: governance.lifecycle.live
system: governance
app: lifecycle
solution_class: class
patterns: [provoked-negative-control]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [project, check_live]
invariants: ""
gotchas: ""
related: ["#885"]
do_not_duplicate: null
---knowledge---

``cli.py status --issue <n>`` answers "where does *this* item sit"; nothing in
the package answered "where does *everything on the board* sit" without an
operator looping the single-issue verb over every number. This module is that
projection, exposed through the existing ``status`` verb's ``--live`` flag
rather than a new top-level verb — a new verb would need registering in
``control-plane/control/verbs.yaml`` / ``functions.yaml`` / ``fixtures/bodies.json``
and red ``check-control-verbs.sh``, for a read-only view of state the CLI
already collects.

It is a **projection**, not a second collector: :func:`project` takes the same
lifecycle record ``audit``/``status``/``close`` already read (``collect_from_github``
in ``cli.py``, or a fixture in a test) and derives each item's stage from
``model.stage_of`` — the one place that decision is made — so a live feed can
never show a stage the rest of the package would disagree with. Two items with
the same artifacts always project to the same stage; that is the property
:func:`check_live` (the gate's drift provocation) exists to break on purpose,
with a projector that computes ``stage`` from a field the record does not
actually carry, to prove the check would refuse it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from governance.lifecycle.model import STAGES, stage_of


def project(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Every in-scope item's stage, derived from the SAME facts the record carries.

    The projection states its own scope (mirroring ``audit.hygiene``) so a
    narrow live feed is never mistaken for a full board snapshot.
    """
    items: List[Dict[str, Any]] = list(record.get("items") or [])
    projected = [
        {
            "issue": item.get("issue"),
            "title": item.get("title", ""),
            "stage": stage_of(item),
        }
        for item in items
    ]
    by_stage: Dict[str, int] = {stage: 0 for stage in STAGES}
    for entry in projected:
        by_stage[entry["stage"]] = by_stage.get(entry["stage"], 0) + 1
    return {
        "scope": record.get("scope") or "unspecified",
        "collected_at": record.get("collected_at") or "",
        "stages": list(STAGES),
        "items": projected,
        "by_stage": by_stage,
    }


def check_live(record: Mapping[str, Any], projected: Mapping[str, Any]) -> List[str]:
    """Refuse a live feed that has drifted from the record it claims to project.

    Every entry in ``projected["items"]`` must name an issue this record
    actually carries, and its ``stage`` must equal ``model.stage_of`` computed
    fresh from that item's own facts — never a cached or independently
    recomputed value. This is what the gate's drift provocation exercises: a
    hand-built "live" document naming a stage the record's own artifacts do
    not support is refused, by issue, rather than trusted because it *looks*
    like a projection.
    """
    problems: List[str] = []
    real_stage = {item.get("issue"): stage_of(item) for item in record.get("items") or []}
    for entry in projected.get("items") or []:
        issue = entry.get("issue")
        if issue not in real_stage:
            problems.append(f"#{issue}: the live feed names an issue the record does not carry")
            continue
        expected = real_stage[issue]
        actual = entry.get("stage")
        if actual != expected:
            problems.append(
                f"#{issue}: the live feed claims stage {actual!r}, but the record's own artifacts "
                f"project to {expected!r} — the feed has drifted from the real store"
            )
    return problems


__all__ = ["project", "check_live"]
