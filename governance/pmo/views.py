"""The PMO views — derived queries over the ticket graph (issue #403).

CMR's doctrine (``vendor/CMR/docs/PROGRAM-MANAGEMENT.md``) names five PMO
abilities and a surface ``fleet/pmo.sh {deps,lanes,report,raid,aging}``. Here
each ability is a **query over the ticket graph** (:mod:`graph`), never a store:

======================  ==================================================
view                    derived from
======================  ==================================================
``deps``                ``blocked_by`` + ``goal`` edges
``lanes``               ``owner`` × the claim's lane, occupancy by status
``report``              tickets by ``goal`` / ``status`` / ``owner``
``raid``                R = live risks, A = assumptions, I = incident-kind,
                        D = decision-kind + dependency edges
``aging``               ticket timestamps + status age, tiered
``gates``               per-task review-gate state + the escalation rung it
                        reached (issue #635, workbook-4)
======================  ==================================================

Every view returns a :class:`View` whose ``document`` is a deterministic,
JSON-serialisable projection of the graph and whose ``findings`` are the reasons
the view is **NOT-OK**. The findings are the load-bearing part (GR-12): a view
that can never be wrong is a formality.

Two rules the views hold to, both from the acceptance criteria:

* **no second source of ``status``** — ``status`` is read from the ticket node,
  which the claim ledger is the single writer of; the board's ``state`` is used
  only for the open/closed lifecycle, which is a different fact.
* **every risk names its owner** — an R item (a *live* risk, see
  :func:`raid`) that names no owner is a finding naming the ticket.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from graph import Graph, age_days, issue_number

#: Aging tiers, in days, per CMR doctrine (`pmo.sh aging`: "older than 14 days,
#: tiered watch/attention/red/postmortem").
AGING_TIERS: tuple[tuple[str, float], ...] = (
    ("postmortem", 90.0),
    ("red", 60.0),
    ("attention", 30.0),
    ("watch", 14.0),
)

#: Risk levels that make a ticket *at risk* rather than merely labelled.
AT_RISK = ("high", "critical")

#: Ticket kinds the incident register (and its RCA / corrective-action / lesson
#: family) projects — the RAID **I** section.
INCIDENT_KINDS = ("incident", "rca", "corrective-action", "lesson")

#: Ticket kinds that carry a decision/proposal — the RAID **D** section. The
#: generic improvement register (an open ``SUGGEST-*``) is the fleet's decision
#: node (ADR-0014), so it projects as ``suggestion``.
DECISION_KINDS = ("suggestion",)


@dataclass(frozen=True)
class Finding:
    """A reason the view is NOT-OK. ``subject`` is the offending ticket/edge."""

    code: str
    subject: str
    detail: str

    def render(self) -> str:
        return f"{self.code}: {self.subject} — {self.detail}"


@dataclass
class View:
    """A derived view: its deterministic document plus any findings."""

    name: str
    clock: str
    document: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def text(self) -> str:
        return json.dumps(self.document, indent=2, sort_keys=True) + "\n"


def _ticket_ids(document: Any) -> set[str]:
    """Every ticket id a rendered view names (the ids a stale view can carry)."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            value = node.get("ticket")
            if isinstance(value, str) and value:
                found.add(value)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(document)
    return found


def _counts(document: Any) -> dict[str, int]:
    """Every integer leaf of a rendered view, keyed by its path (for staleness)."""
    out: dict[str, int] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key in sorted(node):
                walk(node[key], f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        elif isinstance(node, bool):
            return
        elif isinstance(node, int):
            out[path] = node

    walk(document, "$")
    return out


def reconcile(derived: View, saved: Any) -> list[Finding]:
    """Findings for a view that disagrees with a previously rendered one.

    This is the check that a view is a *projection*: a ticket that the saved view
    carries but the freshly derived graph does not (a ticket removed from the
    graph, left behind in a cached view) fails **by name**, and so does a ticket
    the graph has gained, or a summary count that moved. The PMO writes no cache;
    it must be able to *prove* a view it is handed is not one.
    """
    findings: list[Finding] = []
    saved_document = saved.get("document") if isinstance(saved, dict) and "document" in saved else saved
    if not isinstance(saved_document, dict):
        return [
            Finding(
                "view-unreadable",
                derived.name,
                "the view being checked against is not a rendered PMO view",
            )
        ]
    expected = set(derived.document.get("tickets") or [])
    carried = set(saved_document.get("tickets") or [])
    carried |= _ticket_ids(saved_document)
    for ticket in sorted(carried - expected):
        findings.append(
            Finding(
                "view-stale-ticket",
                ticket,
                "present in the saved view but absent from the freshly derived graph",
            )
        )
    for ticket in sorted(expected - carried):
        findings.append(
            Finding(
                "view-missing-ticket",
                ticket,
                "present in the derived graph but absent from the saved view",
            )
        )
    derived_counts = _counts(derived.document)
    saved_counts = _counts(saved_document)
    for path in sorted(set(derived_counts) | set(saved_counts)):
        if derived_counts.get(path) != saved_counts.get(path):
            findings.append(
                Finding(
                    "view-stale-count",
                    path,
                    "the saved view counts "
                    f"{saved_counts.get(path)!r} where the graph yields "
                    f"{derived_counts.get(path)!r}",
                )
            )
    return findings


# --- deps -------------------------------------------------------------------

def deps(graph: Graph) -> View:
    """``blocked_by`` + ``goal`` edges — who waits on whom.

    A NOT-OK edge is one that names a ticket the graph does not carry (an
    **orphan** edge: it joins nothing) or a **cycle** among the blocked-by edges
    (a wave that can never be ready).
    """
    findings: list[Finding] = []
    blocked: list[dict[str, Any]] = []
    goals: list[dict[str, Any]] = []
    edges: dict[str, list[str]] = {}

    for ticket in sorted(graph.tickets):
        waiting = graph.blocked_by(ticket)
        if waiting:
            blocked.append(
                {
                    "ticket": ticket,
                    "blocked_by": sorted(waiting),
                    "status": graph.status(ticket),
                    "owner": graph.owner(ticket),
                }
            )
            edges[ticket] = list(sorted(waiting))
            for target in sorted(waiting):
                if _resolve(graph, target) is None:
                    findings.append(
                        Finding(
                            "deps-orphan-edge",
                            f"{ticket} → {target}",
                            "the blocked-by edge names a ticket the graph does not carry",
                        )
                    )
        goal = graph.goal(ticket)
        if goal:
            goals.append({"ticket": ticket, "goal": goal})
            if _resolve(graph, goal) is None:
                findings.append(
                    Finding(
                        "deps-orphan-goal",
                        f"{ticket} → {goal}",
                        "the goal edge names a ticket the graph does not carry",
                    )
                )

    for cycle in _cycles(edges):
        findings.append(
            Finding(
                "deps-cycle",
                " → ".join(cycle),
                "the blocked-by edges form a cycle, so no wave can ever be ready",
            )
        )

    document = {
        "view": "deps",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "blocked": blocked,
        "goals": goals,
    }
    return View("deps", graph.clock, document, findings)


def _resolve(graph: Graph, reference: str) -> int | None:
    number = issue_number(reference)
    return number if number is not None and _ticket_of(number) in graph.tickets else None


def _ticket_of(number: int) -> str:
    return f"kushin77/agent-orchestrator#{number}"


def _cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """Every simple cycle the edges form, each reported once, deterministically."""
    adjacency: dict[str, list[str]] = {
        _ticket_of(int(key.rsplit("#", 1)[1])): [
            _ticket_of(int(target.rsplit("#", 1)[1])) for target in targets
        ]
        for key, targets in edges.items()
    }
    found: set[tuple[str, ...]] = set()
    for start in sorted(adjacency):
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for neighbour in adjacency.get(node, []):
                if neighbour == start and len(path) > 1:
                    found.add(tuple(sorted(path)))
                elif neighbour not in path:
                    stack.append((neighbour, path + [neighbour]))
    return [list(cycle) for cycle in sorted(found)]


# --- lanes ------------------------------------------------------------------

def lanes(graph: Graph) -> View:
    """Owner × lane occupancy over the graph.

    A NOT-OK lane is a ticket whose work is **in flight** (``in-progress``) and
    names no owner: work with no accountable agent is not a lane, it is a leak.
    """
    findings: list[Finding] = []
    by_owner: dict[str, dict[str, Any]] = {}
    for ticket in sorted(graph.tickets):
        owner = graph.owner(ticket)
        status = graph.status(ticket)
        if not owner:
            if status == "in-progress":
                findings.append(
                    Finding(
                        "lanes-unowned-work",
                        ticket,
                        "the ticket is in progress but the graph names no owner",
                    )
                )
            continue
        row = by_owner.setdefault(
            owner,
            {
                "owner": owner,
                "lane": graph.lane(ticket),
                "tickets": [],
                "in_progress": 0,
                "blocked": 0,
                "in_review": 0,
                "done": 0,
            },
        )
        row["tickets"].append(ticket)
        if not row["lane"]:
            row["lane"] = graph.lane(ticket)
        if status in ("in-progress", "blocked", "in-review", "done"):
            row[status.replace("-", "_")] += 1

    rows = [by_owner[owner] for owner in sorted(by_owner)]
    document = {
        "view": "lanes",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "lanes": rows,
    }
    return View("lanes", graph.clock, document, findings)


# --- report -----------------------------------------------------------------

def report(graph: Graph) -> View:
    """Status rollup by goal / status / owner.

    A rollup is a *view*, never an authority, so this derivation carries no
    finding of its own: it is falsifiable through the reconciliation the gate
    drives — a rollup computed from a stale cache (or one whose counts no longer
    match the graph) is caught by :func:`reconcile`, which is exactly the
    "a rollup computed from a stale cache must exit non-zero" control.
    """
    findings: list[Finding] = []

    def bucket(key: str) -> dict[str, int]:
        return {"key": key, "count": 0}

    goals: dict[str, dict[str, int]] = {}
    statuses: dict[str, dict[str, int]] = {}
    owners: dict[str, dict[str, int]] = {}
    counted = 0
    for ticket in sorted(graph.tickets):
        counted += 1
        goals.setdefault(graph.goal(ticket) or "(no goal)", bucket(graph.goal(ticket) or "(no goal)"))["count"] += 1
        statuses.setdefault(
            graph.status(ticket) or "(no status)", bucket(graph.status(ticket) or "(no status)")
        )["count"] += 1
        owners.setdefault(graph.owner(ticket) or "(unowned)", bucket(graph.owner(ticket) or "(unowned)"))["count"] += 1

    document = {
        "view": "report",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "counted": counted,
        "by_goal": [goals[key] for key in sorted(goals)],
        "by_status": [statuses[key] for key in sorted(statuses)],
        "by_owner": [owners[key] for key in sorted(owners)],
    }
    return View("report", graph.clock, document, findings)


# --- raid -------------------------------------------------------------------

def raid(graph: Graph) -> View:
    """Risks / Assumptions / Incidents / Decisions + dependency edges.

    * **R — live risks.** Blocked or at-risk open work the claim ledger actually
      holds (the ticket has a ``status``): live work with a live risk. The
      doctrine is explicit — *"every risk carries an owner and a named
      remediation"* — so an R item that names no owner is a **finding**.
    * **A — assumptions.** The risks the fleet is *assuming* will not bite: open
      tickets that carry a risk but are dormant (no live claim) and name no
      remediation. They are reported, not failed: an assumption is a fact about
      the programme, and surfacing it is the point.
    * **I — incidents.** The incident register and its RCA / corrective-action /
      lesson family (the ticket kinds the lessons ledger projects).
    * **D — decisions + dependencies.** The decision nodes (the improvement
      register) plus every ``blocked_by`` / ``goal`` edge.
    """
    findings: list[Finding] = []
    risks: list[dict[str, Any]] = []
    assumptions: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []

    for ticket in sorted(graph.tickets):
        facet = graph.raid(ticket)
        level = facet.get("risk") if isinstance(facet.get("risk"), str) else ""
        remediation = facet.get("remediation") if isinstance(facet.get("remediation"), str) else ""
        live = bool(graph.status(ticket)) and graph.status(ticket) != "done"
        closed = graph.is_closed(ticket)
        blocked = bool(graph.blocked_by(ticket))

        if live and not closed and (blocked or level in AT_RISK or remediation):
            risks.append(
                {
                    "ticket": ticket,
                    "risk": level or "blocked",
                    "blocked_by": sorted(graph.blocked_by(ticket)),
                    "remediation": remediation,
                    "owner": graph.owner(ticket),
                }
            )
            if not graph.owner(ticket):
                findings.append(
                    Finding(
                        "raid-unowned-risk",
                        ticket,
                        "a live risk names no owner (doctrine: every risk carries an owner)",
                    )
                )
            if remediation and remediation not in graph.tickets:
                findings.append(
                    Finding(
                        "raid-orphan-remediation",
                        f"{ticket} → {remediation}",
                        "the risk names a remediation the graph does not carry",
                    )
                )
        elif not closed and not live and level and not remediation:
            assumptions.append(
                {
                    "ticket": ticket,
                    "risk": level,
                    "owner": graph.owner(ticket),
                }
            )

        kind = graph.kind(ticket)
        if kind in INCIDENT_KINDS:
            lessons = graph.lessons(ticket)
            incidents.append(
                {
                    "ticket": ticket,
                    "kind": kind,
                    "incident": lessons.get("incident") or "",
                    "rca": lessons.get("rca") or "",
                    "corrective_actions": sorted(lessons.get("corrective_actions") or []),
                }
            )
        if kind in DECISION_KINDS:
            decisions.append({"ticket": ticket, "kind": kind, "status": graph.status(ticket)})

        if graph.blocked_by(ticket):
            dependencies.append(
                {"ticket": ticket, "relation": "blocked_by", "targets": sorted(graph.blocked_by(ticket))}
            )
        if graph.goal(ticket):
            dependencies.append({"ticket": ticket, "relation": "goal", "targets": [graph.goal(ticket)]})

    document = {
        "view": "raid",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "risks": risks,
        "assumptions": assumptions,
        "incidents": incidents,
        "decisions": decisions,
        "dependencies": dependencies,
    }
    return View("raid", graph.clock, document, findings)


# --- aging ------------------------------------------------------------------

def aging(graph: Graph) -> View:
    """What has been *waiting* too long, tiered watch/attention/red/postmortem.

    An **aging candidate** is open work the graph knows about — a ticket that is
    not closed and is either claimed (has a status), blocked, or carries a risk.
    Its age is measured from the timestamp at which it entered its current state,
    read from the graph's own committed inputs (the claim ledger's ``at``, the
    ledger record's ``date``); the clock is the latest timestamp those inputs
    themselves carry, so the view is offline and rebuildable.

    Two things are deliberately explicit rather than silent:

    * the **tier** every aged item lands in, and the **owner** that must carry it
      — an aging item that names no owner is a **finding** (nobody is watching
      what is rotting);
    * the tickets that are aging *candidates* but that the committed inputs give
      no timestamp for. They are reported as ``unauditable`` with the reason the
      board cannot date them, because a view that quietly dropped them would be
      claiming an age it does not have.
    """
    findings: list[Finding] = []
    items: list[dict[str, Any]] = []
    unauditable: list[dict[str, Any]] = []

    for ticket in sorted(graph.tickets):
        closed = graph.is_closed(ticket)
        remediation = graph.raid(ticket).get("remediation")
        remediation = remediation if isinstance(remediation, str) else ""
        if closed and not remediation:
            continue
        status = graph.status(ticket)
        candidate = (
            bool(status)
            or bool(graph.blocked_by(ticket))
            or bool(graph.raid(ticket).get("risk"))
            or bool(remediation)
        )
        if not candidate:
            continue

        anchor = graph.anchors.get(ticket, "")
        if not anchor:
            number = issue_number(ticket)
            reason = (
                "the board snapshot carries no filed-at timestamp"
                if number is not None
                else "no ledger record dates this node"
            )
            unauditable.append({"ticket": ticket, "reason": reason})
            continue
        days = age_days(anchor, graph.clock)
        if days is None:
            unauditable.append({"ticket": ticket, "reason": f"unparseable timestamp {anchor!r}"})
            continue

        tier = _tier(days)
        if tier is None and not (closed and remediation):
            continue
        item = {
            "ticket": ticket,
            "tier": tier or "postmortem",
            "age_days": round(days, 2),
            "anchor": anchor,
            "kind": graph.kind(ticket),
            "status": status,
            "owner": graph.owner(ticket),
        }
        items.append(item)
        if not item["owner"]:
            findings.append(
                Finding(
                    "aging-unowned-item",
                    ticket,
                    f"an aging item in tier '{item['tier']}' names no owner",
                )
            )

    document = {
        "view": "aging",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "items": items,
        "unauditable": unauditable,
        "candidates": len(items) + len(unauditable),
    }
    return View("aging", graph.clock, document, findings)


def _tier(days: float) -> str | None:
    for name, threshold in AGING_TIERS:
        if days >= threshold:
            return name
    return None


# --- gates ------------------------------------------------------------------

#: The board label that carries a task's *review-gate* verdict (issue #635).
#: The label vocabulary is the board's own; the view derives gate state from it
#: plus the ticket's ``status`` — never from a second store of ``status``.
GATE_LABEL_PREFIX = "review-gate:"

#: The declared escalation rungs (mirrors ``governance/merge/gates`` ordering):
#: a blocked task escalates COO (pacing) then CEO (board escalation), and the
#: chain terminates.  Declared here as data so the view can report the rung a
#: task reached without importing the merge package (the PMO reads the graph).
ESCALATION_RUNGS: tuple[str, ...] = ("COO", "CEO")

#: Ticket ``status`` values that mean "the gate is in play".
GATE_OPEN_STATUSES = ("in-review", "done")
GATE_CLOSED_STATUSES = ("blocked",)


def _gate_label(labels: tuple[str, ...]) -> str:
    for label in labels:
        if label.startswith(GATE_LABEL_PREFIX):
            return label[len(GATE_LABEL_PREFIX) :]
    return ""


def gates(graph: Graph) -> View:
    """The review-gate state of every task, plus the escalation rung it reached.

    This is the workbook-4 acceptance criterion "``governance/pmo`` views surface
    the gate state per task", expressed as a *derivation* over facts the graph
    already carries:

    * the ticket's ``status`` (the claim ledger's verdict — the one writer of
      ``status``) tells whether the gate is **open** (``in-review`` / ``done``),
      **closed** (``blocked``), or **pending** (in flight, not yet reviewed);
    * the board issue's ``review-gate:<verdict>`` label carries the *verdict*
      the review produced (``open`` / ``red`` / ``self-review`` / ...), when one
      has been recorded;
    * the ``review-escalation:<rung>`` label carries the C-suite rung a closed
      gate reached.

    The findings are the reasons this view is NOT-OK, and they are the same
    shape as every other view's: a **closed gate on a task the graph reports as
    done** (work that closed with a red gate — the exact failure the acceptance
    criteria forbid), a **done task that is subject to the gate but records no
    verdict** (a close nobody can evidence), and a **closed gate with no
    escalation rung, or a rung the chain does not name** (the declared chain is
    COO → CEO and it terminates).

    Adoption is explicit: the gate contract applies to a task that carries a
    ``review-gate:*`` or ``review-escalation:*`` label.  A legacy close that
    predates the gate carries neither and is reported as gate state (``pending``)
    rather than failed — a view that fails on all history is not a view.
    """
    findings: list[Finding] = []
    rows: list[dict[str, Any]] = []
    for ticket in sorted(graph.tickets):
        status = graph.status(ticket)
        labels = graph.labels_for(ticket)
        verdict = _gate_label(labels)
        escalation = ""
        for label in labels:
            if label.startswith("review-escalation:"):
                escalation = label[len("review-escalation:") :]
        # The recorded verdict label is authoritative when present — it is the
        # review's own finding — and the ticket ``status`` is the fallback for a
        # task that has not recorded one yet.  Order matters: ``done``/``in-review``
        # are open *statuses*, but a task can be done and still carry a red
        # verdict, and that contradiction is exactly what this view must surface.
        if verdict in ("red", "closed", "rejected"):
            state = "closed"
        elif verdict == "open":
            state = "open"
        elif status in GATE_OPEN_STATUSES:
            state = "open"
        elif status in GATE_CLOSED_STATUSES:
            state = "closed"
        else:
            state = "pending"
        closed = graph.is_closed(ticket)
        rows.append(
            {
                "ticket": ticket,
                "status": status,
                "gate": state,
                "verdict": verdict,
                "escalation": escalation,
                "owner": graph.owner(ticket),
            }
        )
        # a task the graph reports done, whose gate reads closed, is work that
        # closed through a red gate — the failure mode the lifecycle forbids
        if closed and state == "closed":
            findings.append(
                Finding(
                    "gate-closed-but-done",
                    ticket,
                    "the task is done but its review gate reads closed "
                    "(a red gate can never yield a closed task)",
                )
            )
        # a done task with no recorded verdict closed without evidence *when the
        # task is subject to the gate*.  Adoption is declared by a label: the
        # gate contract (issue #635) applies to tasks the fleet has opted in
        # (a ``review-gate:*`` label, or an escalation label).  A legacy close
        # that predates the gate carries neither, and reporting it would be a
        # finding about history, not about the gate — hundreds of false FAILs
        # are noise, and a view that always fails is not a view.
        gate_scoped = (
            verdict != ""
            or escalation != ""
            or any(label.startswith("review-escalation:") for label in labels)
        )
        if closed and gate_scoped and not verdict:
            findings.append(
                Finding(
                    "gate-verdict-missing",
                    ticket,
                    "the task is done and is subject to the review gate, but "
                    "records no review-gate verdict "
                    "(a close must name the verdict that opened the gate)",
                )
            )
        # an escalation past the terminal rung cannot be true
        if escalation and escalation not in ESCALATION_RUNGS:
            findings.append(
                Finding(
                    "gate-escalation-unrunged",
                    ticket,
                    f"the task names escalation rung {escalation!r}, which is "
                    f"not one of {', '.join(ESCALATION_RUNGS)} "
                    "(the declared chain terminates at the last rung)",
                )
            )
        # a closed gate must name the rung it escalated to (COO -> CEO)
        if state == "closed" and not escalation:
            findings.append(
                Finding(
                    "gate-escalation-missing",
                    ticket,
                    "the review gate is closed but the task names no "
                    "escalation rung (a blocked task escalates COO -> CEO)",
                )
            )

    document = {
        "view": "gates",
        "clock": graph.clock,
        "tickets": sorted(graph.tickets),
        "rungs": list(ESCALATION_RUNGS),
        "gates": rows,
    }
    return View("gates", graph.clock, document, findings)


# --- registry ---------------------------------------------------------------

VIEWS = {
    "deps": deps,
    "lanes": lanes,
    "report": report,
    "raid": raid,
    "aging": aging,
    "gates": gates,
}
