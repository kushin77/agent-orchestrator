"""No-consensus outcomes: every non-passing ballot is a *defined* outcome.

The doctrine under test: a ballot that cannot reach its threshold or quorum
never silently passes — it lands on REJECTED or NO_CONSENSUS with a
machine-readable reason (all_abstained / no_quorum / tie / unresolved /
vetoed / rejected).
"""

from __future__ import annotations

import pytest

from engine.multiagent.consensus import run_consensus, tally_votes
from engine.multiagent.model import (
    ConsensusConfig,
    ConsensusOutcome,
    ConsensusRequest,
    ThresholdRule,
    Vote,
    VoteChoice,
    fail_result,
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
    return tally_votes(
        _request(voter_ids), votes, config=ConsensusConfig(rule=rule, quorum_ratio=quorum_ratio)
    )


class TestDefinedNoConsensus:
    def test_all_abstain_is_no_consensus_not_pass(self):
        voters = ["v0", "v1", "v2"]
        votes = [_vote(v, "abstain") for v in voters]
        result = _tally(voters, votes)
        assert result.outcome is ConsensusOutcome.NO_CONSENSUS
        assert result.reason == "all_abstained"

    def test_no_quorum_when_registered_voter_abstains(self):
        voters = ["v0", "v1", "v2"]
        votes = [_vote("v0", "approve"), _vote("v1", "approve"), _vote("v2", "abstain")]
        result = _tally(voters, votes)  # quorum_ratio 1.0 -> needs all 3
        assert result.outcome is ConsensusOutcome.NO_CONSENSUS
        assert result.reason == "no_quorum"

    def test_tie_under_majority_is_no_consensus(self):
        voters = ["v0", "v1", "v2", "v3"]
        votes = [_vote("v0", "approve"), _vote("v1", "approve"),
                 _vote("v2", "reject"), _vote("v3", "reject")]
        result = _tally(voters, votes)
        assert result.outcome is ConsensusOutcome.NO_CONSENSUS
        assert result.reason == "tie"
        assert result.passed is False

    def test_supermajority_unresolved_split(self):
        """3 approve / 2 reject clears majority but not the 2/3 bar."""
        voters = ["v0", "v1", "v2", "v3", "v4"]
        votes = [_vote(f"v{i}", "approve") for i in range(3)] + \
                [_vote("v3", "reject"), _vote("v4", "reject")]
        result = _tally(voters, votes, rule=ThresholdRule.SUPERMAJORITY)
        assert result.outcome is ConsensusOutcome.NO_CONSENSUS
        assert result.reason == "unresolved"

    def test_unanimous_is_vetoed_by_one_reject(self):
        voters = ["v0", "v1", "v2"]
        votes = [_vote("v0", "approve"), _vote("v1", "approve"), _vote("v2", "reject")]
        result = _tally(voters, votes, rule=ThresholdRule.UNANIMOUS)
        assert result.outcome is ConsensusOutcome.REJECTED
        assert result.reason == "vetoed"

    def test_majority_rejection_is_explicit(self):
        voters = ["v0", "v1", "v2", "v3"]
        votes = [_vote("v0", "approve"), _vote("v1", "reject"),
                 _vote("v2", "reject"), _vote("v3", "reject")]
        result = _tally(voters, votes)
        assert result.outcome is ConsensusOutcome.REJECTED
        assert result.reason == "rejected"

    @pytest.mark.parametrize(
        "voter_ids,votes,rule",
        [
            (["v0", "v1", "v2"], [_vote("v0", "abstain") for _ in range(3)], ThresholdRule.MAJORITY),
            (["v0", "v1", "v2"], [_vote("v0", "approve"), _vote("v1", "abstain"), _vote("v2", "abstain")], ThresholdRule.MAJORITY),
            (["v0", "v1", "v2", "v3"], [_vote("v0", "approve"), _vote("v1", "approve"), _vote("v2", "reject"), _vote("v3", "reject")], ThresholdRule.MAJORITY),
            (["v0", "v1", "v2", "v3", "v4"], [_vote(f"v{i}", "approve") for i in range(3)] + [_vote("v3", "reject"), _vote("v4", "reject")], ThresholdRule.SUPERMAJORITY),
        ],
    )
    def test_never_a_silent_pass(self, voter_ids, votes, rule):
        """A ballot that does not reach its bar is never PASSED."""
        result = _tally(voter_ids, votes, rule=rule)
        assert result.outcome is not ConsensusOutcome.PASSED
        assert result.outcome in (
            ConsensusOutcome.REJECTED,
            ConsensusOutcome.NO_CONSENSUS,
        )


class TestFailClosedRunnerBallot:
    def test_crashing_voter_abstains_and_no_quorum_blocks_pass(self, voter_lanes):
        """A voter agent that crashes casts an explicit abstain; the default
        quorum (all registered must vote) then blocks any silent pass."""
        request = _request([lane.lane_id for lane in voter_lanes])

        def _boom(_agent_id, _task):
            raise RuntimeError("voter agent unreachable")

        runner = ScriptedRunner(default=_boom)
        result = run_consensus(request, voter_lanes, runner)
        assert result.outcome is ConsensusOutcome.NO_CONSENSUS
        assert result.reason in ("all_abstained", "no_quorum")
        assert result.passed is False

    def test_failed_voter_is_not_a_silent_approve(self, voter_lanes):
        request = _request([lane.lane_id for lane in voter_lanes])
        runner = ScriptedRunner()
        # only two of three voters return a readable approve
        runner.on("v-a-1", "r1:vote:voter-a", ok_result("v-a-1", "t", output={"choice": "approve"}))
        runner.on("v-b-1", "r1:vote:voter-b", ok_result("v-b-1", "t", output={"choice": "approve"}))
        runner.on("v-c-1", "r1:vote:voter-c", fail_result("v-c-1", "t", error="down"))
        result = run_consensus(request, voter_lanes, runner)
        assert result.outcome is ConsensusOutcome.NO_CONSENSUS
        assert result.reason == "no_quorum"
