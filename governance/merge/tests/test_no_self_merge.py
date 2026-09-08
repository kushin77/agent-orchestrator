"""No-self-merge + owner autonomous-merge carve-out tests (issue #43
acceptance criterion 4).

The fleet doctrine (AGENTS.md, GOLDEN-RULES AO-GR-11, GOVERNANCE.md §3): an
agent merges its own PR only under the owner autonomous-merge carve-out, and
the carve-out requires the green verification evidence to be recorded — it
replaces the human reviewer, never the evidence. A self-merge without the
carve-out, and any merge without green verification, is blocked.
"""

from __future__ import annotations

from engine import MergeGovernanceEngine
from gate import green_gate, red_gate
from model import BlockReason, MergeSignals, merge_verdict
from reviewer import PersonaAssigner


class TestSelfMergePolicy:
    def test_self_merge_without_carve_out_is_blocked(
        self, pr_factory, mini_cards
    ):
        engine = MergeGovernanceEngine(
            gate_check=green_gate(),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=False,
        )
        result = engine.run(
            number=10, title="t", author="coder", subject="a change",
            branch="issue-10", merger="coder", commit="abc",
        )
        assert result.mergeable is False
        assert result.block_reason == BlockReason.SELF_MERGE_WITHOUT_CARVE_OUT.value

    def test_self_merge_with_carve_out_and_green_verify_is_mergeable(
        self, pr_factory, mini_cards
    ):
        # owner autonomous-merge mandate: verification evidence replaces the
        # human reviewer, so an authorized self-merge with a green gate +
        # independent review is permitted
        engine = MergeGovernanceEngine(
            gate_check=green_gate(),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=True,
        )
        result = engine.run(
            number=11, title="t", author="coder", subject="a change",
            branch="issue-11", merger="coder", commit="abc",
        )
        assert result.mergeable is True
        assert result.state.value == "mergeable"

    def test_non_author_merge_without_carve_out_is_mergeable(
        self, pr_factory, mini_cards
    ):
        # a distinct integrator (independent of the author) needs no carve-out
        engine = MergeGovernanceEngine(
            gate_check=green_gate(),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=False,
        )
        result = engine.run(
            number=12, title="t", author="coder", subject="a change",
            branch="issue-12", merger="integrator", commit="abc",
        )
        assert result.mergeable is True

    def test_carve_out_never_replaces_the_evidence(self, mini_cards):
        # self-merge + carve-out with a RED gate must still be blocked:
        # the carve-out replaces the human reviewer, never the evidence
        engine = MergeGovernanceEngine(
            gate_check=red_gate("verify failed"),
            reviewer_assigner=PersonaAssigner(cards=mini_cards, use_real_mapping=False),
            owner_carve_out=True,
        )
        result = engine.run(
            number=13, title="t", author="coder", subject="a change",
            branch="issue-13", merger="coder", commit="abc",
        )
        assert result.mergeable is False
        assert result.block_reason == BlockReason.VERIFY_NOT_GREEN.value

    def test_policy_function_encodes_the_carve_out_rule(self):
        # no-self-merge is the rule; the owner carve-out is the only exception
        base = dict(
            verify_green=True,
            reviewer_assigned=True,
            reviewer_distinct=True,
            reviewer_approved=True,
            author="coder",
        )
        permitted, reasons = merge_verdict(
            MergeSignals(**base, merger="coder", owner_carve_out=False)
        )
        assert permitted is False
        assert BlockReason.SELF_MERGE_WITHOUT_CARVE_OUT.value in reasons

        permitted, reasons = merge_verdict(
            MergeSignals(**base, merger="coder", owner_carve_out=True)
        )
        assert permitted is True
        assert reasons == []

        permitted, reasons = merge_verdict(
            MergeSignals(**base, merger="integrator", owner_carve_out=False)
        )
        assert permitted is True
