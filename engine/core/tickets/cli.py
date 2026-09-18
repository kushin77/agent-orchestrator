"""Offline CLI demo of the tenant-facing agentic task manager (issue #634).

Run from anywhere::

    python3 engine/core/tickets/cli.py

It stands up a durable engine over a **file-backed** event store, provisions
the tenant, runs one ticket end-to-end through the full lifecycle
(created -> decomposed -> dispatched -> executed -> reviewed -> closed),
prints the projected ticket plus its tenant-scoped transcript, then proves
durability by re-projecting the lifecycle from the JSONL log alone (exactly
what a restarted process does).

Everything is offline and deterministic: the decomposer is scripted and the
"agents" are scripted, so no network and no model provider is involved.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

_here = os.path.dirname(os.path.abspath(__file__))
# engine/core/tickets -> engine/core -> engine
_engine_root = os.path.dirname(os.path.dirname(_here))
# engine -> repo root (so the PEP-420 `engine.multiagent` package resolves)
_repo_root = os.path.dirname(_engine_root)
for _path in (_engine_root, _repo_root):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.events import FileJsonlEventStore  # noqa: E402
from core.namespaces import NamespaceRegistry  # noqa: E402
from core.runtime import Engine  # noqa: E402

from engine.multiagent.model import (  # noqa: E402
    FanOutPlan,
    HierarchyConfig,
    Lane,
    LaneRole,
    Mission,
    Subtask,
    fail_result,
    ok_result,
)
from engine.multiagent.planner import HierarchicalPlanner  # noqa: E402
from engine.multiagent.runner import ScriptedRunner  # noqa: E402

from core.tickets import ticket_workflow  # noqa: E402
from core.tickets.handlers import register_ticket_handlers  # noqa: E402
from core.tickets.runtime import TicketRuntime  # noqa: E402

TENANT = "acme"
TICKET_ID = "TCK-1042"
MISSION_ID = "mission-tck-1042"
OBJECTIVE = "restore the nightly billing reconciliation feed"

PLANNER_LANE = Lane(
    lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",)
)
SPECIALIST_LANES = (
    Lane(lane_id="analyst", role=LaneRole.SPECIALIST, agent_ids=("analyst-1",)),
    Lane(lane_id="reviewer", role=LaneRole.SPECIALIST, agent_ids=("reviewer-1",)),
)


def _plan(mission: Mission, subtasks) -> FanOutPlan:
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=PLANNER_LANE.lane_id,
        subtasks=tuple(subtasks),
    )


def _work_plan(ctx: Any) -> FanOutPlan:
    """The injected decomposition callable (planner seam: receives a context)."""
    mission = ctx.mission
    return _plan(
        mission,
        (
            Subtask(
                subtask_id="t1",
                objective="diagnose the feed failure",
                lane_id="analyst",
                agent_id="analyst-1",
            ),
            Subtask(
                subtask_id="t2",
                objective="verify the reconciliation totals",
                lane_id="reviewer",
                agent_id="reviewer-1",
            ),
        ),
    )


def _runner() -> ScriptedRunner:
    return (
        ScriptedRunner()
        .on("analyst-1", "t1", ok_result("analyst-1", "t1", output={"cause": "stale token"}))
        .on("reviewer-1", "t2", ok_result("reviewer-1", "t2", output={"totals": "reconciled"}))
    )


def _decomposer() -> "object":
    """One-round decomposition callable (the planner's injected seam)."""
    return lambda ctx: _work_plan(ctx.mission)


def _escalating_decomposer(bound_rounds: int):
    """A decomposer that never resolves: the bound is what stops it."""

    def _decompose(ctx):
        # Round 0 dispatches a subtask that fails; every re-plan re-dispatches
        # the same failing subtask, so only ``max_escalation_rounds`` can end it.
        return _plan(
            ctx.mission,
            (
                Subtask(
                    subtask_id="t1",
                    objective="resolve the unresolved blocker",
                    lane_id="analyst",
                    agent_id="analyst-1",
                    required=True,
                ),
            ),
        )

    return _decompose


def _stubborn_plan(ctx: Any) -> FanOutPlan:
    """A plan that always fails: only ``max_escalation_rounds`` ends it."""
    return _plan(
        ctx.mission,
        (
            Subtask(
                subtask_id="t1",
                objective="resolve the unresolved blocker",
                lane_id="analyst",
                agent_id="analyst-1",
                required=True,
            ),
        ),
    )


def _registry() -> NamespaceRegistry:
    """A fresh registry with the demo tenant provisioned."""
    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    return registry


def _run_demo() -> int:
    workdir = tempfile.mkdtemp(prefix="ao634-tickets.")
    log_path = os.path.join(workdir, "ticket-events.jsonl")

    namespaces = _registry()
    engine = Engine(store=FileJsonlEventStore(log_path), namespaces=namespaces)

    # The escalation bound lives on the planner; the bound is what guarantees
    # a mission terminates (issue #634 acceptance #2).
    bound = 2
    planner = HierarchicalPlanner(
        _runner(),
        planner_lane=PLANNER_LANE,
        specialist_lanes=SPECIALIST_LANES,
        config=HierarchyConfig(max_escalation_rounds=bound),
    )
    register_ticket_handlers(
        engine, decomposer=planner, max_escalation_rounds=bound
    )
    tickets = TicketRuntime(engine, tenant=TENANT)

    spec = ticket_workflow(
        tenant=TENANT,
        ticket_id=TICKET_ID,
        mission_id=MISSION_ID,
        objective=OBJECTIVE,
        decomposer=_work_plan,
        dispatch_lane="analyst",
        review={
            "reviewed_by": "ops-lead",
            "approved": True,
            "rationale": "feed restored and totals reconciled",
        },
        sla_seconds=900.0,
    )

    projection = tickets.run(
        TICKET_ID, spec, title="billing reconciliation feed down"
    )

    print(f"tenant            : {projection.tenant}")
    print(f"ticket            : {projection.ticket_id} ({projection.title})")
    print(f"lifecycle         : {' -> '.join(projection.lifecycle())}")
    print(f"state             : {projection.state.value}")
    decomposition = projection.decomposition
    if decomposition is not None:
        print(
            "decomposition     : "
            f"{decomposition.subtask_count} subtask(s), "
            f"{decomposition.rounds} round(s), "
            f"escalations {decomposition.escalation_count}/"
            f"{decomposition.max_escalation_rounds}, "
            f"resolved={decomposition.resolved}"
        )
        for finding in decomposition.findings:
            print(f"  finding         : {finding['subtask_id']} -> {finding['output']}")
    if projection.review is not None:
        print("review            : NOT APPROVED (ticket not closable)")
    else:
        print("review            : approved (gate open)")

    # Durable: re-read the tenant transcript from the JSONL log alone, exactly
    # as a restarted process would.
    engine2 = Engine(
        store=FileJsonlEventStore(log_path),
        namespaces=namespaces,
        clock=engine.clock,
    )
    replayed = TicketRuntime(engine2, tenant=TENANT).resume(TICKET_ID)
    print(f"replayed from log : {replayed.state.value} matches live={replayed.as_dict() == projection.as_dict()}")

    records = FileJsonlEventStore(log_path).events(TENANT, TICKET_ID)
    print("tenant-scoped transcript:")
    for record in records:
        tenant_scope = record.payload.get("tenant", "—")
        print(f"  {record.seq:>2} {record.kind.value:<22} tenant={tenant_scope}")

    # Escalation bound: the same bound, an unresolvable mission.  The bound is
    # what terminates the mission — the planner never loops past it.
    stubborn_planner = HierarchicalPlanner(
        ScriptedRunner().on_agent(
            "analyst-1", fail_result("analyst-1", "t1", error="blocked")
        ),
        planner_lane=PLANNER_LANE,
        specialist_lanes=SPECIALIST_LANES,
        config=HierarchyConfig(max_escalation_rounds=bound),
    )
    stubborn_engine = Engine(
        store=FileJsonlEventStore(os.path.join(workdir, "stubborn-events.jsonl")),
        namespaces=_registry(),
    )
    register_ticket_handlers(
        stubborn_engine, decomposer=stubborn_planner, max_escalation_rounds=bound
    )
    stubborn_projection = TicketRuntime(stubborn_engine, tenant=TENANT).run(
        "TCK-1043",
        ticket_workflow(
            tenant=TENANT,
            ticket_id="TCK-1043",
            mission_id="mission-tck-1043",
            objective="resolve a blocker that never clears",
            decomposer=_stubborn_plan,
            dispatch_lane="analyst",
        ),
    )
    stubborn_decomposition = stubborn_projection.decomposition
    assert stubborn_decomposition is not None
    print(
        f"bound demo        : max_escalation_rounds={bound} stopped a stubborn "
        f"mission at {stubborn_decomposition.rounds} round(s), "
        f"escalations {stubborn_decomposition.escalation_count}/{bound}, "
        f"unresolved={list(stubborn_decomposition.escalated)}"
    )
    print(f"artifacts         : {workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_demo())
