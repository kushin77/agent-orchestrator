"""Independent SME reviewer assignment tests (issue #43 acceptance criterion 2).

Consumes issue #11 ``assign_reviewer`` semantics: a reviewer persona is
assigned (assigned, not the author), domain-scored, never the author/executor,
and the auditor is never the executing persona (auditor != executor
separation). The offline mirror and the real issue #11 module are both
exercised; the integration test proves they agree on the live platform
library.
"""

from __future__ import annotations

import pytest

from model import BlockReason, MergeGovernanceMachine, MergeGovernanceViolation
from reviewer import (
    AuditorCannotExecuteError,
    NoAuditorAvailableError,
    NoReviewerAvailableError,
    PersonaAssigner,
    SelfAuditError,
    SelfReviewError,
)


class TestReviewerAssignment:
    def test_reviewer_is_assigned_and_distinct_from_author(
        self, mini_cards, pr_factory
    ):
        assigner = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        persona = assigner.assign_reviewer(
            "platform", "coder", "merge governance of a verify-gate change"
        )
        assert persona.posture == "reviewer"
        assert persona.id != "coder"
        assigner.assert_reviewer_distinct("platform", "coder", persona)

    def test_domain_scoring_picks_the_sme(self, mini_cards):
        assigner = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        persona = assigner.assign_reviewer(
            "platform", "coder", "security review of the tenant isolation change"
        )
        assert persona.id == "security-sme"

    def test_tenant_reviewer_visible_to_its_tenant(self, mini_cards):
        assigner = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        persona = assigner.assign_reviewer(
            "acme", "coder", "merge governance of an acme domain change"
        )
        assert persona.tenant in ("acme", "platform")
        # platform reviewer never sees the acme tenant card
        platform_persona = assigner.assign_reviewer(
            "platform", "coder", "acme domain review"
        )
        assert platform_persona.id != "acme-reviewer"

    def test_no_reviewer_available_blocks(self, executor_only_cards, pr_factory):
        assigner = PersonaAssigner(
            cards=executor_only_cards, use_real_mapping=False
        )
        with pytest.raises(NoReviewerAvailableError):
            assigner.assign_reviewer("platform", "coder", "anything")

        # engine translates it into a blocked PR
        from engine import MergeGovernanceEngine
        from gate import green_gate

        engine = MergeGovernanceEngine(
            gate_check=green_gate(), reviewer_assigner=assigner, owner_carve_out=True
        )
        result = engine.run(
            number=9, title="t", author="coder", subject="anything",
            branch="issue-9", merger="coder", commit="abc",
        )
        assert result.mergeable is False
        assert result.block_reason == BlockReason.REVIEWER_NOT_ASSIGNED.value

    def test_self_review_is_rejected(self, mini_cards, pr_factory):
        # direct machine misuse: author attempts to review its own PR
        pr = pr_factory(author="coder")
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        machine.record_verify(rc=0, commit="abc")
        with pytest.raises(MergeGovernanceViolation):
            machine.assign_reviewer(persona_id="coder", posture="reviewer")

        # assigner-side: explicit same identity is refused
        assigner = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        from reviewer import ReviewerPersona

        with pytest.raises(SelfReviewError):
            assigner.assert_reviewer_distinct(
                "platform", "coder",
                ReviewerPersona(id="coder", name="Coder", posture="executor"),
            )


class TestAuditorSeparation:
    def test_auditor_is_assigned_distinct_from_executor(self, mini_cards):
        assigner = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        auditor = assigner.assign_auditor(
            "platform", "coder", "audit the merge evidence of PR #1"
        )
        assert auditor.posture == "auditor"
        assert auditor.id == "auditor"
        assigner.assert_auditor_not_executor(auditor, "platform", "coder")

    def test_auditor_cannot_be_the_executor(self, mini_cards):
        # mirror of issue #11 negative test: dispatching an auditor-posture
        # persona as the executor is structurally refused
        assigner = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        auditor = assigner.assign_auditor(
            "platform", "coder", "audit the merge evidence"
        )
        with pytest.raises(AuditorCannotExecuteError):
            assigner.guard_dispatch(auditor, "executor")

    def test_auditor_cannot_audit_its_own_execution(self, mini_cards):
        # mirror of issue #11 negative test: an auditor-posture persona used as
        # its own executor is rejected (SelfAuditError), and an executor cannot
        # act as the auditor of its own merge evidence
        assigner = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        executor = assigner.assign_auditor(
            "platform", "coder", "audit the merge evidence"
        )
        # sanity: the auditor is not the executor
        assigner.assert_auditor_not_executor(executor, "platform", "coder")
        # but the same identity as executor+auditor is structurally refused
        with pytest.raises(SelfAuditError):
            assigner.assert_auditor_not_executor(
                executor,
                "platform",
                executor.id,
            )

    def test_no_auditor_available(self):
        # a library without an auditor cannot assign one
        cards = {
            ("platform", "coder"): {
                "id": "coder",
                "version": "1.0.0",
                "tenant": "platform",
                "name": "Coder",
                "summary": "executor",
                "expertise": ["code"],
                "ownedLanes": ["code"],
                "posture": "executor",
            },
            ("platform", "reviewer"): {
                "id": "reviewer",
                "version": "1.0.0",
                "tenant": "platform",
                "name": "Reviewer",
                "summary": "reviewer",
                "expertise": ["review"],
                "ownedLanes": ["review"],
                "posture": "reviewer",
            },
        }
        assigner = PersonaAssigner(cards=cards, use_real_mapping=False)
        with pytest.raises(NoAuditorAvailableError):
            assigner.assign_auditor("platform", "coder", "anything")


class TestMeasuredBeforeTrust:
    def test_unvalidated_persona_is_never_assigned(self, mini_cards):
        """Reviewer model measured before trust: only personas that pass the
        validation corpus are assignable (leaderboard auditor-measurement /
        ADR-0012 evidence-based trust doctrine)."""
        assigner = PersonaAssigner(
            cards=mini_cards,
            use_real_mapping=False,
            valid=lambda card: card["id"] != "security-sme",
        )
        persona = assigner.assign_reviewer(
            "platform", "coder", "security review of the tenant isolation change"
        )
        assert persona.id != "security-sme"
        assert persona.posture == "reviewer"


class TestRealPersonaLibraryIntegration:
    """Exercises the committed platform library + real issue #11 module."""

    def test_platform_library_assigns_independent_reviewer(self):
        assigner = PersonaAssigner()  # real cards + real mapping delegation
        persona = assigner.assign_reviewer(
            "platform", "coder", "security review of the tenant isolation change (authnz)"
        )
        assert persona.posture == "reviewer"
        assert persona.id != "coder"
        assigner.assert_reviewer_distinct("platform", "coder", persona)

    def test_platform_library_assigns_independent_auditor(self):
        assigner = PersonaAssigner()
        auditor = assigner.assign_auditor(
            "platform", "coder", "audit the merge evidence of the security change"
        )
        assert auditor.posture == "auditor"
        assert auditor.id == "auditor"
        assigner.assert_auditor_not_executor(auditor, "platform", "coder")

    def test_mirror_and_real_mapping_agree(self, mini_cards):
        """The offline mirror reproduces the real issue #11 assignment."""
        mirror = PersonaAssigner(cards=mini_cards, use_real_mapping=False)
        persona = mirror.assign_reviewer(
            "platform", "coder", "security review of the tenant isolation change"
        )
        assert persona.id == "security-sme"
        # mirror + real module agree over the same library when importable
        real = PersonaAssigner(cards=mini_cards, use_real_mapping=True)
        real_persona = real.assign_reviewer(
            "platform", "coder", "security review of the tenant isolation change"
        )
        assert real_persona.id == persona.id
