"""Verify-gate integration tests (issue #43 acceptance criterion 3).

A PR cannot reach mergeable without green verification: the gate is modeled as
an injected check that must be green, and a failing (NOT-OK) or undetermined
(CANNOT-ASSESS) gate — or a green check with no commit-named attestation —
always blocks. Reviewer approval never rescues a red gate: reviewer verdicts
are evidence, not final.
"""

from __future__ import annotations

import pytest

from gate import aggregate_exit_codes, cannot_assess_gate, red_gate
from model import BlockReason, MergeGovernanceMachine, PrState


class TestGreenVerifyRequired:
    def test_mergeable_requires_green_verify_with_attestation(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=0, commit=None, evidence="green but no commit")
        # an OK result with no commit is not an attestation -> blocked
        assert pr.state is PrState.BLOCKED
        assert pr.block_reason == BlockReason.VERIFY_NO_COMMIT.value

    def test_red_gate_blocks_before_review(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=1, commit="abc", evidence="lint failed")
        assert pr.state is PrState.BLOCKED
        assert pr.block_reason == BlockReason.VERIFY_NOT_GREEN.value

    def test_cannot_assess_is_never_a_pass(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=2, commit="abc", evidence="timeout")
        assert pr.state is PrState.BLOCKED
        assert pr.block_reason == BlockReason.VERIFY_CANNOT_ASSESS.value

    def test_reviewer_approval_does_not_rescue_a_red_gate(self, pr_factory):
        # The linear chain makes approval unreachable behind a red gate; the
        # engine run below proves the end-to-end invariant: red gate -> blocked
        # even though a reviewer would have approved.
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=1, commit="abc", evidence="tests failed")
        with pytest.raises(Exception):
            machine.approve_review()  # not allowed from BLOCKED/VERIFY_GATE
        assert pr.state is PrState.BLOCKED
        assert not pr.is_mergeable


class TestEngineGateInjection:
    def test_engine_blocks_on_red_injected_gate(self, pr_factory, mini_cards):
        from engine import MergeGovernanceEngine
        from reviewer import PersonaAssigner

        engine = MergeGovernanceEngine(
            gate_check=red_gate("tests failed"),
            reviewer_assigner=PersonaAssigner(
                cards=mini_cards, use_real_mapping=False
            ),
            owner_carve_out=True,
        )
        result = engine.run(
            number=7,
            title="t",
            author="coder",
            subject="any change",
            branch="issue-7",
            merger="coder",
            commit="abc",
        )
        assert result.mergeable is False
        assert result.block_reason == BlockReason.VERIFY_NOT_GREEN.value

    def test_engine_blocks_on_cannot_assess_injected_gate(self):
        from engine import MergeGovernanceEngine

        engine = MergeGovernanceEngine(gate_check=cannot_assess_gate(), owner_carve_out=True)
        result = engine.run(
            number=8,
            title="t",
            author="coder",
            subject="any change",
            branch="issue-8",
            merger="coder",
            commit="abc",
        )
        assert result.mergeable is False
        assert result.block_reason == BlockReason.VERIFY_CANNOT_ASSESS.value


class TestAggregationMirror:
    def test_all_ok_passes(self):
        assert aggregate_exit_codes([0, 0, 0, 0, 0]) == 0

    def test_any_not_ok_fails(self):
        # mirrors merge-gate.sh: any NOT-OK fails the composite
        assert aggregate_exit_codes([0, 1, 0, 0, 0]) == 1

    def test_cannot_assess_keeps_gate_from_green(self):
        # any CANNOT-ASSESS (2) keeps the gate from green
        assert aggregate_exit_codes([0, 2, 0, 0, 0]) == 2
        assert aggregate_exit_codes([2, 2, 2]) == 2

    def test_cannot_assess_never_overrides_not_ok(self):
        assert aggregate_exit_codes([1, 2, 2]) == 1
