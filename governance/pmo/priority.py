"""``priority`` — a single explainable priority order over open work.

Every term is a **derived query over the ticket graph** (:mod:`graph`) plus the
declared weights in :mod:`policy` — never a new ledger, never a fabricated
number. Each item's ``score`` is the sum of:

* ``priority``   <- the board's ``priority:P0/P1/P2`` label (``policy.priority_weights``)
* ``aging``      <- the item's aging tier, exactly as :func:`views.aging` computes it
* ``fanout``     <- ``policy.fanout_weight_per_blocker`` × how many *other* open
                    tickets name this one in their own ``blocked_by`` (a ticket
                    that unblocks more work outranks one that unblocks nothing)
* ``readiness``  <- ``policy.readiness_bonus`` when the ticket has no *open*
                    blocker (ready work outranks work still waiting)
* ``capacity``   <- **negative**: ``policy.capacity_penalty_per_inflight`` ×
                    the owner's current in-progress count (a loaded owner is
                    depriotised, not starved — it never zeroes the score)

A term with no committed source in this repo (``sla_breach`` — see
``policy.yaml``'s own comment) is never invented: it is carried in the
document's ``unsourced`` block, always contributing 0, and cited by name.

The sum is **additive, not multiplicative** on purpose: a multiplicative
formula lets any single zero term (including the declared-zero ``sla_breach``)
collapse every score to zero, which would make the whole priority order a
formality. Addition keeps every term's contribution legible on its own line.

Scores are rounded to 2 decimal places before sorting and the tiebreak is the
board issue number ascending — unconditional, so two items that tie on every
weighted term still produce one deterministic order (this is what
``--check``'s re-derive-and-compare is asserting: not just "the code ran
twice" but "ties do not depend on dict/set iteration order").

---knowledge---
module_id: governance.pmo.priority
system: governance
app: pmo
solution_class: enterprise
patterns: [explainable-order, deterministic-ties]
derives_from: null
owner_sme: pmo-sme
tier: L1
interfaces: [priority]
invariants: "ties do not depend on dict or set iteration order, so re-deriving twice is stable"
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any

from graph import Graph, age_days, issue_number
from policy import Policy
from views import AGING_TIERS, Finding, View, _tier


def _aging_tier(graph: Graph, ticket: str) -> str:
    anchor = graph.anchors.get(ticket, "")
    if not anchor:
        return "none"
    days = age_days(anchor, graph.clock)
    if days is None:
        return "none"
    return _tier(days) or "none"


def _is_ready(graph: Graph, ticket: str) -> bool:
    """No *open* blocker: every ``blocked_by`` target is closed (or absent)."""
    for target in graph.blocked_by(ticket):
        number = issue_number(target)
        if number is None:
            continue  # an edge to a node the graph does not carry — deps flags it
        blocker_ticket = f"kushin77/agent-orchestrator#{number}"
        if blocker_ticket in graph.tickets and not graph.is_closed(blocker_ticket):
            return False
    return True


def _fanout(graph: Graph, ticket: str) -> int:
    """How many other open tickets name this one in their ``blocked_by``."""
    number = issue_number(ticket)
    if number is None:
        return 0
    needle = f"#{number}"
    count = 0
    for other in graph.tickets:
        if other == ticket or graph.is_closed(other):
            continue
        if needle in graph.blocked_by(other):
            count += 1
    return count


def _inflight_by_owner(graph: Graph) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ticket in graph.tickets:
        owner = graph.owner(ticket)
        if owner and graph.status(ticket) == "in-progress":
            counts[owner] = counts.get(owner, 0) + 1
    return counts


def priority(graph: Graph, policy: Policy) -> View:
    findings: list[Finding] = []
    inflight = _inflight_by_owner(graph)
    items: list[dict[str, Any]] = []

    for ticket in sorted(graph.tickets):
        if graph.is_closed(ticket):
            continue
        number = issue_number(ticket)
        if number is None:
            continue  # priority ranks board work; ledger-only nodes have no issue

        labels = graph.labels_for(ticket)
        priority_score, level = policy.priority_weight(labels)
        tier = _aging_tier(graph, ticket)
        aging_score = policy.aging_weight(tier)
        fanout = _fanout(graph, ticket)
        fanout_score = policy.fanout_weight_per_blocker * fanout
        ready = _is_ready(graph, ticket)
        readiness_score = policy.readiness_bonus if ready else 0.0
        owner = graph.owner(ticket)
        inflight_count = inflight.get(owner, 0) if owner else 0
        capacity_score = -policy.capacity_penalty_per_inflight * inflight_count

        total = round(
            priority_score + aging_score + fanout_score + readiness_score + capacity_score, 2
        )

        items.append(
            {
                "ticket": ticket,
                "number": number,
                "score": total,
                "terms": {
                    "priority": {"value": priority_score, "level": level, "source": "board priority:* label"},
                    "aging": {"value": aging_score, "tier": tier, "source": "views.aging tier"},
                    "fanout": {"value": fanout_score, "blockers_of_others": fanout, "source": "deps blocked_by edges"},
                    "readiness": {"value": readiness_score, "ready": ready, "source": "deps blocked_by open-ness"},
                    "capacity": {"value": capacity_score, "owner_inflight": inflight_count, "source": "lanes in-progress count"},
                },
                "owner": owner,
                "status": graph.status(ticket),
                "ready": ready,
            }
        )

    items.sort(key=lambda item: (-item["score"], item["number"]))
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank

    document = {
        "view": "priority",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "items": items,
        "unsourced": {
            name: {"weight": term["weight"], "reason": term["reason"]}
            for name, term in policy.unsourced_terms.items()
        },
    }
    return View("priority", graph.clock, document, findings)
