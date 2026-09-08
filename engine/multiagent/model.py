"""engine.multiagent — multi-agent orchestration domain model (issue #24).

Vocabulary for the state-machine execution pillar's multi-agent
orchestration lane (work item 20, phase 3, EPIC-00 issue #4): hierarchical
lanes (a planner/lead lane delegates to specialist lanes and aggregates) and
peer patterns (bounded subtask fan-out with allSettled semantics and a
consensus/voting protocol with threshold + quorum rules).

Adapted (never verbatim — see ``engine/multiagent/README.md`` and
``docs/CANNIBALIZATION.md`` for provenance) from the fleet sources named in
the issue: ``leaderboard scripts/{platoon,soldier,elite}/*`` and
``dispatch/fanout.sh`` (disjoint-work fan-out, claims, collision avoidance),
``news-feed-engine/services/multi-agent-system`` (majority-vote + weighted
expertise consensus, policy gates) and ``services/ai-personas`` (persona
archetypes mapped onto lanes), and ``capital-underwriting/scripts/agent/*``
(dispatch daemon shapes).  Everything here is plain JSON-safe data so a plan
or report can be embedded in an engine-core workflow event and replayed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


class LaneRole(str, Enum):
    """Closed lane-role vocabulary for hierarchical orchestration.

    A *planner* lane is the lead: it decomposes a mission into subtasks,
    fans them out to *specialist* lanes and aggregates their results.  A
    *voter* lane participates in the peer consensus/voting pattern.  At the
    model level lanes are disjoint worker pools (no two lanes share an
    agent); at runtime the planner fans out to specialists.
    """

    PLANNER = "planner"
    SPECIALIST = "specialist"
    VOTER = "voter"


class ResultStatus(str, Enum):
    """Closed per-run outcome vocabulary for one injected agent call."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskKind(str, Enum):
    """Closed vocabulary for what a delegated task asks an agent to do."""

    WORK = "work"  # a specialist subtask
    VOTE = "vote"  # a consensus ballot
    SYNTHESIS = "synthesis"  # planner aggregation


class VoteChoice(str, Enum):
    """Closed per-voter choice vocabulary (weighted voting)."""

    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


class ThresholdRule(str, Enum):
    """Closed consensus threshold rules (news-feed majority + policy gates).

    Decisions are made on *weighted* votes (majority vote + weighted
    expertise).  ``required_share`` is the approve fraction of active weight
    the rule demands; the per-rule ``*_passes`` predicates encode how ties
    are resolved so a decision is never silently reached.
    """

    MAJORITY = "majority"
    SUPERMAJORITY = "supermajority"
    UNANIMOUS = "unanimous"

    @property
    def required_share(self) -> float:
        return {"majority": 0.5, "supermajority": 2.0 / 3.0, "unanimous": 1.0}[
            self.value
        ]

    def approve_passes(self, approve_weight: float, reject_weight: float) -> bool:
        """Pass verdict on the approve side.

        * MAJORITY — strictly more approve than reject weight (a 50/50 tie
          does *not* pass).
        * SUPERMAJORITY — approve weight is at least two thirds of active
          weight (``aw >= 2*rw``).
        * UNANIMOUS — no reject among the active voters.
        """
        if self is ThresholdRule.MAJORITY:
            return approve_weight > reject_weight
        if self is ThresholdRule.SUPERMAJORITY:
            return approve_weight >= 2.0 * reject_weight
        return reject_weight == 0.0

    def reject_passes(self, approve_weight: float, reject_weight: float) -> bool:
        """Mirror verdict on the reject side (used to separate a clean
        rejection from a genuine no-consensus)."""
        if self is ThresholdRule.MAJORITY:
            return reject_weight > approve_weight
        if self is ThresholdRule.SUPERMAJORITY:
            return reject_weight >= 2.0 * approve_weight
        return reject_weight > 0.0


class ConsensusOutcome(str, Enum):
    """Closed consensus verdicts.  A ballot always lands on one of these —
    a no-consensus is a *defined* outcome, never a silent pass."""

    PASSED = "passed"
    REJECTED = "rejected"
    NO_CONSENSUS = "no_consensus"


# ---------------------------------------------------------------------------
# Lanes and missions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lane:
    """A typed worker pool: role + persona/specialization + disjoint agents.

    ``agent_ids`` names the concrete agents in the pool.  When empty the pool
    is the single implicit agent ``lane_id``.  Lane pools are disjoint by
    construction — validation lives in :func:`validate_lanes`.
    """

    lane_id: str
    role: LaneRole
    persona: str = ""
    specialization: str = ""
    agent_ids: Tuple[str, ...] = field(default_factory=tuple)

    def agents(self) -> Tuple[str, ...]:
        return tuple(self.agent_ids) if self.agent_ids else (self.lane_id,)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "role": self.role.value,
            "persona": self.persona,
            "specialization": self.specialization,
            "agent_ids": list(self.agent_ids),
        }


def validate_lanes(lanes: Sequence[Lane]) -> None:
    """Structural guarantees: unique lane ids, disjoint agent pools.

    This is the model-level collision-avoidance / disjoint-work guarantee
    (leaderboard fanout doctrine): no two lanes share an agent, so work
    dispatched to different lanes can never collide on a worker.
    """
    seen_ids: Dict[str, str] = {}
    seen_agents: Dict[str, str] = {}
    for lane in lanes:
        if not lane.lane_id:
            raise ValueError("lane_id must be non-empty")
        if lane.lane_id in seen_ids:
            raise ValueError(f"duplicate lane id: {lane.lane_id!r}")
        seen_ids[lane.lane_id] = lane.lane_id
        for agent in lane.agents():
            if agent in seen_agents:
                raise ValueError(
                    f"agent {agent!r} shared by lanes "
                    f"{seen_agents[agent]!r} and {lane.lane_id!r}"
                )
            seen_agents[agent] = lane.lane_id


@dataclass(frozen=True)
class Mission:
    """One unit of delegated work the planner decomposes and fans out."""

    mission_id: str
    objective: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "objective": self.objective,
            "context": dict(self.context),
        }


# ---------------------------------------------------------------------------
# Delegated agent work
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentTask:
    """A single delegated task handed to one agent through the runner seam."""

    task_id: str
    objective: str
    lane_id: str = ""
    agent_id: str = ""
    kind: TaskKind = TaskKind.WORK
    context: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "lane_id": self.lane_id,
            "agent_id": self.agent_id,
            "kind": self.kind.value,
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class AgentResult:
    """The deterministic result of one injected ``run_agent`` call."""

    agent_id: str
    task_id: str
    status: ResultStatus = ResultStatus.SUCCEEDED
    output: Any = None
    reasoning: str = ""
    confidence: float = 1.0
    error: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0,1]: {self.confidence}")
        if self.status is ResultStatus.FAILED and not self.error:
            # A failed result carries a reason so it is never a silent failure.
            object.__setattr__(self, "error", self.error or "agent failed")

    @property
    def ok(self) -> bool:
        return self.status is ResultStatus.SUCCEEDED

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "output": self.output,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "error": self.error,
        }


def ok_result(agent_id: str, task_id: str, output: Any = None,
              reasoning: str = "", confidence: float = 1.0) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        task_id=task_id,
        status=ResultStatus.SUCCEEDED,
        output=output,
        reasoning=reasoning,
        confidence=confidence,
    )


def fail_result(agent_id: str, task_id: str, error: str = "agent failed",
                output: Any = None) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        task_id=task_id,
        status=ResultStatus.FAILED,
        output=output,
        error=error,
        confidence=0.0,
    )


# ---------------------------------------------------------------------------
# Fan-out (hierarchical dispatch)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Subtask:
    """One specialist assignment produced by the planner's decomposition."""

    subtask_id: str
    objective: str
    lane_id: str
    agent_id: str = ""
    required: bool = True  # a required subtask that fails triggers escalation
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subtask_id:
            raise ValueError("subtask_id must be non-empty")
        if not self.lane_id:
            raise ValueError("subtask must be assigned to a lane")

    def to_task(self) -> AgentTask:
        return AgentTask(
            task_id=self.subtask_id,
            objective=self.objective,
            lane_id=self.lane_id,
            agent_id=self.agent_id,
            context=self.context,
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "objective": self.objective,
            "lane_id": self.lane_id,
            "agent_id": self.agent_id,
            "required": self.required,
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class FanOutPlan:
    """The planner's bounded dispatch plan: mission + subtask assignments."""

    mission_id: str
    planner_lane_id: str
    subtasks: Tuple[Subtask, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FanOutItem:
    """One dispatched subtask and its injected agent result."""

    subtask: Subtask
    result: AgentResult

    @property
    def ok(self) -> bool:
        return self.result.ok

    def as_dict(self) -> Dict[str, Any]:
        return {
            "subtask": self.subtask.as_dict(),
            "result": self.result.as_dict(),
        }


@dataclass(frozen=True)
class FanOutReport:
    """allSettled report of one dispatch round (never aborts on a failure)."""

    plan: FanOutPlan
    items: Tuple[FanOutItem, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> Tuple[FanOutItem, ...]:
        return tuple(item for item in self.items if item.ok)

    @property
    def failed(self) -> Tuple[FanOutItem, ...]:
        return tuple(item for item in self.items if not item.ok)

    def unacceptable(self, require_confidence: float) -> Tuple[FanOutItem, ...]:
        """Required items whose result is missing or below the confidence bar."""
        return tuple(
            item
            for item in self.items
            if item.subtask.required
            and (not item.ok or item.result.confidence < require_confidence)
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "plan": fan_out_plan_to_dict(self.plan),
            "items": [item.as_dict() for item in self.items],
        }


def fan_out_plan_to_dict(plan: FanOutPlan) -> Dict[str, Any]:
    return {
        "mission_id": plan.mission_id,
        "planner_lane_id": plan.planner_lane_id,
        "subtasks": [sub.as_dict() for sub in plan.subtasks],
    }


def fan_out_plan_from_dict(data: Mapping[str, Any]) -> FanOutPlan:
    subtasks = tuple(
        Subtask(
            subtask_id=item["subtask_id"],
            objective=item["objective"],
            lane_id=item["lane_id"],
            agent_id=item.get("agent_id", ""),
            required=bool(item.get("required", True)),
            context=dict(item.get("context", {})),
        )
        for item in data.get("subtasks", [])
    )
    return FanOutPlan(
        mission_id=data["mission_id"],
        planner_lane_id=data["planner_lane_id"],
        subtasks=subtasks,
    )


@dataclass(frozen=True)
class EscalationContext:
    """What the planner observes when (re)planning one round.

    Round 0 has no prior plan/report; every later round carries the previous
    dispatch so the planner can re-plan around its required failures.
    """

    mission: Mission
    round: int
    prior_plan: Optional[FanOutPlan] = None
    prior_report: Optional[FanOutReport] = None

    @property
    def required_failures(self) -> Tuple[str, ...]:
        """Subtask ids that failed and are required (the escalation trigger)."""
        if self.prior_report is None:
            return ()
        return tuple(
            item.subtask.subtask_id
            for item in self.prior_report.items
            if item.subtask.required and not item.ok
        )


# ---------------------------------------------------------------------------
# Consensus / voting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsensusConfig:
    """Tuning for one ballot: threshold rule + quorum."""

    rule: ThresholdRule = ThresholdRule.MAJORITY
    quorum_ratio: float = 1.0  # fraction of registered voters that must vote
    min_voters: int = 1

    def __post_init__(self) -> None:
        if not 0.0 < self.quorum_ratio <= 1.0:
            raise ValueError("quorum_ratio must be within (0, 1]")
        if self.min_voters < 1:
            raise ValueError("min_voters must be >= 1")


@dataclass(frozen=True)
class Vote:
    """One weighted vote cast by a voter agent."""

    voter_id: str
    lane_id: str
    choice: VoteChoice
    weight: float = 1.0
    confidence: float = 1.0
    reasoning: str = ""

    def __post_init__(self) -> None:
        if self.weight <= 0.0:
            raise ValueError(f"vote weight must be > 0: {self.weight}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0,1]: {self.confidence}")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "voter_id": self.voter_id,
            "lane_id": self.lane_id,
            "choice": self.choice.value,
            "weight": self.weight,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


@dataclass(frozen=True)
class ConsensusRequest:
    """The proposal put to a registered pool of voter lanes."""

    request_id: str
    proposal: str
    voters: Tuple[str, ...] = field(default_factory=tuple)
    context: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "proposal": self.proposal,
            "voters": list(self.voters),
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class ConsensusResult:
    """The defined outcome of one ballot — always explicit, never a silent pass.

    ``outcome`` is PASSED / REJECTED / NO_CONSENSUS.  ``reason`` is a
    machine-readable code explaining a non-PASSED verdict: ``all_abstained``,
    ``no_quorum``, ``tie``, ``unresolved``, ``vetoed`` or ``rejected``.
    """

    request_id: str
    outcome: ConsensusOutcome
    rule: ThresholdRule
    votes: Tuple[Vote, ...] = field(default_factory=tuple)
    registered: int = 0
    active: int = 0
    quorum_required: int = 0
    approve_weight: float = 0.0
    reject_weight: float = 0.0
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome is ConsensusOutcome.PASSED

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "outcome": self.outcome.value,
            "rule": self.rule.value,
            "reason": self.reason,
            "registered": self.registered,
            "active": self.active,
            "quorum_required": self.quorum_required,
            "approve_weight": self.approve_weight,
            "reject_weight": self.reject_weight,
            "votes": [vote.as_dict() for vote in self.votes],
        }


# ---------------------------------------------------------------------------
# Aggregated reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregationReport:
    """The planner lane's synthesis of one (final) fan-out round."""

    planner_lane_id: str
    summary: str
    findings: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    failures: Tuple[str, ...] = field(default_factory=tuple)  # subtask ids
    escalated: Tuple[str, ...] = field(default_factory=tuple)  # still unresolved

    def as_dict(self) -> Dict[str, Any]:
        return {
            "planner_lane_id": self.planner_lane_id,
            "summary": self.summary,
            "findings": list(self.findings),
            "failures": list(self.failures),
            "escalated": list(self.escalated),
        }


@dataclass(frozen=True)
class HierarchyReport:
    """Full hierarchical run: every dispatch round + the planner aggregation."""

    mission: Mission
    planner_lane_id: str
    plans: Tuple[FanOutPlan, ...] = field(default_factory=tuple)
    reports: Tuple[FanOutReport, ...] = field(default_factory=tuple)
    escalation_count: int = 0
    aggregate: Optional[AggregationReport] = None

    @property
    def final_report(self) -> Optional[FanOutReport]:
        return self.reports[-1] if self.reports else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission": self.mission.as_dict(),
            "planner_lane_id": self.planner_lane_id,
            "plans": [fan_out_plan_to_dict(plan) for plan in self.plans],
            "reports": [report.as_dict() for report in self.reports],
            "escalation_count": self.escalation_count,
            "aggregate": self.aggregate.as_dict() if self.aggregate else None,
        }


@dataclass(frozen=True)
class OrchestrationReport:
    """One multi-agent run: the hierarchical outcome plus an optional verdict."""

    mission: Mission
    hierarchy: HierarchyReport
    consensus: Optional[ConsensusResult] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission": self.mission.as_dict(),
            "hierarchy": self.hierarchy.as_dict(),
            "consensus": self.consensus.as_dict() if self.consensus else None,
        }


# ---------------------------------------------------------------------------
# Runtime configuration (bounds)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HierarchyConfig:
    """Bounded fan-out / escalation knobs for the hierarchical planner."""

    max_fan_out: int = 16  # hard cap on subtasks per dispatch round
    max_escalation_rounds: int = 2  # re-plans the planner may attempt
    require_confidence: float = 0.0  # required specialists must meet this bar

    def __post_init__(self) -> None:
        if self.max_fan_out < 1:
            raise ValueError("max_fan_out must be >= 1")
        if self.max_escalation_rounds < 0:
            raise ValueError("max_escalation_rounds must be >= 0")
        if not 0.0 <= self.require_confidence <= 1.0:
            raise ValueError("require_confidence must be within [0,1]")
