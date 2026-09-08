"""engine.multiagent — top-level multi-agent orchestrator.

Composes the two patterns from the issue into one deterministic run:

* *hierarchical* — the planner lane decomposes the mission, fans it out to
  specialist lanes (bounded, allSettled) and aggregates, escalating required
  failures back to the planner within a bound; and
* *peer* — an optional consensus/voting ballot over voter lanes whose
  verdict (PASSED / REJECTED / NO_CONSENSUS) is folded into the report.

Everything runs through the injected runner seam
(``run_agent(agent_id, task) -> AgentResult``) so a scripted runner makes the
whole composition deterministic and offline-testable.
"""

from __future__ import annotations

from typing import Callable, Dict, Mapping, Optional, Sequence

from .consensus import ConsensusConfig, ConsensusRequest, run_consensus
from .model import (
    ConsensusResult,
    EscalationContext,
    FanOutPlan,
    HierarchyConfig,
    HierarchyReport,
    Lane,
    LaneRole,
    Mission,
    OrchestrationReport,
    validate_lanes,
)
from .planner import HierarchicalPlanner


class MultiAgentOrchestrator:
    """Deterministic multi-agent run: hierarchical delegation + optional vote."""

    def __init__(
        self,
        runner: object,
        *,
        planner_lane: Lane,
        specialist_lanes: Sequence[Lane],
        voter_lanes: Sequence[Lane] = (),
        config: Optional[HierarchyConfig] = None,
        consensus_config: Optional[ConsensusConfig] = None,
        voter_weights: Optional[Mapping[str, float]] = None,
    ) -> None:
        voter = list(voter_lanes)
        for lane in voter:
            if lane.role is not LaneRole.VOTER:
                raise ValueError(f"voter lane {lane.lane_id!r} must have role VOTER")
        if voter:
            validate_lanes([planner_lane, *specialist_lanes, *voter])
        self._runner = runner
        self._voter_lanes = tuple(voter)
        self._consensus_config = consensus_config or ConsensusConfig()
        self._voter_weights = dict(voter_weights or {})
        self._planner = HierarchicalPlanner(
            runner,
            planner_lane=planner_lane,
            specialist_lanes=specialist_lanes,
            config=config,
        )

    @property
    def planner(self) -> HierarchicalPlanner:
        return self._planner

    def run(
        self,
        mission: Mission,
        decomposer: Callable[[EscalationContext], FanOutPlan],
        consensus_request: Optional[ConsensusRequest] = None,
    ) -> OrchestrationReport:
        """Run the mission; when a ballot is requested, fold in its verdict."""
        hierarchy = self._planner.run(mission, decomposer)
        consensus: Optional[ConsensusResult] = None
        if consensus_request is not None:
            if not self._voter_lanes:
                raise ValueError(
                    "consensus_request requires voter_lanes on the orchestrator"
                )
            consensus = run_consensus(
                consensus_request,
                self._voter_lanes,
                self._runner,
                config=self._consensus_config,
                weights=self._voter_weights,
            )
        return OrchestrationReport(
            mission=mission, hierarchy=hierarchy, consensus=consensus
        )
