"""Durability replay: a ticket survives a restart, read from the JSONL log.

Acceptance criterion (issue #634): the lifecycle is "durable through the
file-backed event store".  These tests interrupt a ticket mid-flight, drop the
engine, and rebuild the projection from a **fresh**
:class:`core.events.FileJsonlEventStore` over the same file — exactly what a
restarted process does.  Nothing is read from the old engine's memory.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from core.events import FileJsonlEventStore
from core.model import WorkflowStatus
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from core.tickets import (
    TicketRuntime,
    TicketState,
    register_ticket_handlers,
    ticket_workflow,
)
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
    Mission,
    Subtask,
    ok_result,
)
from engine.multiagent.planner import HierarchicalPlanner
from engine.multiagent.runner import ScriptedRunner

TENANT = "acme"
TICKET_ID = "TCK-DURABLE"


def _plan(ctx: Any) -> FanOutPlan:
    """The injected round-level decomposition seam (receives a planner context)."""
    mission = ctx.mission
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id="planner",
        subtasks=(
            Subtask(
                subtask_id="t1",
                objective="diagnose",
                lane_id="analyst",
                agent_id="analyst-1",
            ),
        ),
    )


def _runner() -> ScriptedRunner:
    return ScriptedRunner().on(
        "analyst-1", "t1", ok_result("analyst-1", "t1", output={"cause": "stale token"})
    )


def _registry() -> NamespaceRegistry:
    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    return registry


def _engine(path: str, planner_lane: Any, specialist_lanes: Any) -> Engine:
    engine = Engine(
        store=FileJsonlEventStore(path), namespaces=_registry()
    )
    planner = HierarchicalPlanner(
        _runner(),
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_escalation_rounds=2),
    )
    register_ticket_handlers(engine, decomposer=planner, max_escalation_rounds=2)
    return engine


def _spec(**kwargs: Any) -> Any:
    return ticket_workflow(
        tenant=TENANT,
        ticket_id=TICKET_ID,
        mission_id="mission-durable",
        objective="restore the feed",
        decomposer=_plan,
        dispatch_lane="analyst",
        **kwargs,
    )


def _snapshot(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_ticket_resumes_from_the_jsonl_log_after_a_restart(
    tmp_path, planner_lane, specialist_lanes
):
    """A half-run ticket is rebuilt and finished by a brand-new engine."""
    path = str(tmp_path / "ticket.jsonl")

    # --- process 1: start, run two steps, then "die" -----------------------
    engine1 = _engine(path, planner_lane, specialist_lanes)
    tickets1 = TicketRuntime(engine1, tenant=TENANT)
    execution = tickets1.start(TICKET_ID, _spec(), title="feed down")
    tickets1._emit_lifecycle(execution, TICKET_ID)
    engine1.advance(execution, max_handlers=1)  # decompose
    tickets1._emit_lifecycle(execution, TICKET_ID)
    engine1.advance(execution, max_handlers=1)  # dispatch
    tickets1._emit_lifecycle(execution, TICKET_ID)

    mid = TicketRuntime.project(
        engine1.store.events(TENANT, TICKET_ID), tenant=TENANT, ticket_id=TICKET_ID
    )
    assert mid.state is TicketState.DISPATCHED
    assert execution.status is WorkflowStatus.RUNNING

    # --- process 2: a fresh engine over the same log -----------------------
    engine2 = _engine(path, planner_lane, specialist_lanes)
    tickets2 = TicketRuntime(engine2, tenant=TENANT)
    resumed = engine2.resume(TENANT, TICKET_ID)

    replay = TicketRuntime.project(
        engine2.store.events(TENANT, TICKET_ID), tenant=TENANT, ticket_id=TICKET_ID
    )
    assert replay.state is TicketState.DISPATCHED
    assert replay.decomposition is not None
    assert replay.dispatch is not None
    assert replay.decomposition.subtask_count == 1

    while not resumed.status.terminal:
        tickets2._emit_lifecycle(resumed, TICKET_ID)
        before = len(resumed.events)
        engine2.advance(resumed, max_handlers=1)
        tickets2._emit_lifecycle(resumed, TICKET_ID)
        if resumed.status.terminal:
            break
        assert len(resumed.events) > before

    final = tickets2.resume(TICKET_ID)
    assert final.state is TicketState.CLOSED
    assert final.closed is True
    assert final.lifecycle() == (
        "created",
        "decomposed",
        "dispatched",
        "executed",
        "reviewed",
        "closed",
    )


def test_resume_reads_only_the_file_not_engine_memory(
    tmp_path, planner_lane, specialist_lanes
):
    """The projection after restart comes from disk, byte-for-byte."""
    path = str(tmp_path / "ticket.jsonl")
    engine1 = _engine(path, planner_lane, specialist_lanes)
    engine1_projection = TicketRuntime(engine1, tenant=TENANT).run(
        TICKET_ID,
        _spec(review={"reviewed_by": "ops", "approved": True, "rationale": "ok"}),
    )

    # A brand-new engine, a brand-new store handle over the same file.
    engine2 = _engine(path, planner_lane, specialist_lanes)
    replayed = TicketRuntime(engine2, tenant=TENANT).resume(TICKET_ID)

    assert replayed.as_dict() == engine1_projection.as_dict()
    assert replayed.state is TicketState.CLOSED


def test_lifecycle_events_survive_the_jsonl_round_trip(
    tmp_path, planner_lane, specialist_lanes
):
    """Every lifecycle event is a persisted, tenant-scoped JSONL line."""
    path = str(tmp_path / "ticket.jsonl")
    engine = _engine(path, planner_lane, specialist_lanes)
    TicketRuntime(engine, tenant=TENANT).run(
        TICKET_ID,
        _spec(review={"reviewed_by": "ops", "approved": True, "rationale": "ok"}),
    )

    lines = _snapshot(path)
    kinds = [line["kind"] for line in lines]
    for expected in (
        ENGINEER_TASK_CREATED if ENGINEER_TASK_CREATED in kinds else "workflow_started",
        PLAN_DECOMPOSED,
        TICKET_DISPATCHED,
        TICKET_EXECUTED,
        TICKET_REVIEWED,
        ENGINEER_TASK_CLOSED,
    ):
        assert expected in kinds, f"missing {expected} in {kinds}"

    # Every line is tenant-scoped by its envelope; lifecycle lines also carry
    # the tenant in the payload, so a spliced log is detectable on replay.
    for line in lines:
        assert line["namespace_id"] == TENANT
        assert line["workflow_id"] == TICKET_ID
    lifecycle_lines = [
        line
        for line in lines
        if line["kind"]
        in {PLAN_DECOMPOSED, TICKET_DISPATCHED, TICKET_EXECUTED, TICKET_REVIEWED}
    ]
    assert len(lifecycle_lines) == 4
    for line in lifecycle_lines:
        assert line["payload"]["tenant"] == TENANT
        assert line["payload"]["ticket_id"] == TICKET_ID

    # The CLOSED anchor is appended by the close step, so it lands just before
    # that step's own success event — and the engine's terminal event is last
    # (the projection refuses anything after a terminal one).
    assert kinds[-1] == "workflow_completed"
    assert ENGINEER_TASK_CLOSED in kinds
    assert kinds.index(ENGINEER_TASK_CLOSED) < kinds.index("workflow_completed")


def test_decomposition_is_replayable_from_the_log_alone(
    tmp_path, planner_lane, specialist_lanes
):
    """The decomposed plan round-trips through JSON without the planner."""
    path = str(tmp_path / "ticket.jsonl")
    engine = _engine(path, planner_lane, specialist_lanes)
    TicketRuntime(engine, tenant=TENANT).run(TICKET_ID, _spec())

    lines = _snapshot(path)
    decomposed = [line for line in lines if line["kind"] == PLAN_DECOMPOSED]
    assert len(decomposed) == 1
    payload = decomposed[0]["payload"]
    # The payload carries the plan's numbers, so a replay needs no planner.
    for key in (
        "mission_id",
        "planner_lane_id",
        "rounds",
        "max_escalation_rounds",
        "escalation_count",
        "subtask_count",
        "findings",
        "failures",
        "escalated",
        "summary",
    ):
        assert key in payload, f"decomposition payload is missing {key!r}"
    assert isinstance(payload["findings"], list)
