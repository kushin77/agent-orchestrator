"""``dispatch`` — attach each ready task to an agent, in priority order.

---knowledge---
module_id: governance.pmo.dispatch
system: governance
app: pmo
solution_class: enterprise
patterns: [derived-view-over-priority, single-source-of-order]
derives_from: governance/pmo/priority.py
owner_sme: pmo-sme
tier: L1
interfaces: [paperclip_ticket_record, dispatch, validate_plan]
invariants: "dispatch never re-derives a priority score; it only consumes priority.priority's order"
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

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

import clusters as clusters_mod
from graph import CannotAssess, Graph, issue_number
from policy import Policy
from priority import priority
from views import Finding, View

#: ADR-0012(b): "code / test / PR authoring routes to the `hermes` persona at
#: MED; research / docs / reporting routes to the `paperclip` persona at LOW."
#: This is PERSONA ROUTING (declared registry vocabulary), never a runtime
#: call — see docs/decision-records/ADR-0012-hermes-paperclip-boundary.md §(e)
#: "map the policy, do not couple the runtime". The classification signal is
#: the ticket's `kind` facet — a real, committed ADR-0014 contract field
#: (governance/pmo already reads it via graph.kind()), never an invented
#: keyword heuristic on free text: the non-code ticket kinds (the lessons/RCA
#: family plus the generic improvement register) are the research/reporting
#: half; the default `task` kind is the code-author/test/PR half.
_PAPERCLIP_KINDS = ("incident", "rca", "corrective-action", "lesson", "suggestion")


def _default_executor(graph: Graph, ticket: str, lane) -> str:
    """ADR-0012(b) persona routing. A lane's declared ``executor`` (SME-ROUTING
    style override) wins outright; otherwise the ticket ``kind`` decides."""
    if lane.executor:
        return lane.executor
    return "paperclip" if graph.kind(ticket) in _PAPERCLIP_KINDS else "hermes"


#: keys the frozen contract (docs/contracts/paperclip/ticket.schema.json)
#: knows about a ticket node — additionalProperties: false there, so a
#: dispatch-only field (lane/tier/executor/brief) never leaks into this
#: sub-record; it lives as a SIBLING key on the assignment instead.
_CONTRACT_KEYS = ("id", "owner", "status", "blocked_by", "goal", "evidence", "kind", "facets")


def paperclip_ticket_record(graph: Graph, ticket: str) -> dict[str, Any]:
    """A ``dispatch`` assignment's reporting half: fields shaped to the frozen
    ticket contract (ADR-0014's join node), a partial PROJECTION written to
    stdout only — never a store, never a second source of ``status``.

    Only fields the graph actually carries are populated (an unclaimed,
    dispatch-ready ticket legitimately has no ``owner``/``status``/``goal`` yet
    — inventing one to satisfy the contract's ``required`` list would be
    exactly the fabrication GR-12 forbids), so this is a **partial** record:
    every populated key's type/enum matches the contract exactly, but the
    contract's own ``required`` completeness is not claimed for a ticket that
    is not yet claimed.
    """
    record: dict[str, Any] = {"id": ticket}
    owner = graph.owner(ticket)
    if owner:
        record["owner"] = owner
    status = graph.status(ticket)
    if status:
        record["status"] = status
    blocked_by = graph.blocked_by(ticket)
    if blocked_by:
        record["blocked_by"] = sorted(blocked_by)
    goal = graph.goal(ticket)
    if goal:
        record["goal"] = goal
    kind = graph.kind(ticket)
    if kind and kind != "task":
        record["kind"] = kind
    raid = graph.raid(ticket)
    if raid:
        record["facets"] = {"raid": raid}
    return record

#: agent brief skeleton (goal-first, per ~/.claude/CLAUDE.md's `/focus` rules:
#: goal in the first line, then constraints, then context, then steps).
BRIEF_TEMPLATE = """GOAL: Resolve {ticket} ({lane} lane, {sme} SME, tier {tier}/{model}, executor {executor}) — read the issue, make the change, run the lane's gate, open a PR.
CONSTRAINTS: owns only {owns}; one issue = one lane = one branch (docs/EXECUTION-PLAN.md); never edit vendor/, .board/, .fleet/; escalate tier only on observed difficulty, never pre-emptively.
CONTEXT: priority rank {rank}, score {score} ({term_summary}); {owner_note}. Executor is ADR-0012(b) persona routing (declared vocabulary, no runtime call).
STEPS: 1) read the issue and AGENTS.md/CLAUDE.md for {lane}; 2) implement + test; 3) run the lane's gate (make verify or the lane-specific target); 4) open a PR closing {ticket}."""

#: cluster brief skeleton (--by-cluster): one agent, N member issues, one recipe.
CLUSTER_BRIEF_TEMPLATE = """GOAL: Apply cluster {cluster_id}'s recipe ({family}) across all {issue_count} member issues in one pass — {recipe}.
CONSTRAINTS: one agent, one branch per cluster (docs/EXECUTION-PLAN.md's "no two lanes share a file" extends to "no two clusters share an issue" — board-triage's own contract); tier {tier}/{model}, SME {sme}; escalate tier only on observed difficulty.
CONTEXT: priority rank {priority_rank}; evidence: {evidence}; member issues: {issues}.
STEPS: 1) read every member issue and confirm the shared recipe still applies; 2) apply the recipe per issue; 3) run the lane's gate once per issue (or the batch gate the recipe names); 4) open one PR per issue (or one batched PR if the recipe says so), each closing its issue."""


def _term_summary(item: dict[str, Any]) -> str:
    terms = item["terms"]
    return ", ".join(f"{name}={t['value']:+g}" for name, t in terms.items())


def _brief(item: dict[str, Any], lane, model: str, executor: str) -> str:
    owner = item.get("owner") or ""
    owner_note = f"currently unowned" if not owner else f"owner of record {owner!r} (unclaimed for this dispatch)"
    return BRIEF_TEMPLATE.format(
        ticket=item["ticket"],
        lane=lane.lane,
        sme=lane.sme,
        tier=lane.tier,
        model=model,
        executor=executor,
        owns=", ".join(lane.owns) or "(no owned globs declared — default lane)",
        rank=item["rank"],
        score=item["score"],
        term_summary=_term_summary(item),
        owner_note=owner_note,
    )


def _cluster_brief(cluster, model: str) -> str:
    return CLUSTER_BRIEF_TEMPLATE.format(
        cluster_id=cluster.id,
        family=cluster.family,
        issue_count=len(cluster.issues),
        recipe=cluster.recipe,
        tier=cluster.tier,
        model=model,
        sme=cluster.sme,
        priority_rank=cluster.priority_rank,
        evidence=cluster.evidence,
        issues=", ".join(f"{i['repo']}#{i['number']}" for i in cluster.issues),
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


def dispatch(
    graph: Graph,
    policy: Policy,
    wave: int = 1,
    wave_cap: int | None = None,
    by_cluster: bool = False,
    clusters: "clusters_mod.Clusters | None" = None,
) -> View:
    findings: list[Finding] = []
    cap = wave_cap if wave_cap is not None else policy.wave_cap_default
    if cap < 1:
        raise ValueError("wave_cap must be >= 1")

    prio = priority(graph, policy)
    items_by_ticket = {item["ticket"]: item for item in prio.document["items"]}

    # --- --by-cluster: one agent per batchable cluster, this wave only ------
    # clusters.json is optional, read-only INPUT from a separate lane
    # (governance/pmo/clusters.py). Absent -> fall back to the per-issue plan
    # below, unchanged. This branch never re-derives clustering itself (PMO
    # adds no ledger); it only filters/sorts the proposal it was handed.
    if by_cluster and clusters is not None and clusters.clusters:
        batch_assignments: list[dict[str, Any]] = []
        batch_deferred: list[dict[str, Any]] = []
        for cluster in sorted(clusters.clusters, key=lambda c: c.priority_rank):
            if cluster.wave != wave:
                batch_deferred.append(
                    {"cluster": cluster.id, "reason": f"cluster-wave-{cluster.wave}-not-requested-wave-{wave}"}
                )
                continue
            if not cluster.batchable:
                batch_deferred.append({"cluster": cluster.id, "reason": "cluster-not-batchable"})
                continue
            if len(batch_assignments) >= cap:
                batch_deferred.append({"cluster": cluster.id, "reason": "wave-cap-reached"})
                continue
            model = policy.model_tiers.get(cluster.tier, cluster.tier)
            issue_ids = [f"{i['repo']}#{i['number']}" for i in cluster.issues]
            batch_assignments.append(
                {
                    "cluster": cluster.id,
                    "family": cluster.family,
                    "priority_rank": cluster.priority_rank,
                    "lane": cluster.id,
                    "tier": cluster.tier,
                    "model": model,
                    "sme_profile": cluster.sme,
                    "evidence": cluster.evidence,
                    "issue_count": len(cluster.issues),
                    "issues": issue_ids,
                    "agent_brief": _cluster_brief(cluster, model),
                }
            )
        document = {
            "view": "dispatch",
            "clock": graph.clock,
            "tickets": sorted(graph.tickets),
            "wave": wave,
            "wave_cap": cap,
            "by_cluster": True,
            "clusters_source_generated_at": clusters.generated_at,
            "assignments": batch_assignments,
            "deferred": batch_deferred,
            "unowned_risks": [],
            "unclustered": list(clusters.unclustered),
        }
        findings.extend(validate_plan(document))
        return View("dispatch", graph.clock, document, findings)

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
        executor = _default_executor(graph, item["ticket"], lane)
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
                "executor": executor,
                "agent_brief": _brief(item, lane, model, executor),
                "paperclip_ticket": paperclip_ticket_record(graph, item["ticket"]),
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
        "by_cluster": False,
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
