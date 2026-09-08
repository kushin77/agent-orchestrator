"""Model + lane vocabulary tests: disjoint-worker guarantees, plan (de)serialization."""

from __future__ import annotations

import pytest

from engine.multiagent.model import (
    AgentResult,
    EscalationContext,
    FanOutPlan,
    Lane,
    LaneRole,
    Mission,
    ResultStatus,
    Subtask,
    ThresholdRule,
    fan_out_plan_from_dict,
    fan_out_plan_to_dict,
    ok_result,
    validate_lanes,
)


def _specialist(lane_id: str, agents=("a1", "a2")):
    return Lane(lane_id=lane_id, role=LaneRole.SPECIALIST, agent_ids=tuple(agents))


class TestLaneGuarantees:
    def test_lanes_disjoint_by_default(self):
        lanes = (_specialist("x", ("alice",)), _specialist("y", ("bob",)))
        validate_lanes(lanes)  # must not raise

    def test_shared_agent_across_lanes_rejected(self):
        lanes = (_specialist("x", ("alice", "carol")), _specialist("y", ("bob", "carol")))
        with pytest.raises(ValueError, match="shared by lanes"):
            validate_lanes(lanes)

    def test_duplicate_lane_id_rejected(self):
        lanes = (_specialist("dup"), _specialist("dup"))
        with pytest.raises(ValueError, match="duplicate lane id"):
            validate_lanes(lanes)

    def test_empty_lane_id_rejected(self):
        with pytest.raises(ValueError, match="lane_id"):
            validate_lanes([Lane(lane_id="", role=LaneRole.PLANNER)])

    def test_implicit_agent_defaults_to_lane_id(self):
        lane = Lane(lane_id="worker-1", role=LaneRole.SPECIALIST)
        assert lane.agents() == ("worker-1",)


class TestValueObjects:
    def test_subtask_requires_lane(self):
        with pytest.raises(ValueError, match="assigned to a lane"):
            Subtask(subtask_id="t1", objective="x", lane_id="")

    def test_agent_result_confidence_bounds(self):
        with pytest.raises(ValueError, match="confidence"):
            ok_result("a", "t", confidence=1.5)

    def test_plan_round_trip_json_safe(self):
        mission = Mission(mission_id="m1", objective="plan")
        subtask = Subtask(
            subtask_id="t1",
            objective="do",
            lane_id="specialist-a",
            agent_id="s-a-1",
            required=True,
        )
        plan = FanOutPlan(
            mission_id=mission.mission_id,
            planner_lane_id="planner",
            subtasks=(subtask,),
        )
        restored = fan_out_plan_from_dict(fan_out_plan_to_dict(plan))
        assert restored.mission_id == "m1"
        assert restored.planner_lane_id == "planner"
        assert restored.subtasks[0].subtask_id == "t1"
        assert restored.subtasks[0].agent_id == "s-a-1"

    def test_escalation_context_reports_required_failures(self):
        mission = Mission(mission_id="m1", objective="x")
        ok_item = _item("good", ok=True)
        bad_required = _item("bad-req", ok=False, required=True)
        bad_optional = _item("bad-opt", ok=False, required=False)
        from engine.multiagent.model import FanOutReport

        prior = FanOutReport(
            plan=FanOutPlan(mission_id="m1", planner_lane_id="planner"),
            items=(ok_item, bad_required, bad_optional),
        )
        ctx = EscalationContext(mission=mission, round=1, prior_report=prior)
        assert ctx.required_failures == ("bad-req",)

    def test_threshold_rule_shares(self):
        assert ThresholdRule.MAJORITY.required_share == 0.5
        assert ThresholdRule.SUPERMAJORITY.required_share == pytest.approx(2 / 3)
        assert ThresholdRule.UNANIMOUS.required_share == 1.0


def _item(subtask_id: str, *, ok: bool, required: bool = True):
    subtask = Subtask(
        subtask_id=subtask_id,
        objective="o",
        lane_id="specialist-a",
        required=required,
    )
    result = AgentResult(
        agent_id="s-a-1",
        task_id=subtask_id,
        status=ResultStatus.SUCCEEDED if ok else ResultStatus.FAILED,
        error="" if ok else "boom",
    )
    from engine.multiagent.model import FanOutItem

    return FanOutItem(subtask=subtask, result=result)
