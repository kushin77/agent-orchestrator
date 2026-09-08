"""Bounded fan-out tests: hard caps are enforced, never silently trimmed."""

from __future__ import annotations

import pytest

from engine.multiagent.fanout import (
    FanOutDispatcher,
    FanOutLimitError,
    PlanValidationError,
    validate_plan,
)
from engine.multiagent.model import (
    FanOutPlan,
    HierarchyConfig,
    Subtask,
    ok_result,
)
from engine.multiagent.planner import HierarchicalPlanner, static_decomposer
from engine.multiagent.runner import ScriptedRunner


def _plan(mission, planner_lane_id, subtasks):
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane_id,
        subtasks=tuple(subtasks),
    )


def _n_subtasks(n, lane_id="specialist-a", agent_id="s-a-1"):
    return tuple(
        Subtask(
            subtask_id=f"t{i}",
            objective=f"task {i}",
            lane_id=lane_id,
            agent_id=agent_id,
        )
        for i in range(n)
    )


def test_dispatcher_refuses_plan_over_the_cap(mission, planner_lane, specialist_lanes):
    runner = ScriptedRunner().on_all(ok_result("s-a-1", "*", output="ok"))
    dispatcher = FanOutDispatcher(
        runner, max_fan_out=2, specialist_lanes=specialist_lanes
    )
    plan = _plan(mission, planner_lane.lane_id, _n_subtasks(3))
    with pytest.raises(FanOutLimitError, match="bounded fan-out cap is 2"):
        dispatcher.dispatch(plan)


def test_dispatcher_allows_exactly_at_the_cap(mission, planner_lane, specialist_lanes):
    runner = ScriptedRunner().on_all(ok_result("s-a-1", "*", output="ok"))
    dispatcher = FanOutDispatcher(
        runner, max_fan_out=2, specialist_lanes=specialist_lanes
    )
    plan = _plan(mission, planner_lane.lane_id, _n_subtasks(2))
    report = dispatcher.dispatch(plan)
    assert len(report.succeeded) == 2


def test_hierarchical_planner_bounds_the_decomposer(
    mission, planner_lane, specialist_lanes
):
    """A decomposer that over-plans is refused by the bounded planner."""
    runner = ScriptedRunner().on_all(ok_result("s-a-1", "*", output="ok"))
    planner = HierarchicalPlanner(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_fan_out=1),
    )
    plan = _plan(mission, planner_lane.lane_id, _n_subtasks(2))
    with pytest.raises(FanOutLimitError):
        planner.run(mission, static_decomposer(plan))


def test_validate_plan_rejects_planner_lane_self_execution(mission, planner_lane):
    runner = ScriptedRunner().on_all(ok_result("planner-1", "*", output="ok"))
    dispatcher = FanOutDispatcher(
        runner, max_fan_out=4, specialist_lanes=(planner_lane,)
    )
    plan = FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane.lane_id,
        subtasks=(Subtask(subtask_id="t1", objective="x", lane_id="planner"),),
    )
    with pytest.raises(PlanValidationError, match="do not self-execute"):
        dispatcher.dispatch(plan)


def test_validate_plan_rejects_unknown_lane(mission, planner_lane, specialist_lanes):
    plan = _plan(
        mission,
        planner_lane.lane_id,
        (Subtask(subtask_id="t1", objective="x", lane_id="ghost-lane"),),
    )
    with pytest.raises(PlanValidationError, match="unknown lane"):
        validate_plan(plan, specialist_lanes)


def test_validate_plan_rejects_agent_not_owned_by_lane(mission, planner_lane, specialist_lanes):
    plan = _plan(
        mission,
        planner_lane.lane_id,
        (Subtask(subtask_id="t1", objective="x", lane_id="specialist-a", agent_id="intruder"),),
    )
    with pytest.raises(PlanValidationError, match="not owned by lane"):
        validate_plan(plan, specialist_lanes)
