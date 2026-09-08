"""engine.multiagent — bounded fan-out dispatcher (hierarchical dispatch).

Dispatches a planner-produced :class:`FanOutPlan` to specialist lanes through
the injected runner seam with *allSettled* semantics: every subtask runs, a
subtask failure never aborts the round (gov-ai-scout ``allSettled`` subtask
pipeline pattern), and each round is bounded by ``max_fan_out`` — a plan that
exceeds the bound is refused (``FanOutLimitError``), never silently trimmed.
Disjoint-work validation enforces the model-level guarantee that work never
collides across lanes (leaderboard fanout doctrine).
"""

from __future__ import annotations

from typing import Optional, Sequence

from .model import (
    AgentResult,
    AgentTask,
    FanOutItem,
    FanOutPlan,
    FanOutReport,
    Lane,
    ResultStatus,
    Subtask,
    validate_lanes,
)
from .runner import coerce_agent_result


class FanOutError(RuntimeError):
    """Base error for fan-out dispatch."""


class FanOutLimitError(FanOutError):
    """A plan exceeds the configured bounded fan-out cap."""


class PlanValidationError(FanOutError):
    """A plan violates the disjoint-work / lane-assignment guarantees."""


def validate_plan(plan: FanOutPlan, specialist_lanes: Sequence[Lane]) -> None:
    """Fail closed when a plan is not a safe disjoint dispatch.

    Every subtask must be assigned to a known specialist lane and an agent
    that lane owns; the planner lane never executes its own subtasks (the
    planner coordinates and aggregates — it does not do the specialist work).
    """
    by_id = {lane.lane_id: lane for lane in specialist_lanes}
    for subtask in plan.subtasks:
        if subtask.lane_id == plan.planner_lane_id:
            raise PlanValidationError(
                f"subtask {subtask.subtask_id!r} assigned to the planner lane "
                f"{plan.planner_lane_id!r}; planner lanes do not self-execute"
            )
        lane = by_id.get(subtask.lane_id)
        if lane is None:
            raise PlanValidationError(
                f"subtask {subtask.subtask_id!r} assigned to unknown lane "
                f"{subtask.lane_id!r}"
            )
        agents = lane.agents()
        if subtask.agent_id and subtask.agent_id not in agents:
            raise PlanValidationError(
                f"subtask {subtask.subtask_id!r} agent {subtask.agent_id!r} "
                f"is not owned by lane {subtask.lane_id!r}"
            )


def _resolve_agent(subtask: Subtask, specialist_lanes: Sequence[Lane]) -> str:
    """Pin a subtask to its lane's agent when the plan left it implicit."""
    if subtask.agent_id:
        return subtask.agent_id
    for lane in specialist_lanes:
        if lane.lane_id == subtask.lane_id:
            return lane.agents()[0]
    raise PlanValidationError(f"no lane {subtask.lane_id!r} to resolve agent")


class FanOutDispatcher:
    """Bounded, allSettled fan-out of one plan through an injected runner."""

    def __init__(
        self,
        runner: object,
        *,
        max_fan_out: int = 16,
        specialist_lanes: Optional[Sequence[Lane]] = None,
    ) -> None:
        if not callable(getattr(runner, "run_agent", None)):
            raise TypeError("runner must expose run_agent(agent_id, task)")
        if max_fan_out < 1:
            raise ValueError("max_fan_out must be >= 1")
        self._runner = runner
        self._max_fan_out = int(max_fan_out)
        self._specialist_lanes = tuple(specialist_lanes or ())

    def dispatch(self, plan: FanOutPlan) -> FanOutReport:
        if len(plan.subtasks) > self._max_fan_out:
            raise FanOutLimitError(
                f"plan for mission {plan.mission_id!r} has "
                f"{len(plan.subtasks)} subtasks; bounded fan-out cap is "
                f"{self._max_fan_out}"
            )
        if self._specialist_lanes:
            validate_lanes(self._specialist_lanes)
            validate_plan(plan, self._specialist_lanes)
        items = tuple(self._dispatch_one(plan, subtask) for subtask in plan.subtasks)
        return FanOutReport(plan=plan, items=items)

    def _dispatch_one(self, plan: FanOutPlan, subtask: Subtask) -> FanOutItem:
        agent_id = _resolve_agent(subtask, self._specialist_lanes)
        task = AgentTask(
            task_id=subtask.subtask_id,
            objective=subtask.objective,
            lane_id=subtask.lane_id,
            agent_id=agent_id,
            context=subtask.context,
        )
        try:
            raw = self._runner.run_agent(agent_id, task)
        except Exception as exc:  # an agent crash is a subtask failure (allSettled)
            result = AgentResult(
                agent_id=agent_id,
                task_id=subtask.subtask_id,
                status=ResultStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                confidence=0.0,
            )
            return FanOutItem(subtask=subtask, result=result)
        result = coerce_agent_result(raw, agent_id, subtask.subtask_id)
        return FanOutItem(subtask=subtask, result=result)
