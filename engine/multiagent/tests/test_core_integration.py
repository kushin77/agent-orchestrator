"""Integration with engine/core durable steps: FAN_OUT + JOIN handlers.

The engine core (issue #21) hosts the FAN_OUT / JOIN step kinds; this suite
registers the multi-agent handlers on the core handler seam and proves the
hierarchical fan-out runs as *durable* engine steps — including that a JOIN
step reconstructs its aggregation from the persisted fan-out output after a
crash + resume (no hidden in-memory state).
"""

from __future__ import annotations

import pytest

from engine.core.events import FileJsonlEventStore, InMemoryEventStore
from engine.core.model import WorkflowStatus
from engine.core.namespaces import NamespaceRegistry
from engine.core.runtime import Engine

from engine.multiagent import core_seam
from engine.multiagent.core_seam import (
    HANDLER_FAN_OUT,
    HANDLER_JOIN,
    fan_join_workflow,
    register_multiagent_handlers,
)
from engine.multiagent.model import (
    FanOutPlan,
    HierarchyConfig,
    Lane,
    LaneRole,
    Mission,
    Subtask,
    fail_result,
    ok_result,
)
from engine.multiagent.runner import ScriptedRunner

PLANNER_LANE = Lane(lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",))
SPECIALIST_LANES = (
    Lane(lane_id="specialist-a", role=LaneRole.SPECIALIST, agent_ids=("s-a-1",)),
    Lane(lane_id="specialist-b", role=LaneRole.SPECIALIST, agent_ids=("s-b-1",)),
    Lane(lane_id="specialist-c", role=LaneRole.SPECIALIST, agent_ids=("s-c-1",)),
)


def _plan(mission, planner_lane_id, subtasks):
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane_id,
        subtasks=tuple(subtasks),
    )


def _work_plan(mission):
    return _plan(
        mission,
        PLANNER_LANE.lane_id,
        (
            Subtask(subtask_id="t1", objective="check facts", lane_id="specialist-a", agent_id="s-a-1"),
            Subtask(subtask_id="t2", objective="review risk", lane_id="specialist-b", agent_id="s-b-1"),
        ),
    )


def _ok_runner():
    return (
        ScriptedRunner()
        .on("s-a-1", "t1", ok_result("s-a-1", "t1", output={"facts": "verified"}))
        .on("s-b-1", "t2", ok_result("s-b-1", "t2", output={"risk": "low"}))
    )


def _engine_with_handlers(runner, store=None, config=None):
    reg = NamespaceRegistry()
    if reg.get("acme") is None:
        reg.create("acme")
    engine = Engine(store=store or InMemoryEventStore(), namespaces=reg)
    register_multiagent_handlers(
        engine, runner, specialist_lanes=SPECIALIST_LANES, config=config
    )
    return engine


def test_fan_out_join_runs_as_durable_engine_steps():
    mission = Mission(mission_id="m-core", objective="assess launch")
    plan = _work_plan(mission)
    engine = _engine_with_handlers(_ok_runner())
    assert engine.has_handler(HANDLER_FAN_OUT)
    assert engine.has_handler(HANDLER_JOIN)

    execution = engine.run_workflow("acme", fan_join_workflow("ma-core", plan))

    assert execution.status is WorkflowStatus.SUCCEEDED
    fan_output = execution.state_for("fan").output
    assert len(fan_output["report"]["items"]) == 2
    join_output = execution.state_for("join").output
    assert join_output["succeeded"] == 2
    assert join_output["failed"] == 0
    # the planner-style join summary is persisted in the transcript
    summary = core_seam.aggregate_report_from_join_output(join_output)
    assert summary["succeeded"] == 2


def test_fan_out_join_survives_interruption_and_resumes(tmp_path):
    """A JOIN aggregates from the durable transcript after a crash + resume."""
    mission = Mission(mission_id="m-resume", objective="assess launch")
    plan = _work_plan(mission)
    runner = _ok_runner()
    log_path = tmp_path / "wf.jsonl"
    store1 = FileJsonlEventStore(str(log_path))
    engine1 = _engine_with_handlers(runner, store=store1)
    spec = fan_join_workflow("ma-resume", plan)
    execution = engine1.start_workflow("acme", spec)
    engine1.advance(execution, max_handlers=1)  # only the FAN_OUT step runs

    # "process crash": a fresh engine + fresh registry over the same JSONL log
    store2 = FileJsonlEventStore(str(log_path))
    engine2 = _engine_with_handlers(runner, store=store2)
    resumed = engine2.resume("acme", execution.workflow_id)
    engine2.advance(resumed)

    assert resumed.status is WorkflowStatus.SUCCEEDED
    # the completed FAN_OUT step was never re-executed after resume (only the
    # join handler ran, and it calls no agents)...
    assert runner.calls == [("s-a-1", "t1"), ("s-b-1", "t2")]
    assert resumed.state_for("fan").status.value == "succeeded"
    # ...and the JOIN step rebuilt its aggregation from the persisted output
    join_output = resumed.state_for("join").output
    assert join_output["succeeded"] == 2
    assert join_output["failed"] == 0


def test_fan_out_workflow_fails_when_plan_exceeds_bounded_fan_out():
    """A FAN_OUT over the bound fails the workflow closed (no silent trim)."""
    mission = Mission(mission_id="m-over", objective="assess launch")
    plan = _plan(
        mission,
        PLANNER_LANE.lane_id,
        (
            Subtask(subtask_id="t1", objective="a", lane_id="specialist-a", agent_id="s-a-1"),
            Subtask(subtask_id="t2", objective="b", lane_id="specialist-b", agent_id="s-b-1"),
            Subtask(subtask_id="t3", objective="c", lane_id="specialist-c", agent_id="s-c-1"),
        ),
    )
    runner = ScriptedRunner().on_all(ok_result("s-a-1", "*", output="ok"))
    engine = _engine_with_handlers(
        runner, config=HierarchyConfig(max_fan_out=2)
    )
    execution = engine.run_workflow("acme", fan_join_workflow("ma-over", plan))
    assert execution.status is WorkflowStatus.FAILED
    assert execution.state_for("fan").status.value == "failed"


def test_required_specialist_failure_is_recorded_in_join():
    """A failing required specialist shows up in the join's failure counts."""
    mission = Mission(mission_id="m-fail", objective="assess launch")
    plan = _plan(
        mission,
        PLANNER_LANE.lane_id,
        (Subtask(subtask_id="t1", objective="a", lane_id="specialist-a", agent_id="s-a-1"),),
    )
    runner = ScriptedRunner().on(
        "s-a-1", "t1", fail_result("s-a-1", "t1", error="no data")
    )
    engine = _engine_with_handlers(runner)
    execution = engine.run_workflow("acme", fan_join_workflow("ma-fail", plan))

    assert execution.status is WorkflowStatus.SUCCEEDED  # allSettled: join still runs
    join_output = execution.state_for("join").output
    assert join_output["succeeded"] == 0
    assert join_output["failed"] == 1
    assert join_output["failures"] == ["t1"]
