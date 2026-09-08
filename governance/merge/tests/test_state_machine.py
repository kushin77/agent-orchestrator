"""State-machine tests for the merge-governance lifecycle (issue #43).

Covers the acceptance-chain transitions
``opened -> verify-gate -> sme-review-assigned -> approved/verified ->
mergeable | blocked``, invalid-transition refusal, and the blocked-PR retry
discipline (a red gate blocks until a real fix commit).
"""

from __future__ import annotations

import pytest

from model import (
    BlockReason,
    InvalidTransition,
    MergeGovernanceMachine,
    MergeGovernanceViolation,
    PrState,
)


def _drive_to_approved(pr, *, rc=0, commit="abc123", reviewer="reviewer"):
    """Drive a PR through the linear chain up to approved/verified."""
    machine = MergeGovernanceMachine(pr)
    machine.begin_verify()
    machine.record_verify(rc=rc, commit=commit, evidence="make verify green")
    assert pr.state is PrState.SME_REVIEW_ASSIGNED
    machine.assign_reviewer(persona_id=reviewer, posture="reviewer")
    machine.approve_review()
    assert pr.state is PrState.APPROVED_VERIFIED
    return machine


class TestLinearLifecycle:
    def test_happy_path_reaches_mergeable(self, pr_factory):
        pr = pr_factory()
        machine = _drive_to_approved(pr)
        machine.conclude_merge(merger="integrator", owner_carve_out=False)
        assert pr.state is PrState.MERGEABLE
        assert pr.is_mergeable
        assert pr.merge_evidence_commit == "abc123"

    def test_states_follow_acceptance_chain(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        assert pr.state is PrState.OPENED
        machine.begin_verify()
        assert pr.state is PrState.VERIFY_GATE
        machine.record_verify(rc=0, commit="c1")
        assert pr.state is PrState.SME_REVIEW_ASSIGNED
        machine.assign_reviewer(persona_id="qa-sme", posture="reviewer")
        machine.approve_review()
        assert pr.state is PrState.APPROVED_VERIFIED
        machine.conclude_merge(merger="integrator", owner_carve_out=False)
        assert pr.state is PrState.MERGEABLE

    def test_open_to_verify_then_review_then_merge_order_is_enforced(
        self, pr_factory
    ):
        # approve_review before the verify gate is green is structurally
        # impossible: the state machine only allows approve from
        # SME_REVIEW_ASSIGNED, which is only reached by a green record_verify.
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        with pytest.raises(InvalidTransition):
            machine.approve_review()


class TestInvalidTransitions:
    def test_conclude_merge_from_opened_is_rejected(self, pr_factory):
        machine = MergeGovernanceMachine(pr_factory())
        with pytest.raises(InvalidTransition):
            machine.conclude_merge(merger="x", owner_carve_out=False)

    def test_begin_verify_twice_is_rejected(self, pr_factory):
        machine = MergeGovernanceMachine(pr_factory())
        machine.begin_verify()
        with pytest.raises(InvalidTransition):
            machine.begin_verify()

    def test_retry_from_non_blocked_is_rejected(self, pr_factory):
        pr = pr_factory()
        machine = _drive_to_approved(pr)
        with pytest.raises(InvalidTransition):
            machine.retry(fix_commit="new-commit")

    def test_assign_reviewer_rejects_non_reviewer_posture(self, pr_factory):
        machine = MergeGovernanceMachine(pr_factory())
        machine.begin_verify()
        with pytest.raises(MergeGovernanceViolation):
            machine.assign_reviewer(persona_id="coder", posture="executor")


class TestBlockedRetry:
    def test_blocked_is_terminal_until_retry(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=1, commit="bad")
        assert pr.state is PrState.BLOCKED
        assert pr.block_reason == BlockReason.VERIFY_NOT_GREEN.value
        # no further success transition is allowed from BLOCKED
        with pytest.raises(InvalidTransition):
            machine.conclude_merge(merger="x", owner_carve_out=False)

    def test_retry_requires_a_fix_commit(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=1, commit="bad")
        with pytest.raises(InvalidTransition):
            machine.retry(fix_commit="")

    def test_retry_on_same_commit_is_rejected(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=1, commit="bad")
        with pytest.raises(InvalidTransition):
            machine.retry(fix_commit="bad")

    def test_retry_with_new_commit_reenters_verify_gate(self, pr_factory):
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=1, commit="bad")
        machine.retry(fix_commit="fixed-commit")
        assert pr.state is PrState.VERIFY_GATE
        assert pr.verify_commit is None  # previous evidence cleared
        assert pr.block_reason is None
        # and a green gate on the fix commit can now reach MERGEABLE
        machine.record_verify(rc=0, commit="fixed-commit")
        machine.assign_reviewer(persona_id="reviewer", posture="reviewer")
        machine.approve_review()
        machine.conclude_merge(merger="integrator", owner_carve_out=False)
        assert pr.state is PrState.MERGEABLE


class TestPolicyRulesAreRecheckedAtConclude:
    def test_conclude_rechecks_verify_even_if_path_was_green(self, pr_factory):
        # Defense in depth: conclude_merge recomputes the policy from recorded
        # signals; a PR whose verification evidence is absent can never pass.
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        # no record_verify call; cannot even reach the conclude state, but the
        # policy function itself must refuse when evidence is missing:
        from model import MergeSignals, merge_verdict

        permitted, reasons = merge_verdict(
            MergeSignals(
                verify_green=False,
                reviewer_assigned=True,
                reviewer_distinct=True,
                reviewer_approved=True,
                author=pr.author,
                merger="integrator",
                owner_carve_out=False,
            )
        )
        assert permitted is False
        assert BlockReason.VERIFY_NOT_GREEN.value in reasons
