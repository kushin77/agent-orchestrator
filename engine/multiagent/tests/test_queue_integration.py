"""Integration with engine/queue: fan-out tasks are enqueued and claimed.

A planner plan fans out through the real queue lifecycle — enqueue -> claim
(lease) -> start -> run -> ack/fail — via a queue Worker whose task handler
runs each subtask through the injected runner.  A subtask whose agent fails
is never recorded SUCCEEDED (never-a-false-PASS doctrine); the report
reflects the queue's terminal state.
"""

from __future__ import annotations

import pytest

from engine.queue import JobQueue, QueueConfig
from engine.queue.store import InMemoryStore

from engine.multiagent.fanout import FanOutLimitError
from engine.multiagent.model import (
    FanOutPlan,
    Lane,
    LaneRole,
    Mission,
    Subtask,
    fail_result,
    ok_result,
)
from engine.multiagent.queue_seam import QueueFanOut
from engine.multiagent.runner import ScriptedRunner

PLANNER_LANE = Lane(lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",))


def _plan(mission, planner_lane_id, subtasks):
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane_id,
        subtasks=tuple(subtasks),
    )


def _new_queue(max_attempts=1, per_tenant_max_pending=50):
    return JobQueue(
        store=InMemoryStore(),
        config=QueueConfig(
            max_attempts=max_attempts,
            per_tenant_max_pending=per_tenant_max_pending,
        ),
    )


def _three_subtask_plan(mission):
    return _plan(
        mission,
        PLANNER_LANE.lane_id,
        (
            Subtask(subtask_id="t1", objective="check facts", lane_id="specialist-a", agent_id="s-a-1"),
            Subtask(subtask_id="t2", objective="review risk", lane_id="specialist-b", agent_id="s-b-1"),
            Subtask(subtask_id="t3", objective="draft plan", lane_id="specialist-c", agent_id="s-c-1"),
        ),
    )


def test_queue_fan_out_runs_every_subtask_through_the_lifecycle():
    mission = Mission(mission_id="m-queue-ok", objective="assess launch")
    plan = _three_subtask_plan(mission)
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", ok_result("s-a-1", "t1", output={"facts": "ok"}))
        .on("s-b-1", "t2", ok_result("s-b-1", "t2", output={"risk": "low"}))
        .on("s-c-1", "t3", ok_result("s-c-1", "t3", output={"plan": "go"}))
    )
    queue = _new_queue()
    report = QueueFanOut(queue, runner, worker_agent="ma-1", max_fan_out=8).dispatch(plan)

    assert report.succeeded == 3
    assert report.failed == 0
    assert report.processed == 3
    for subtask in plan.subtasks:
        task = queue.get(subtask.subtask_id)
        assert task is not None and task.status.value == "SUCCEEDED"
    assert queue.depth() == 0  # the tenant's queue drained
    # each task flowed through the audit ledger
    ledger = {e.task_id for e in queue.audit_log()}
    assert {"t1", "t2", "t3"} <= ledger


def test_queue_fan_out_failed_subtask_is_never_recorded_succeeded():
    mission = Mission(mission_id="m-queue-fail", objective="assess launch")
    plan = _three_subtask_plan(mission)
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", ok_result("s-a-1", "t1", output="ok"))
        .on("s-b-1", "t2", fail_result("s-b-1", "t2", error="no data"))
        .on("s-c-1", "t3", ok_result("s-c-1", "t3", output="ok"))
    )
    queue = _new_queue(max_attempts=1)
    report = QueueFanOut(queue, runner, worker_agent="ma-1").dispatch(plan)

    assert report.succeeded == 2
    assert report.failed == 1
    assert report.outcomes[1]["status"] == "failed"
    # never-a-false-PASS: the failed task never reached SUCCEEDED
    t2 = queue.get("t2")
    assert t2 is not None and t2.status.value == "FAILED"
    ledger_states = {e.to_state.value for e in queue.audit_log() if e.task_id == "t2"}
    assert "SUCCEEDED" not in ledger_states


def test_queue_fan_out_is_bounded():
    mission = Mission(mission_id="m-queue-over", objective="assess launch")
    plan = _three_subtask_plan(mission)
    runner = ScriptedRunner().on_all(ok_result("s-a-1", "*", output="ok"))
    queue = _new_queue()
    qf = QueueFanOut(queue, runner, worker_agent="ma-1", max_fan_out=2)
    with pytest.raises(FanOutLimitError, match="bounded fan-out cap is 2"):
        qf.dispatch(plan)
    assert queue.depth() == 0  # nothing was enqueued past the bound
