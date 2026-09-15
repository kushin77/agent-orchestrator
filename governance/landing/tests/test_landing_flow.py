"""The landing flow: order, refusal, idempotence, dry run (#764).

Every acceptance criterion of issue #764 that is about behaviour is pinned here
against a recording port — so "it refuses", "it lands in this order", "a second
run is a no-op" and "a dry run changes nothing" are measurements, not claims.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import HEAD, PARENT, FakeOps, write_attestation

from governance.landing import evidence as evidence_mod
from governance.landing.engine import LandingEngine, describe
from governance.landing.ports import PullRequest, RecordingOps


def _green(tmp_path, **overrides) -> FakeOps:
    write_attestation(tmp_path / evidence_mod.ATTESTATION_REL, commit=HEAD)
    return FakeOps(root=tmp_path, **overrides)


class TestTheRefusal:
    """A lane without green, commit-named evidence is refused — before any write."""

    @pytest.mark.parametrize(
        "result,rc,commit,expected",
        [
            ("NOT-OK", 1, HEAD, evidence_mod.GAP_NOT_GREEN),
            ("PASS", 0, None, evidence_mod.GAP_UNNAMED_COMMIT),
        ],
    )
    def test_invalid_evidence_is_refused_in_both_modes(self, tmp_path, request_factory, result, rc, commit, expected):
        write_attestation(tmp_path / evidence_mod.ATTESTATION_REL, result=result, rc=rc, commit=commit)
        for apply in (False, True):
            ops = FakeOps(root=tmp_path)
            landing = LandingEngine(ops, request_factory(apply=apply)).land()
            assert landing.rc == 1, f"apply={apply} did not refuse"
            assert landing.refusal_code == expected
            assert ops.calls == [], f"apply={apply} performed a write before refusing: {ops.calls}"

    def test_a_stale_attestation_refuses_in_dry_run_and_re_gates_in_apply(self, tmp_path, request_factory):
        """Green-but-old evidence: unassessable as a plan, re-attested before a merge."""
        write_attestation(tmp_path / evidence_mod.ATTESTATION_REL, commit=PARENT)
        dry_ops = FakeOps(root=tmp_path)
        dry = LandingEngine(dry_ops, request_factory(apply=False)).land()
        assert dry.rc == 1 and dry.refusal_code == evidence_mod.GAP_OTHER_COMMIT
        assert dry_ops.calls == []

        ops = FakeOps(root=tmp_path)
        applied = LandingEngine(ops, request_factory(apply=True)).land()
        assert applied.rc == 0
        assert ("contract", "11") in ops.calls, "apply re-runs the contract, which re-attests the head"
        assert ("merge", "11") in ops.calls
        assert applied.verdict["verify_commit"] == HEAD

    def test_a_missing_attestation_is_cannot_assess_in_dry_run(self, tmp_path, request_factory):
        ops = FakeOps(root=tmp_path)
        landing = LandingEngine(ops, request_factory(apply=False)).land()
        assert landing.rc == 2 and landing.refusal_code == evidence_mod.GAP_ABSENT
        assert ops.calls == []

    def test_a_blocked_merge_verdict_is_refused_even_with_green_evidence(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        landing = LandingEngine(ops, request_factory(apply=True, owner_carve_out=False)).land()
        assert landing.rc == 1
        assert landing.refusal_code == "merge-verdict-blocked"
        assert "self-merge-without-owner-carve-out" in landing.refusal
        assert ops.calls == []

    def test_a_closed_pull_request_is_refused_rather_than_reopened(self, tmp_path, request_factory):
        ops = _green(tmp_path, pr=PullRequest(number=9, state="CLOSED"))
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 1 and landing.refusal_code == "pull-request-not-open"
        assert ops.calls == []


class TestTheOrder:
    """push -> PR -> contract -> merge -> branch delete -> closure, and nothing else."""

    def test_apply_performs_the_seven_steps_in_order(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 0, landing.refusal or describe(landing)
        assert ops.calls == [
            ("push", "issue-764"),
            ("open-pr", "issue-764"),
            ("contract", "11"),
            ("merge", "11"),
            ("delete-branch", "issue-764"),
            ("lifecycle-close", "764"),
        ]
        assert landing.granted and landing.merge_commit == "c" * 40

    def test_the_contract_runs_at_the_pr_boundary(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        LandingEngine(ops, request_factory(apply=True)).land()
        assert ("contract", "11") in ops.calls
        contract_index = ops.calls.index(("contract", "11"))
        assert ops.calls.index(("open-pr", "issue-764")) < contract_index
        assert contract_index < ops.calls.index(("merge", "11"))

    def test_an_already_published_branch_is_not_pushed_again(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        ops.pushed = True
        LandingEngine(ops, request_factory(apply=True)).land()
        assert not any(call[0] == "push" for call in ops.calls)

    def test_an_open_pull_request_is_reused_not_duplicated(self, tmp_path, request_factory):
        ops = _green(tmp_path, pr=PullRequest(number=42, state="OPEN", head=HEAD))
        LandingEngine(ops, request_factory(apply=True)).land()
        assert not any(call[0] == "open-pr" for call in ops.calls)
        assert ("merge", "42") in ops.calls


class TestTheMergeBoundaryRecheck:
    """The evidence is re-read after the contract, against the PR's own head."""

    def test_a_contract_that_attests_another_commit_never_merges(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        ops._contract_commit = PARENT
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 1 and landing.refusal_code == evidence_mod.GAP_OTHER_COMMIT
        assert not any(call[0] == "merge" for call in ops.calls)
        assert ("open-pr", "issue-764") in ops.calls, "the refusal is reported after the push and the PR"

    def test_a_contract_that_attests_a_red_gate_never_merges(self, tmp_path, request_factory):
        ops = _green(tmp_path, contract_rc=1, contract_output="MERGE-GATE: NOT-OK")
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 1 and landing.refusal_code == "pre-merge-contract-failed"
        assert not any(call[0] == "merge" for call in ops.calls)

    def test_a_contract_that_cannot_assess_never_merges_and_says_so(self, tmp_path, request_factory):
        ops = _green(tmp_path, contract_rc=2, contract_output="MERGE-GATE: CANNOT-ASSESS")
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 2
        assert not any(call[0] == "merge" for call in ops.calls)


class TestIdempotence:
    """A landed lane is terminal: no push, no second PR, no second merge."""

    def test_a_merged_lane_is_a_no_op_in_both_modes(self, tmp_path, request_factory):
        merged = PullRequest(number=11, state="MERGED", head=HEAD, merge_commit="c" * 40)
        for apply in (False, True):
            ops = FakeOps(root=tmp_path, pr=merged)
            landing = LandingEngine(ops, request_factory(apply=apply)).land()
            assert landing.rc == 0, f"apply={apply} reported an error for a terminal lane"
            assert landing.terminal and landing.merge_commit == "c" * 40
            assert [call for call in ops.calls if call[0] != "lifecycle-close"] == []
            assert "TERMINAL" in describe(landing)

    def test_a_terminal_lane_does_not_need_evidence_to_be_re_judged(self, tmp_path, request_factory):
        """The attestation is for the tree being merged; it is not re-demanded after."""
        merged = PullRequest(number=11, state="MERGED", head=HEAD, merge_commit="c" * 40)
        ops = FakeOps(root=tmp_path, pr=merged)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 0
        assert landing.verdict is None, "a terminal lane is not re-litigated"


class TestDryRun:
    """The default performs no write at all — and says what it would do."""

    def test_a_grantable_lane_plans_and_changes_nothing(self, tmp_path, request_factory):
        real = _green(tmp_path)
        recording = RecordingOps(reads=real)
        landing = LandingEngine(recording, request_factory(apply=False)).land()
        assert landing.rc == 0 and landing.granted and landing.dry_run
        assert real.calls == [], "a dry run performed a write"
        assert recording.planned == [], "a dry run did not even ask the ops layer to write"
        assert [step.action for step in landing.steps] == [
            "push",
            "open-pr",
            "contract",
            "merge",
            "delete-branch",
            "lifecycle-close",
        ]
        assert all(step.outcome == "planned" for step in landing.steps)
        report = describe(landing)
        assert "DRY RUN" in report and "no remote change" in report

    def test_a_dry_run_writes_no_file(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        before = {path for path in tmp_path.rglob("*")}
        LandingEngine(RecordingOps(reads=ops), request_factory(apply=False)).land()
        assert {path for path in tmp_path.rglob("*")} == before

    def test_an_apply_writes_its_record_and_its_pr_body(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert (tmp_path / ".verify" / "landing-764.json").is_file()
        assert Path(landing.report_path).is_file()
        assert "Closes #764" in ops.body


class TestThePrBody:
    """The body is written in the shape ``scripts/check-pr-contract.sh`` enforces."""

    def test_the_body_declares_closes_ai_assistance_and_red_evidence(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        LandingEngine(ops, request_factory(apply=True)).land()
        assert re.search(r"^Closes\s+#764$", ops.body, re.M)
        assert re.search(r"^AI-assistance:\s*[^<\s][^(]*\([^)]*\)\s*$", ops.body, re.M)
        assert re.search(r"^##\s+Pre-existing red\s*$", ops.body, re.M)
        assert re.search(r"^None\b", ops.body.split("## Pre-existing red")[1].strip(), re.M)

    def test_the_body_quotes_the_attestation_it_is_based_on(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        LandingEngine(ops, request_factory(apply=True)).land()
        assert HEAD in ops.body
        assert "merge-attestation.json" in ops.body
        assert '"result": "PASS"' in ops.body


class TestTheReport:
    def test_the_report_quotes_the_verdict_and_every_step(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        report = describe(landing)
        assert "governance/merge" in report
        assert landing.verdict["mergeable"] is True
        assert "MERGED" in report
        for action in ("push", "open-pr", "contract", "merge", "delete-branch", "lifecycle-close"):
            assert action in report

    def test_a_non_terminal_closure_is_reported_rather_than_claimed(self, tmp_path, request_factory):
        ops = _green(tmp_path, closure_rc=1)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 1
        assert landing.lifecycle_rc == 1
        assert "closure is NOT terminal" in describe(landing)

    def test_a_closure_that_cannot_assess_is_not_a_pass(self, tmp_path, request_factory):
        ops = _green(tmp_path, closure_rc=2)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 2
