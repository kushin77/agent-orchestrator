"""Audit-trail tests for merge governance (issue #43 acceptance criterion 5).

Every merge decision leaves an append-only audit trail on the PR: the verify
run and its commit-named attestation, the reviewer assignment, the review
verdict, and the final merge/block decision with its evidence. Blocked
decisions record why.
"""

from __future__ import annotations

from engine import MergeGovernanceEngine
from gate import green_gate, red_gate
from model import BlockReason, MergeGovernanceMachine, PrState
from reviewer import PersonaAssigner


class TestMergeableAuditTrail:
    def test_happy_path_records_full_decision_trail(self, pr_factory, mini_cards):
        engine = MergeGovernanceEngine(
            gate_check=green_gate(),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=True,
        )
        result = engine.run(
            number=20, title="t", author="coder", subject="a change",
            branch="issue-20", merger="coder", commit="abc123",
        )
        actions = [entry.action for entry in result.audit]
        assert actions == [
            "verify-start",
            "verify-pass",
            "reviewer-assigned",
            "review-approve",
            "merge-permitted",
        ]
        final = result.audit[-1]
        assert final.commit == "abc123"
        assert result.mergeable is True

    def test_audit_is_append_only_and_seq_ordered(self, pr_factory, mini_cards):
        machine = MergeGovernanceMachine(pr_factory())
        machine.begin_verify()
        machine.record_verify(rc=0, commit="c1")
        seqs = [e.seq for e in machine.pr.audit]
        assert seqs == sorted(seqs) == [1, 2]
        assert [e.action for e in machine.pr.audit] == ["verify-start", "verify-pass"]


class TestBlockedAuditTrail:
    def test_red_gate_audit_records_verify_failure_and_evidence(
        self, pr_factory, mini_cards
    ):
        engine = MergeGovernanceEngine(
            gate_check=red_gate("negative-controls failed"),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=True,
        )
        result = engine.run(
            number=21, title="t", author="coder", subject="a change",
            branch="issue-21", merger="coder", commit="abc123",
        )
        actions = [entry.action for entry in result.audit]
        assert actions == ["verify-start", "block"]
        assert result.block_reason == BlockReason.VERIFY_NOT_GREEN.value
        block_entry = result.audit[-1]
        assert "negative-controls failed" in block_entry.detail

    def test_no_reviewer_audit_records_reason(self, executor_only_cards):
        engine = MergeGovernanceEngine(
            gate_check=green_gate(),
            reviewer_assigner=PersonaAssigner(
                cards=executor_only_cards, use_real_mapping=False
            ),
            owner_carve_out=True,
        )
        result = engine.run(
            number=22, title="t", author="coder", subject="a change",
            branch="issue-22", merger="coder", commit="abc",
        )
        assert result.block_reason == BlockReason.REVIEWER_NOT_ASSIGNED.value
        assert [e.action for e in result.audit][-1] == "block"

    def test_self_merge_denial_is_audited(self, mini_cards):
        engine = MergeGovernanceEngine(
            gate_check=green_gate(),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=False,
        )
        result = engine.run(
            number=23, title="t", author="coder", subject="a change",
            branch="issue-23", merger="coder", commit="abc",
        )
        assert result.block_reason == BlockReason.SELF_MERGE_WITHOUT_CARVE_OUT.value
        actions = [entry.action for entry in result.audit]
        assert actions == [
            "verify-start",
            "verify-pass",
            "reviewer-assigned",
            "review-approve",
            "block",
        ]
        deny = result.audit[-1]
        assert "self-merge-without-owner-carve-out" in deny.detail


class TestEvidenceIsNamed:
    def test_merge_decision_evidence_names_the_attested_commit(
        self, pr_factory, mini_cards
    ):
        engine = MergeGovernanceEngine(
            gate_check=green_gate(),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=True,
        )
        result = engine.run(
            number=24, title="t", author="coder", subject="a change",
            branch="issue-24", merger="coder", commit="deadbeef",
        )
        # the audit trail and the PR record both name the attested commit
        assert result.verify_commit == "deadbeef"
        assert result.audit_records()[-1]["commit"] == "deadbeef"
        # an attestation that cannot name a commit is never evidence
        pr = pr_factory()
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=0, commit=None)
        assert pr.state is PrState.BLOCKED
        assert pr.block_reason == BlockReason.VERIFY_NO_COMMIT.value
