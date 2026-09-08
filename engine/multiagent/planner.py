"""engine.multiagent — hierarchical orchestration (planner lane -> specialists).

The hierarchical pattern from the issue: a *planner/lead lane* decomposes a
mission into specialist subtasks, fans them out to *specialist lanes* with
bounded, allSettled semantics, and aggregates their results.  When a
*required* specialist subtask fails (or lands below the confidence bar), the
work escalates back to the planner for re-planning — bounded by
``max_escalation_rounds`` so the mission always terminates.  Decomposition is
an injected callable (``decomposer``) so tests are fully deterministic; in
production the decomposer is typically itself an agent loop exposed through
the runner seam (``engine/loop``, issue #23 — see ``loop_seam.py``).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .fanout import FanOutDispatcher, validate_plan
from .model import (
    AggregationReport,
    AgentResult,
    EscalationContext,  # noqa: F401  (re-exported for callers)
    FanOutItem,
    FanOutPlan,
    FanOutReport,
    HierarchyConfig,
    HierarchyReport,
    Lane,
    LaneRole,
    Mission,
    ResultStatus,
    Subtask,
    validate_lanes,
)


class HierarchicalPlanner:
    """Deterministic hierarchical orchestrator over the injected runner.

    ``decomposer`` receives an :class:`EscalationContext` (carrying the
    mission and, from the second round on, the prior plan/report so it can
    re-plan around failures) and returns a :class:`FanOutPlan`.
    """

    def __init__(
        self,
        runner: object,
        *,
        planner_lane: Lane,
        specialist_lanes: Sequence[Lane],
        config: Optional[HierarchyConfig] = None,
    ) -> None:
        if planner_lane.role is not LaneRole.PLANNER:
            raise ValueError("planner_lane must have role PLANNER")
        specialist = list(specialist_lanes)
        if not specialist:
            raise ValueError("at least one specialist lane is required")
        for lane in specialist:
            if lane.role is not LaneRole.SPECIALIST:
                raise ValueError(f"lane {lane.lane_id!r} must have role SPECIALIST")
        all_lanes = [planner_lane, *specialist]
        validate_lanes(all_lanes)
        self._planner_lane = planner_lane
        self._specialist_lanes = tuple(specialist)
        self._config = config or HierarchyConfig()
        self._dispatcher = FanOutDispatcher(
            runner,
            max_fan_out=self._config.max_fan_out,
            specialist_lanes=self._specialist_lanes,
        )

    @property
    def planner_lane(self) -> Lane:
        return self._planner_lane

    @property
    def config(self) -> HierarchyConfig:
        return self._config

    def run(
        self,
        mission: Mission,
        decomposer: Callable[[EscalationContext], FanOutPlan],
    ) -> HierarchyReport:
        """Run the bounded hierarchical mission to a terminal aggregation."""
        plans: List[FanOutPlan] = []
        reports: List[FanOutReport] = []
        round_no = 0
        while True:
            ctx = EscalationContext(
                mission=mission,
                round=round_no,
                prior_plan=plans[-1] if plans else None,
                prior_report=reports[-1] if reports else None,
            )
            plan = decomposer(ctx)
            validate_plan(plan, self._specialist_lanes)
            report = self._dispatcher.dispatch(plan)
            plans.append(plan)
            reports.append(report)
            if not report.unacceptable(self._config.require_confidence):
                break
            if round_no >= self._config.max_escalation_rounds:
                break
            round_no += 1
        aggregate = self._aggregate(mission, reports[-1])
        return HierarchyReport(
            mission=mission,
            planner_lane_id=self._planner_lane.lane_id,
            plans=tuple(plans),
            reports=tuple(reports),
            escalation_count=round_no,
            aggregate=aggregate,
        )

    # -- planner aggregation -------------------------------------------------

    def _aggregate(self, mission: Mission, report: FanOutReport) -> AggregationReport:
        findings: List[Dict] = []
        failures: List[str] = []
        escalated: List[str] = []
        bar = self._config.require_confidence
        for item in report.items:
            result = item.result
            acceptable = item.ok and result.confidence >= bar
            if acceptable:
                findings.append(
                    {
                        "subtask_id": item.subtask.subtask_id,
                        "lane_id": item.subtask.lane_id,
                        "agent_id": result.agent_id,
                        "confidence": result.confidence,
                        "output": result.output,
                    }
                )
            else:
                if not item.ok:
                    failures.append(item.subtask.subtask_id)
                if item.subtask.required:
                    escalated.append(item.subtask.subtask_id)
        summary = (
            f"mission {mission.mission_id}: {len(findings)} subtask(s) "
            f"succeeded, {len(failures)} failed, {len(escalated)} required "
            "subtask(s) escalated unresolved"
        )
        return AggregationReport(
            planner_lane_id=self._planner_lane.lane_id,
            summary=summary,
            findings=tuple(findings),
            failures=tuple(failures),
            escalated=tuple(escalated),
        )


# ---------------------------------------------------------------------------
# Convenience decomposers
# ---------------------------------------------------------------------------


def static_decomposer(plan: FanOutPlan) -> Callable[[EscalationContext], FanOutPlan]:
    """Wrap a fixed plan as a decomposer (single-round deterministic runs)."""

    def _decompose(_ctx: EscalationContext) -> FanOutPlan:
        return plan

    return _decompose


def subtask_ok(item: FanOutItem) -> bool:
    """Convenience predicate used by escalation tests."""
    return item.ok


def make_subtask(
    subtask_id: str,
    objective: str,
    lane_id: str,
    *,
    required: bool = True,
    agent_id: str = "",
    context: Optional[Dict] = None,
) -> Subtask:
    return Subtask(
        subtask_id=subtask_id,
        objective=objective,
        lane_id=lane_id,
        agent_id=agent_id,
        required=required,
        context=dict(context or {}),
    )
