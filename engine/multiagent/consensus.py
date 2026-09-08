"""engine.multiagent — consensus / voting protocol (peer pattern).

The peer pattern from the issue: a proposal is put to a registered pool of
voter lanes; every voter agent casts a *weighted* vote (approve / reject /
abstain, majority-vote + weighted-expertise from the news-feed-engine
multi-agent-system design).  A ballot always lands on a **defined** outcome
— PASSED, REJECTED or NO_CONSENSUS — never a silent pass.  No-consensus is
explicit and carries a machine-readable reason (``all_abstained``,
``no_quorum``, ``tie``, ``unresolved``, ``vetoed``, ``rejected``).

:func:`tally_votes` is the pure, deterministic decision core (heavily unit
tested); :func:`run_consensus` obtains the votes through the injected runner
seam so the whole protocol is testable with a scripted runner.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .model import (
    AgentResult,
    AgentTask,
    ConsensusConfig,
    ConsensusOutcome,
    ConsensusRequest,
    ConsensusResult,
    Lane,
    LaneRole,
    ResultStatus,
    TaskKind,
    ThresholdRule,
    Vote,
    VoteChoice,
)
from .runner import fail_result


def vote_from_agent_result(
    lane: Lane, result: AgentResult, weight: float = 1.0
) -> Vote:
    """Parse one voter agent's result into a :class:`Vote`.

    Fail-closed: a voter agent that fails, or returns an unreadable output,
    is recorded as an explicit ABSTAIN with the reason captured — never a
    silent approve.  Under the default quorum such an abstain pushes the
    ballot to NO_CONSENSUS rather than a pass.
    """
    output = result.output if isinstance(result.output, Mapping) else {}
    if result.ok and "choice" in output:
        try:
            choice = VoteChoice(str(output["choice"]).lower())
        except ValueError:
            choice = VoteChoice.ABSTAIN
        if choice is not VoteChoice.ABSTAIN:
            return Vote(
                voter_id=lane.lane_id,
                lane_id=lane.lane_id,
                choice=choice,
                weight=weight,
                confidence=result.confidence,
                reasoning=str(output.get("reasoning", result.reasoning)),
            )
    reason = result.error or "voter output was not a readable vote"
    return Vote(
        voter_id=lane.lane_id,
        lane_id=lane.lane_id,
        choice=VoteChoice.ABSTAIN,
        weight=weight,
        confidence=0.0,
        reasoning=f"ABSTAIN ({reason})",
    )


def _quorum_required(config: ConsensusConfig, registered_count: int) -> int:
    if registered_count == 0:
        return config.min_voters
    return max(config.min_voters, int(math.ceil(config.quorum_ratio * registered_count)))


def tally_votes(
    request: ConsensusRequest,
    votes: Sequence[Vote],
    config: Optional[ConsensusConfig] = None,
    registered: Optional[Sequence[str]] = None,
) -> ConsensusResult:
    """Deterministic decision core for one ballot (pure; no I/O).

    ``registered`` is the voter pool the quorum is computed against (defaults
    to ``request.voters``, then to the distinct voters who cast a vote).
    """
    config = config or ConsensusConfig()
    cast = tuple(votes)
    if registered is not None:
        registered_ids = tuple(registered)
    elif request.voters:
        registered_ids = tuple(request.voters)
    else:
        registered_ids = tuple(dict.fromkeys(v.voter_id for v in cast))
    active = tuple(v for v in cast if v.choice is not VoteChoice.ABSTAIN)

    quorum = _quorum_required(config, len(registered_ids))
    base = ConsensusResult(
        request_id=request.request_id,
        outcome=ConsensusOutcome.NO_CONSENSUS,
        rule=config.rule,
        votes=cast,
        registered=len(registered_ids),
        active=len(active),
        quorum_required=quorum,
        approve_weight=0.0,
        reject_weight=0.0,
        reason="all_abstained",
    )
    if not active:
        return base  # NO_CONSENSUS: all_abstained

    approve_weight = sum(v.weight for v in active if v.choice is VoteChoice.APPROVE)
    reject_weight = sum(v.weight for v in active if v.choice is VoteChoice.REJECT)
    base = ConsensusResult(
        request_id=request.request_id,
        outcome=ConsensusOutcome.NO_CONSENSUS,
        rule=config.rule,
        votes=cast,
        registered=len(registered_ids),
        active=len(active),
        quorum_required=quorum,
        approve_weight=approve_weight,
        reject_weight=reject_weight,
        reason="no_quorum",
    )
    if len(active) < quorum:
        return base  # NO_CONSENSUS: no_quorum

    rule = config.rule
    if rule.approve_passes(approve_weight, reject_weight):
        return ConsensusResult(
            request_id=request.request_id,
            outcome=ConsensusOutcome.PASSED,
            rule=rule,
            votes=cast,
            registered=len(registered_ids),
            active=len(active),
            quorum_required=quorum,
            approve_weight=approve_weight,
            reject_weight=reject_weight,
            reason="",
        )
    if rule.reject_passes(approve_weight, reject_weight):
        reason = "vetoed" if rule is ThresholdRule.UNANIMOUS else "rejected"
        return ConsensusResult(
            request_id=request.request_id,
            outcome=ConsensusOutcome.REJECTED,
            rule=rule,
            votes=cast,
            registered=len(registered_ids),
            active=len(active),
            quorum_required=quorum,
            approve_weight=approve_weight,
            reject_weight=reject_weight,
            reason=reason,
        )
    reason = "tie" if rule is ThresholdRule.MAJORITY else "unresolved"
    return ConsensusResult(
        request_id=request.request_id,
        outcome=ConsensusOutcome.NO_CONSENSUS,
        rule=rule,
        votes=cast,
        registered=len(registered_ids),
        active=len(active),
        quorum_required=quorum,
        approve_weight=approve_weight,
        reject_weight=reject_weight,
        reason=reason,
    )


def run_consensus(
    request: ConsensusRequest,
    voter_lanes: Sequence[Lane],
    runner: object,
    config: Optional[ConsensusConfig] = None,
    weights: Optional[Mapping[str, float]] = None,
) -> ConsensusResult:
    """Run one ballot: every voter lane's agent votes through the runner."""
    weights = dict(weights or {})
    votes: list = []
    for lane in voter_lanes:
        if lane.role is not LaneRole.VOTER:
            raise ValueError(f"lane {lane.lane_id!r} must have role VOTER")
        agent = lane.agents()[0]
        task = AgentTask(
            task_id=f"{request.request_id}:vote:{lane.lane_id}",
            objective=request.proposal,
            lane_id=lane.lane_id,
            agent_id=agent,
            kind=TaskKind.VOTE,
            context=dict(request.context),
        )
        try:
            raw = runner.run_agent(agent, task)
        except Exception as exc:  # a crashing voter is an explicit abstain
            raw = fail_result(agent, task.task_id, error=f"{type(exc).__name__}: {exc}")
        result = (
            raw
            if isinstance(raw, AgentResult)
            else _coerce_result(raw, agent, task.task_id)
        )
        weight = float(weights.get(lane.lane_id, 1.0))
        votes.append(vote_from_agent_result(lane, result, weight=weight))
    return tally_votes(
        request,
        tuple(votes),
        config=config,
        registered=tuple(lane.lane_id for lane in voter_lanes),
    )


def _coerce_result(raw: Any, agent: str, task_id: str) -> AgentResult:
    from .runner import coerce_agent_result  # local to avoid a hard cycle

    return coerce_agent_result(raw, agent, task_id)
