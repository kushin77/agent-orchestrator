"""Happy path: one tenant ticket end-to-end through the full lifecycle.

The acceptance criterion (issue #634) is a ticket that moves
``created -> decomposed -> dispatched -> executed -> reviewed -> closed`` and
is durable through the file-backed event store.  These tests drive the real
:class:`core.tickets.TicketRuntime` over a real :class:`core.runtime.Engine`
with the real :class:`engine.multiagent.planner.HierarchicalPlanner` as the
injected decomposer — no parallel machinery, no mocks of the lifecycle.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from core.events import InMemoryEventStore
from core.model import WorkflowStatus
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from core.tickets import TicketRuntime, TicketState, register_ticket_handlers
from core.tickets.model import (
    ENGINEER_TASK_CLOSED,
    ENGINEER_TASK_CREATED,
    PLAN_DECOMPOSED,
    TICKET_DISPATCHED,
    TICKET_EXECUTED,
    TICKET_REVIEWED,
)

from engine.multiagent.model import (
    FanOutPlan,
    HierarchyConfig,
    HierarchyReport,
    Mission,
    Subtask,
    ok_result,
)
from engine.multiagent.planner import HierarchicalPlanner
from engine.multiagent.runner import ScriptedRunner

TENANT = "acme"
TICKET_ID = "TCK-100"
MISSION_ID = "mission-tck-100"
OBJECTIVE = "restore the nightly reconciliation feed"


def _plan(mission: Mission, subtasks: Tuple[Subtask, ...], planner: str) -> FanOutPlan:
    return FanOutPlan(
        mission_id=mission.mission_id, planner_lane_id=planner, subtasks=subtasks
    )


def _ok_runner() -> ScriptedRunner:
    return (
        ScriptedRunner()
        .on("analyst-1", "t1", ok_result("analyst-1", "t1", output={"cause": "stale token"}))
        .on("reviewer-1", "t2", ok_result("reviewer-1", "t2", output={"totals": "ok"}))
    )


def _work_plan(ctx: Any) -> FanOutPlan:
    """The injected decomposition callable (planner seam: gets a context)."""
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
                objective="verify reconciliation totals",
                lane_id="reviewer",
                agent_id="reviewer-1",
            ),
        ),
        "planner",
    )


def _world(
    planner_lane: Any,
    specialist_lanes: Any,
    *,
    max_escalation_rounds: int = 2,
) -> Tuple[Engine, TicketRuntime, HierarchicalPlanner]:
    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    engine = Engine(store=InMemoryEventStore(), namespaces=registry)
    planner = HierarchicalPlanner(
        _ok_runner(),
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_escalation_rounds=max_escalation_rounds),
    )
    register_ticket_handlers(
        engine, decomposer=planner, max_escalation_rounds=max_escalation_rounds
    )
    return engine, TicketRuntime(engine, tenant=TENANT), planner


def _run_ticket(tickets: TicketRuntime, **kwargs: Any) -> Any:
    from core.tickets import ticket_workflow

    spec = ticket_workflow(
        tenant=TENANT,
        ticket_id=TICKET_ID,
        mission_id=MISSION_ID,
        objective=OBJECTIVE,
        decomposer=_work_plan,
        dispatch_lane="analyst",
        **kwargs,
    )
    return tickets.run(TICKET_ID, spec, title="reconciliation feed down")


def test_happy_path_reaches_closed_with_every_lifecycle_state(
    planner_lane, specialist_lanes
):
    """The ticket walks all six states in order and ends CLOSED."""
    _engine, tickets, _planner = _world(planner_lane, specialist_lanes)
    projection = _run_ticket(
        tickets,
        review={"reviewed_by": "ops-lead", "approved": True, "rationale": "verified"},
    )

    assert projection.state is TicketState.CLOSED
    assert projection.closed is True
    assert projection.lifecycle() == (
        "created",
        "decomposed",
        "dispatched",
        "executed",
        "reviewed",
        "closed",
    )
    assert projection.tenant == TENANT
    assert projection.ticket_id == TICKET_ID


def test_happy_path_decomposition_is_the_real_planner_report(
    planner_lane, specialist_lanes
):
    """The projected decomposition carries the planner's own numbers."""
    _engine, tickets, _planner = _world(planner_lane, specialist_lanes)
    projection = _run_ticket(tickets)

    decomposition = projection.decomposition
    assert decomposition is not None
    assert decomposition.mission_id == MISSION_ID
    assert decomposition.planner_lane_id == "planner"
    assert decomposition.max_escalation_rounds == 2
    # A clean one-round mission: one dispatch round, no escalation used.
    assert decomposition.rounds == 1
    assert decomposition.escalation_count == 0
    assert decomposition.subtask_count == 2
    assert decomposition.failures == ()
    assert decomposition.escalated == ()
    assert decomposition.resolved is True
    assert {f["subtask_id"] for f in decomposition.findings} == {"t1", "t2"}


def test_happy_path_dispatch_and_execution_are_recorded(
    planner_lane, specialist_lanes
):
    _engine, tickets, _planner = _world(planner_lane, specialist_lanes)
    projection = _run_ticket(tickets)

    assert projection.dispatch is not None
    assert projection.dispatch["lane"] == "analyst"
    assert projection.dispatch["queue"] == f"{TENANT}:default"
    assert projection.execution is not None
    assert projection.execution["subtask_count"] == 2
    assert projection.execution["resolved"] is True
    assert projection.review is None  # un-reviewed run: an approved-gate default


def test_ticket_workflow_terminal_status_is_succeeded(
    planner_lane, specialist_lanes
):
    """CLOSED comes from a genuinely green engine run, not from a marker."""
    engine, tickets, _planner = _world(planner_lane, specialist_lanes)
    projection = _run_ticket(tickets)
    assert projection.closed

    execution = engine.get_workflow(TENANT, TICKET_ID)
    assert execution.status is WorkflowStatus.SUCCEEDED


def test_events_carry_tenant_scope_and_are_ordered(
    planner_lane, specialist_lanes
):
    """Every ticket lifecycle event carries the tenant, in lifecycle order."""
    engine, tickets, _planner = _world(planner_lane, specialist_lanes)
    _run_ticket(tickets)

    records = engine.store.events(TENANT, TICKET_ID)
    kinds = [
        record.kind.value
        for record in records
        if record.kind.value
        in {
            ENGINEER_TASK_CREATED,
            PLAN_DECOMPOSED,
            TICKET_DISPATCHED,
            TICKET_EXECUTED,
            TICKET_REVIEWED,
            ENGINEER_TASK_CLOSED,
        }
    ]
    # The four lane-owned lifecycle events land in lifecycle order, with the
    # CLOSED anchor appended by the close step itself.
    assert kinds == [
        PLAN_DECOMPOSED,
        TICKET_DISPATCHED,
        TICKET_EXECUTED,
        TICKET_REVIEWED,
        ENGINEER_TASK_CLOSED,
    ]
    # The CREATED anchor is the engine's own ``workflow_started``; it is mapped
    # rather than emitted, so it never appears as a separate event.
    assert ENGINEER_TASK_CREATED not in [
        record.kind.value for record in records
    ]

    lifecycle_records = [
        record
        for record in records
        if record.kind.value
        in {
            PLAN_DECOMPOSED,
            TICKET_DISPATCHED,
            TICKET_EXECUTED,
            TICKET_REVIEWED,
        }
    ]
    assert lifecycle_records, "expected lifecycle events in the transcript"
    for record in lifecycle_records:
        # Tenant scope travels in the payload *and* in the engine's envelope.
        assert record.payload["tenant"] == TENANT
        assert record.namespace_id == TENANT
        assert record.payload["ticket_id"] == TICKET_ID

    # The engine's own anchors are tenant-scoped too.
    started = records[0]
    assert started.namespace_id == TENANT
    assert started.payload["inputs"]["tenant"] == TENANT
    assert started.payload["inputs"]["ticket_id"] == TICKET_ID


def test_projection_is_a_pure_function_of_the_log(planner_lane, specialist_lanes):
    """The same log always projects the same ticket (deterministic)."""
    engine, tickets, _planner = _world(planner_lane, specialist_lanes)
    projection = _run_ticket(tickets)
    records = engine.store.events(TENANT, TICKET_ID)

    for _ in range(3):
        again = TicketRuntime.project(records, tenant=TENANT, ticket_id=TICKET_ID)
        assert again.as_dict() == projection.as_dict()


def test_rejected_review_blocks_closure(planner_lane, specialist_lanes):
    """A review that did not approve leaves the ticket REVIEWED, not CLOSED."""
    _engine, tickets, _planner = _world(planner_lane, specialist_lanes)
    projection = _run_ticket(
        tickets,
        review={
            "reviewed_by": "ops-lead",
            "approved": False,
            "rationale": "totals do not reconcile",
        },
    )
    assert projection.state is TicketState.REVIEWED
    assert projection.closed is False
    assert projection.review is not None
    assert projection.review.approved is False
    assert projection.approved is False
    # Honest outcome: the execution still ran, so REVIEWED is reachable.
    assert "reviewed" in projection.lifecycle()
    assert "closed" not in projection.lifecycle()
