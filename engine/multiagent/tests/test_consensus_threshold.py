"""Consensus reaching threshold: majority / supermajority / unanimous passes."""

from __future__ import annotations

from engine.multiagent.consensus import run_consensus, tally_votes
from engine.multiagent.model import (
    ConsensusConfig,
    ConsensusOutcome,
    ConsensusRequest,
    ThresholdRule,
    Vote,
    VoteChoice,
    ok_result,
)
from engine.multiagent.runner import ScriptedRunner


def _vote(voter_id, choice, weight=1.0):
    return Vote(
        voter_id=voter_id,
        lane_id=voter_id,
        choice=VoteChoice(choice),
        weight=weight,
    )


def _request(voter_ids):
    return ConsensusRequest(
        request_id="r1", proposal="approve launch", voters=tuple(voter_ids)
    )


def _tally(voter_ids, votes, rule=ThresholdRule.MAJORITY, quorum_ratio=1.0):
    config = ConsensusConfig(rule=rule, quorum_ratio=quorum_ratio)
    return tally_votes(_request(voter_ids), votes, config=config)


class TestThresholdPasses:
    def test_majority_passes_with_strict_majority(self):
        voters = [f"v{i}" for i in range(5)]
        votes = [_vote("v0", "approve"), _vote("v1", "approve"), _vote("v2", "approve"),
                 _vote("v3", "reject"), _vote("v4", "reject")]
        result = _tally(voters, votes)
        assert result.outcome is ConsensusOutcome.PASSED
        assert result.approve_weight == 3 and result.reject_weight == 2

    def test_supermajority_passes(self):
        voters = [f"v{i}" for i in range(5)]
        votes = [_vote(f"v{i}", "approve") for i in range(4)] + [_vote("v4", "reject")]
        result = _tally(voters, votes, rule=ThresholdRule.SUPERMAJORITY)
        assert result.outcome is ConsensusOutcome.PASSED

    def test_unanimous_passes_when_no_rejects(self):
        voters = [f"v{i}" for i in range(3)]
        votes = [_vote(f"v{i}", "approve") for i in range(3)]
        result = _tally(voters, votes, rule=ThresholdRule.UNANIMOUS)
        assert result.outcome is ConsensusOutcome.PASSED

    def test_weighted_majority_vote(self):
        """Weighted expertise: heavier approve votes outweigh more reject votes."""
        voters = ["v0", "v1", "v2", "v3"]
        votes = [
            _vote("v0", "approve", weight=2.0),
            _vote("v1", "approve", weight=2.0),
            _vote("v2", "reject", weight=1.0),
            _vote("v3", "reject", weight=1.0),
        ]
        result = _tally(voters, votes)
        assert result.outcome is ConsensusOutcome.PASSED
        assert result.approve_weight == 4.0 and result.reject_weight == 2.0

    def test_quorum_met_when_all_registered_vote(self):
        voters = ["v0", "v1", "v2"]
        votes = [_vote("v0", "approve"), _vote("v1", "approve"), _vote("v2", "reject")]
        result = _tally(voters, votes)
        assert result.active == 3 and result.quorum_required == 3
        assert result.outcome is ConsensusOutcome.PASSED


class TestRunConsensus:
    AGENTS = {"voter-a": "v-a-1", "voter-b": "v-b-1", "voter-c": "v-c-1"}

    def _runner_for(self, decisions):
        """decisions: voter-lane id -> approve|reject|abstain."""
        runner = ScriptedRunner()
        for lane, choice in decisions.items():
            agent = self.AGENTS[lane]
            runner.on(
                agent,
                f"r1:vote:{lane}",
                ok_result(agent, f"r1:vote:{lane}", output={"choice": choice}),
            )
        return runner

    def test_consensus_reaches_threshold_end_to_end(self, voter_lanes):
        request = _request([lane.lane_id for lane in voter_lanes])
        decisions = {"voter-a": "approve", "voter-b": "approve", "voter-c": "approve"}
        result = run_consensus(request, voter_lanes, self._runner_for(decisions))
        assert result.outcome is ConsensusOutcome.PASSED
        assert result.active == 3
        assert result.registered == 3

    def test_consensus_weighted_via_orchestrator_weights(self, voter_lanes):
        request = _request([lane.lane_id for lane in voter_lanes])
        runner = self._runner_for(
            {"voter-a": "approve", "voter-b": "approve", "voter-c": "reject"}
        )
        result = run_consensus(
            request, voter_lanes, runner, weights={"voter-a": 2.0, "voter-b": 2.0}
        )
        # approve weight 4 vs reject weight 1 -> passes
        assert result.outcome is ConsensusOutcome.PASSED
