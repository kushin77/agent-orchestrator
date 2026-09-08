"""Top-level orchestrator composition: hierarchy + optional consensus verdict."""

from __future__ import annotations

import pytest

from engine.multiagent.model import (
    ConsensusOutcome,
    ConsensusRequest,
    FanOutPlan,
    Mission,
    Subtask,
    ok_result,
)
from engine.multiagent.orchestrator import MultiAgentOrchestrator
from engine.multiagent.planner import static_decomposer
from engine.multiagent.runner import ScriptedRunner


def _plan(mission, planner_lane_id, subtasks):
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane_id,
        subtasks=tuple(subtasks),
    )


def _work_plan(mission, planner_lane_id):
    return _plan(
        mission,
        planner_lane_id,
        (
            Subtask(subtask_id="t1", objective="check facts", lane_id="specialist-a"),
            Subtask(subtask_id="t2", objective="review risk", lane_id="specialist-b"),
        ),
    )


def _full_runner():
    """Scripts specialist work + a 3-approve ballot."""
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", ok_result("s-a-1", "t1", output={"facts": "ok"}))
        .on("s-b-1", "t2", ok_result("s-b-1", "t2", output={"risk": "low"}))
        .on("v-a-1", "r1:vote:voter-a", ok_result("v-a-1", "r1:vote:voter-a", output={"choice": "approve"}))
        .on("v-b-1", "r1:vote:voter-b", ok_result("v-b-1", "r1:vote:voter-b", output={"choice": "approve"}))
        .on("v-c-1", "r1:vote:voter-c", ok_result("v-c-1", "r1:vote:voter-c", output={"choice": "approve"}))
    )
    return runner


def test_orchestrator_runs_hierarchy_and_folds_in_consensus(
    mission, planner_lane, specialist_lanes, voter_lanes
):
    orch = MultiAgentOrchestrator(
        _full_runner(),
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        voter_lanes=voter_lanes,
    )
    request = ConsensusRequest(
        request_id="r1",
        proposal="approve launch",
        voters=tuple(lane.lane_id for lane in voter_lanes),
    )
    report = orch.run(
        mission, static_decomposer(_work_plan(mission, planner_lane.lane_id)), request
    )

    assert report.hierarchy.escalation_count == 0
    assert len(report.hierarchy.aggregate.findings) == 2
    assert report.consensus is not None
    assert report.consensus.outcome is ConsensusOutcome.PASSED
    # whole report is a JSON-safe transcript
    data = report.as_dict()
    assert data["consensus"]["outcome"] == "passed"
    assert data["hierarchy"]["escalation_count"] == 0


def test_orchestrator_consensus_verdict_can_block(mission, planner_lane, specialist_lanes, voter_lanes):
    """A rejecting voter pool produces a defined REJECTED verdict, never a pass."""
    runner = (
        ScriptedRunner()
        .on("s-a-1", "t1", ok_result("s-a-1", "t1", output={"facts": "ok"}))
        .on("v-a-1", "r1:vote:voter-a", ok_result("v-a-1", "r1:vote:voter-a", output={"choice": "reject"}))
        .on("v-b-1", "r1:vote:voter-b", ok_result("v-b-1", "r1:vote:voter-b", output={"choice": "reject"}))
    )
    # only voter-a and voter-b are registered; voter-c omitted from the ballot
    request = ConsensusRequest(
        request_id="r1", proposal="approve launch", voters=("voter-a", "voter-b")
    )
    orch = MultiAgentOrchestrator(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        voter_lanes=(voter_lanes[0], voter_lanes[1]),
    )
    report = orch.run(mission, static_decomposer(_plan(mission, planner_lane.lane_id, ())), request)
    assert report.consensus.outcome is ConsensusOutcome.REJECTED


def test_consensus_request_without_voter_lanes_fails_closed(
    mission, planner_lane, specialist_lanes
):
    orch = MultiAgentOrchestrator(
        ScriptedRunner().on_all(ok_result("x", "y", output=None)),
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        voter_lanes=(),
    )
    request = ConsensusRequest(request_id="r1", proposal="approve launch", voters=())
    with pytest.raises(ValueError, match="voter_lanes"):
        orch.run(mission, static_decomposer(_plan(mission, planner_lane.lane_id, ())), request)
