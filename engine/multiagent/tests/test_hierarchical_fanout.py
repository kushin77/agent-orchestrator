"""Hierarchical fan-out: planner lane -> specialist lanes -> aggregation."""

from __future__ import annotations

from engine.multiagent.fanout import FanOutDispatcher
from engine.multiagent.model import (
    FanOutPlan,
    HierarchyConfig,
    ResultStatus,
    Subtask,
    ok_result,
    fail_result,
)
from engine.multiagent.planner import HierarchicalPlanner, static_decomposer
from engine.multiagent.runner import ScriptedRunner


def _plan(mission, planner_lane_id, subtasks):
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane_id,
        subtasks=tuple(subtasks),
    )


def _three_subtask_plan(mission, planner_lane_id):
    subtasks = (
        Subtask(subtask_id="t1", objective="check facts", lane_id="specialist-a"),
        Subtask(subtask_id="t2", objective="review risk", lane_id="specialist-b"),
        Subtask(subtask_id="t3", objective="draft plan", lane_id="specialist-c"),
    )
    return _plan(mission, planner_lane_id, subtasks)


def _make_planner(runner, specialist_lanes, planner_lane, **cfg):
    return HierarchicalPlanner(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(**cfg),
    )


def test_planner_fans_out_to_specialist_lanes_and_aggregates(
    mission, planner_lane, specialist_lanes
):
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", ok_result("s-a-1", "t1", output={"facts": 3}))
        .on("s-b-1", "t2", ok_result("s-b-1", "t2", output={"risk": "medium"}))
        .on("s-c-1", "t3", ok_result("s-c-1", "t3", output={"plan": "go"}))
    )
    planner = _make_planner(runner, specialist_lanes, planner_lane)
    plan = _three_subtask_plan(mission, planner_lane.lane_id)
    report = planner.run(mission, static_decomposer(plan))

    assert report.escalation_count == 0
    assert len(report.reports) == 1
    final = report.final_report
    assert final is not None and len(final.succeeded) == 3
    # each subtask ran on its own disjoint specialist lane/agent (model disjointness)
    assert runner.calls == [("s-a-1", "t1"), ("s-b-1", "t2"), ("s-c-1", "t3")]
    # the planner aggregated the specialist findings
    aggregate = report.aggregate
    assert aggregate is not None
    assert len(aggregate.findings) == 3
    assert aggregate.failures == ()
    assert aggregate.escalated == ()
    lanes = {f["lane_id"] for f in aggregate.findings}
    assert lanes == {"specialist-a", "specialist-b", "specialist-c"}
    # JSON-safe transcript shape
    assert "escalation_count" in report.as_dict()


def test_optional_subtask_failure_is_recorded_not_escalated(
    mission, planner_lane, specialist_lanes
):
    """A non-required subtask failure is tolerated (recorded, no escalation)."""
    subtasks = (
        Subtask(
            subtask_id="t1", objective="check facts", lane_id="specialist-a",
            required=False,
        ),
        Subtask(subtask_id="t2", objective="review risk", lane_id="specialist-b"),
    )
    plan = _plan(mission, planner_lane.lane_id, subtasks)
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", fail_result("s-a-1", "t1", error="sources unavailable"))
        .on("s-b-1", "t2", ok_result("s-b-1", "t2", output={"risk": "low"}))
    )
    report = _make_planner(runner, specialist_lanes, planner_lane).run(
        mission, static_decomposer(plan)
    )

    assert report.escalation_count == 0
    assert report.aggregate.failures == ("t1",)
    assert report.aggregate.escalated == ()
    assert len(report.final_report.succeeded) == 1


def test_fan_out_report_all_settled_never_aborts(mission, planner_lane, specialist_lanes):
    """allSettled: a crashing specialist does not abort the other subtasks."""
    subtasks = (
        Subtask(
            subtask_id="t1", objective="check facts", lane_id="specialist-a",
            agent_id="s-a-1",
        ),
        Subtask(
            subtask_id="t2", objective="review risk", lane_id="specialist-b",
            agent_id="s-b-1",
        ),
    )
    plan = _plan(mission, planner_lane.lane_id, subtasks)

    def _crash(_agent_id, _task):
        raise RuntimeError("agent process died")

    runner = ScriptedRunner(default=_crash)
    dispatcher = FanOutDispatcher(runner, max_fan_out=4)
    report = dispatcher.dispatch(plan)

    assert len(report.items) == 2
    assert all(item.result.status is ResultStatus.FAILED for item in report.items)
    assert "agent process died" in report.failed[0].result.error
