"""Escalation tests: required failures re-plan back to the planner, bounded."""

from __future__ import annotations

import pytest

from engine.multiagent.model import (
    FanOutPlan,
    HierarchyConfig,
    ResultStatus,
    Subtask,
    fail_result,
    ok_result,
)
from engine.multiagent.planner import HierarchicalPlanner
from engine.multiagent.runner import ScriptedRunner


def _plan(mission, planner_lane_id, subtasks):
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane_id,
        subtasks=tuple(subtasks),
    )


def _escalating_decomposer(round0_subtask, fallback_subtask):
    """Round 0 runs ``round0_subtask``; the planner then re-plans to the
    fallback (a classic escalation: try specialist-a, then specialist-b)."""

    def _decompose(ctx):
        if ctx.round == 0 or ctx.prior_report is None:
            return _plan(ctx.mission, "planner", (round0_subtask,))
        return _plan(ctx.mission, "planner", (fallback_subtask,))

    return _decompose


def test_escalation_replans_failed_required_subtask_to_another_lane(
    mission, planner_lane, specialist_lanes
):
    """specialist-a fails t1; the planner escalates and specialist-b resolves it."""
    t1_on_a = Subtask(
        subtask_id="t1", objective="verify claim", lane_id="specialist-a",
        agent_id="s-a-1", required=True,
    )
    t1_on_b = Subtask(
        subtask_id="t1", objective="verify claim", lane_id="specialist-b",
        agent_id="s-b-1", required=True,
    )
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", fail_result("s-a-1", "t1", error="no data access"))
        .on("s-b-1", "t1", ok_result("s-b-1", "t1", output={"verified": True}))
    )
    planner = HierarchicalPlanner(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_escalation_rounds=2),
    )
    report = planner.run(
        mission, _escalating_decomposer(t1_on_a, t1_on_b)
    )

    assert report.escalation_count == 1
    assert len(report.reports) == 2
    assert report.aggregate.escalated == ()
    assert report.aggregate.failures == ()
    assert len(report.aggregate.findings) == 1
    # the resolved finding came from specialist-b (the escalation target)
    assert report.aggregate.findings[0]["agent_id"] == "s-b-1"
    # escalation is visible in the transcript
    assert report.as_dict()["escalation_count"] == 1


def test_escalation_budget_is_bounded_and_never_silently_passes(
    mission, planner_lane, specialist_lanes
):
    """A stubborn required failure exhausts the budget and is reported, never
    silently dropped or passed."""
    failing = Subtask(
        subtask_id="t1", objective="verify claim", lane_id="specialist-a",
        agent_id="s-a-1", required=True,
    )
    runner = ScriptedRunner().on(
        "s-a-1", "t1", fail_result("s-a-1", "t1", error="always fails")
    )
    planner = HierarchicalPlanner(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_escalation_rounds=2),
    )
    report = planner.run(mission, _escalating_decomposer(failing, failing))

    # initial dispatch + 2 re-plans = 3 rounds, then the budget stops the loop
    assert report.escalation_count == 2
    assert len(report.reports) == 3
    assert report.aggregate.failures == ("t1",)
    assert report.aggregate.escalated == ("t1",)
    assert report.aggregate.findings == ()
    assert report.as_dict()["aggregate"]["escalated"] == ["t1"]


def test_escalation_on_low_confidence_required_subtask(
    mission, planner_lane, specialist_lanes
):
    """A 'succeeded' specialist below the confidence bar is escalated too."""
    t_low = Subtask(
        subtask_id="t1", objective="verify claim", lane_id="specialist-a",
        agent_id="s-a-1", required=True,
    )
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", ok_result("s-a-1", "t1", output="guess", confidence=0.4))
    )
    planner = HierarchicalPlanner(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(require_confidence=0.9, max_escalation_rounds=1),
    )

    def _decompose(ctx):
        return _plan(ctx.mission, "planner", (t_low,))

    report = planner.run(mission, _decompose)

    assert report.escalation_count == 1
    # honest outcome: the low-confidence required result is not a clean finding
    assert report.aggregate.escalated == ("t1",)
    assert report.aggregate.failures == ()
    assert report.aggregate.findings == ()


def test_required_failure_context_is_visible_to_decomposer(
    mission, planner_lane, specialist_lanes
):
    """The escalation context tells the planner which subtasks failed."""
    t1 = Subtask(
        subtask_id="t1", objective="a", lane_id="specialist-a", agent_id="s-a-1"
    )
    seen = []

    def _decompose(ctx):
        seen.append((ctx.round, tuple(ctx.required_failures)))
        return _plan(ctx.mission, "planner", (t1,))

    runner = ScriptedRunner().on(
        "s-a-1", "t1", fail_result("s-a-1", "t1", error="nope")
    )
    planner = HierarchicalPlanner(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_escalation_rounds=1),
    )
    planner.run(mission, _decompose)
    assert seen[0] == (0, ())
    assert (1, ("t1",)) in seen
