"""``dispatch`` — attach each ready task to an agent, in priority order.

Built on :func:`priority.priority`: this module never re-derives a score, it
only *consumes* the priority order and answers "who runs this, on what, at
what tier, in which lane" for the top of it. Like every PMO view it is a
**derivation**, never a store — nothing here is written unless the caller asks
for ``--apply`` (implemented in ``cli.py``, never inside this module: the plan
derivation stays read-only so the gate can exercise it without touching
GitHub).

A **wave** is one lane-collision-free batch: at most one ready task per lane
(``docs/EXECUTION-PLAN.md``'s own contract — "no two lanes share a file in the
same wave"), up to ``wave_cap`` tasks total. Building the plan this way means a
lane collision inside one wave *cannot happen by construction* — the plan
builder skips a second candidate for a lane already filled and reports it
under ``deferred``. :func:`validate_plan` is the falsifiable half (GR-12): it
re-checks that invariant against an arbitrary plan document (including one a
test hands it on purpose), so the property is provable, not merely "true by
construction and untested".
"""

from __future__ import annotations

from typing import Any

from graph import Graph, issue_number
from policy import Policy
from priority import priority
from views import Finding, View

#: agent brief skeleton (goal-first, per ~/.claude/CLAUDE.md's `/focus` rules:
#: goal in the first line, then constraints, then context, then steps).
BRIEF_TEMPLATE = """GOAL: Resolve {ticket} ({lane} lane, {sme} SME, tier {tier}/{model}) — read the issue, make the change, run the lane's gate, open a PR.
CONSTRAINTS: owns only {owns}; one issue = one lane = one branch (docs/EXECUTION-PLAN.md); never edit vendor/, .board/, .fleet/; escalate tier only on observed difficulty, never pre-emptively.
CONTEXT: priority rank {rank}, score {score} ({term_summary}); {owner_note}.
STEPS: 1) read the issue and AGENTS.md/CLAUDE.md for {lane}; 2) implement + test; 3) run the lane's gate (make verify or the lane-specific target); 4) open a PR closing {ticket}."""


def _term_summary(item: dict[str, Any]) -> str:
    terms = item["terms"]
    return ", ".join(f"{name}={t['value']:+g}" for name, t in terms.items())


def _brief(item: dict[str, Any], lane, model: str) -> str:
    owner = item.get("owner") or ""
    owner_note = f"currently unowned" if not owner else f"owner of record {owner!r} (unclaimed for this dispatch)"
    return BRIEF_TEMPLATE.format(
        ticket=item["ticket"],
        lane=lane.lane,
        sme=lane.sme,
        tier=lane.tier,
        model=model,
        owns=", ".join(lane.owns) or "(no owned globs declared — default lane)",
        rank=item["rank"],
        score=item["score"],
        term_summary=_term_summary(item),
        owner_note=owner_note,
    )


def _is_unowned_live_risk(graph: Graph, ticket: str) -> bool:
    """Mirrors ``views.raid``'s R condition for a live risk with no owner."""
    facet = graph.raid(ticket)
    level = facet.get("risk") if isinstance(facet.get("risk"), str) else ""
    remediation = facet.get("remediation") if isinstance(facet.get("remediation"), str) else ""
    live = bool(graph.status(ticket)) and graph.status(ticket) != "done"
    closed = graph.is_closed(ticket)
    blocked = bool(graph.blocked_by(ticket))
    is_risk = live and not closed and (blocked or level in ("high", "critical") or remediation)
    return is_risk and not graph.owner(ticket)


def dispatch(graph: Graph, policy: Policy, wave: int = 1, wave_cap: int | None = None) -> View:
    findings: list[Finding] = []
    cap = wave_cap if wave_cap is not None else policy.wave_cap_default
    if cap < 1:
        raise ValueError("wave_cap must be >= 1")

    prio = priority(graph, policy)
    items_by_ticket = {item["ticket"]: item for item in prio.document["items"]}

    assignments: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    used_lanes: set[str] = set()
    unowned_risk_tickets = {t for t in graph.tickets if _is_unowned_live_risk(graph, t)}
    covered_risks: set[str] = set()

    for item in prio.document["items"]:
        if len(assignments) >= cap:
            deferred.append({"ticket": item["ticket"], "rank": item["rank"], "reason": "wave-cap-reached"})
            continue
        if not item["ready"]:
            deferred.append({"ticket": item["ticket"], "rank": item["rank"], "reason": "not-ready-open-blocker"})
            continue
        if item["owner"]:
            deferred.append({"ticket": item["ticket"], "rank": item["rank"], "reason": "already-owned"})
            continue

        lane = policy.lane_for(graph.labels_for(item["ticket"]))
        if lane.lane in used_lanes:
            deferred.append(
                {"ticket": item["ticket"], "rank": item["rank"], "reason": f"lane-occupied-this-wave:{lane.lane}"}
            )
            continue

        model = policy.model_tiers.get(lane.tier, lane.tier)
        assignments.append(
            {
                "ticket": item["ticket"],
                "rank": item["rank"],
                "score": item["score"],
                "lane": lane.lane,
                "owns": list(lane.owns),
                "tier": lane.tier,
                "model": model,
                "sme_profile": lane.sme,
                "agent_brief": _brief(item, lane, model),
            }
        )
        used_lanes.add(lane.lane)
        if item["ticket"] in unowned_risk_tickets:
            covered_risks.add(item["ticket"])

    unowned_risks = [
        {
            "ticket": ticket,
            "reason": "a live risk with no owner is deferred for triage, not silently dropped",
        }
        for ticket in sorted(unowned_risk_tickets - covered_risks)
        if ticket in items_by_ticket
    ]

    document = {
        "view": "dispatch",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "wave": wave,
        "wave_cap": cap,
        "assignments": assignments,
        "deferred": deferred,
        "unowned_risks": unowned_risks,
    }
    findings.extend(validate_plan(document))
    return View("dispatch", graph.clock, document, findings)


def validate_plan(document: dict[str, Any]) -> list[Finding]:
    """The falsifiable half (GR-12): provable, not merely "true by construction".

    Two refusals, both named after the offending ticket(s)/lane:

    * ``dispatch-lane-collision`` — two assignments in the same wave share a
      lane (docs/EXECUTION-PLAN.md: no two lanes share a file in one wave).
    * ``dispatch-unowned-risk`` — a live risk with no owner (the raid view's own
      R condition) that the plan neither assigns nor lists under
      ``unowned_risks`` with a reason — i.e. it was silently dropped.
    """
    findings: list[Finding] = []
    assignments = document.get("assignments") or []
    wave = document.get("wave")

    by_lane: dict[str, list[str]] = {}
    for entry in assignments:
        by_lane.setdefault(entry.get("lane", ""), []).append(entry.get("ticket", ""))
    for lane, tickets in sorted(by_lane.items()):
        if len(tickets) > 1:
            findings.append(
                Finding(
                    "dispatch-lane-collision",
                    f"wave {wave}: lane {lane}",
                    f"{len(tickets)} tasks assigned to one lane in one wave: {', '.join(sorted(tickets))}",
                )
            )

    assigned_tickets = {entry.get("ticket", "") for entry in assignments}
    named_unowned = {entry.get("ticket", "") for entry in (document.get("unowned_risks") or [])}
    for entry in document.get("_assert_unowned_risks", []):
        # test/gate seam: a caller may assert a ticket is a live unowned risk
        # that the plan must account for, without recomputing the graph.
        if entry not in assigned_tickets and entry not in named_unowned:
            findings.append(
                Finding(
                    "dispatch-unowned-risk",
                    entry,
                    "a live risk with no owner is neither assigned nor listed "
                    "under unowned_risks — it was silently dropped",
                )
            )

    return findings


def _issue(ticket: str) -> int | None:
    return issue_number(ticket)
