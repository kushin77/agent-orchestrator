"""The landing flow: order, refusal, idempotence, dry run (#764).

Every acceptance criterion of issue #764 that is about behaviour is pinned here
against a recording port — so "it refuses", "it lands in this order", "a second
run is a no-op" and "a dry run changes nothing" are measurements, not claims.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_landing_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
HEAD = _conftest.HEAD
PARENT = _conftest.PARENT
FakeOps = _conftest.FakeOps
write_attestation = _conftest.write_attestation

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
        assert [call[0] for call in ops.calls] == [
            "push",
            "open-pr",
            "contract",
            "gate-status",
            "landed-contract",
            "merge",
            "delete-branch",
            "lifecycle-close",
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


class TestTheLandedContractPrecondition:
    """The commits to be squashed must carry the trailing ticket trailer (#998).

    The repository sets ``squash_merge_commit_message=COMMIT_MESSAGES``, so the
    landed commit body is composed from the branch commit messages, not the PR
    body. A PR whose body is contract-perfect still lands trailer-less if its
    commit message lacks the trailer — so the merge is refused by name on the
    artifact that lands, before it lands.
    """

    def test_a_commit_message_lacking_the_trailer_is_refused_by_name(self, tmp_path, request_factory):
        """Provocation (1): a perfect PR body does not excuse a trailer-less commit."""
        ops = _green(
            tmp_path,
            landed_contract_rc=1,
            landed_contract_output=(
                "check-pr-contract: LANDED\n"
                "  FAIL  commit-missing-ticket-trailer:0123456789ab"
            ),
        )
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 1
        assert landing.refusal_code == "landed-contract-blocked"
        assert not any(call[0] == "merge" for call in ops.calls)
        # The PR body is contract-perfect — the refusal names the commit-message
        # finding, never the PR body.
        assert "Closes #764" in ops.body
        assert "AI-assistance:" in ops.body
        assert any("commit-missing-ticket-trailer" in step.detail for step in landing.steps)

    def test_a_trailer_less_pr_body_is_refused_before_the_merge(self, tmp_path, request_factory):
        """Provocation (2): a PR body that fails the contract is refused by name."""
        ops = _green(
            tmp_path,
            contract_rc=1,
            contract_output="merge-gate: NOT-OK\n  FAIL  pr-body-missing-ai-assistance",
        )
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 1
        assert landing.refusal_code == "pre-merge-contract-failed"
        assert not any(call[0] == "merge" for call in ops.calls)
        assert any("pr-body-missing-ai-assistance" in step.detail for step in landing.steps)

    def test_a_landed_contract_that_cannot_assess_never_merges(self, tmp_path, request_factory):
        """CANNOT-ASSESS is never a pass: the tri-state binds at the merge boundary."""
        ops = _green(
            tmp_path,
            landed_contract_rc=2,
            landed_contract_output="check-pr-contract: CANNOT-ASSESS — no commits in landed range",
        )
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 2
        assert landing.refusal_code == "landed-contract-blocked"
        assert not any(call[0] == "merge" for call in ops.calls)

    def test_a_well_formed_branch_still_lands(self, tmp_path, request_factory):
        """Negative control: a green contract and a green landed contract still merge."""
        ops = _green(tmp_path)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 0 and landing.granted
        assert ("merge", "11") in ops.calls
        assert landing.merge_commit == "c" * 40
        # The explicit squash message carries the trailing trailer that lands.
        assert "Refs kushin77/agent-orchestrator#764" in ops.squash_body
        assert "Closes #764" in ops.squash_body

    def test_the_landed_contract_runs_at_the_merge_boundary(self, tmp_path, request_factory):
        """The precondition sits after the contract and before the merge."""
        ops = _green(tmp_path)
        LandingEngine(ops, request_factory(apply=True)).land()
        landed = ops.calls.index(("landed-contract", "master.." + "a" * 40))
        assert ops.calls.index(("contract", "11")) < landed
        assert landed < ops.calls.index(("merge", "11"))


class TestTheGateStatus:
    """The gate-of-record status is posted at the PR boundary (#1072, ADR-0028).

    Posted immediately after the pre-merge contract runs against the PR head
    commit, BEFORE the merge decision, for every contract outcome — so a red
    commit is decorated red rather than left blank. A failed/unreadable poster
    is a named CANNOT-ASSESS refusal (``gate-status-unpublished``): the merge
    is refused and nothing is merged.
    """

    def test_a_green_contract_publishes_success_before_the_merge(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 0 and landing.granted
        assert (HEAD, 0) in ops.published_statuses
        gate_status_index = ops.calls.index(("gate-status", f"{HEAD}:0"))
        contract_index = ops.calls.index(("contract", "11"))
        merge_index = ops.calls.index(("merge", "11"))
        assert contract_index < gate_status_index < merge_index
        assert any(step.action == "gate-status" for step in landing.steps)

    def test_a_red_contract_publishes_failure_and_still_refuses_the_merge(self, tmp_path, request_factory):
        ops = _green(tmp_path, contract_rc=1, contract_output="MERGE-GATE: NOT-OK")
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 1
        assert (HEAD, 1) in ops.published_statuses, "a red contract must still be decorated, never left blank"
        assert not any(call[0] == "merge" for call in ops.calls)

    def test_a_cannot_assess_contract_publishes_error(self, tmp_path, request_factory):
        ops = _green(tmp_path, contract_rc=2, contract_output="MERGE-GATE: CANNOT-ASSESS")
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 2
        assert (HEAD, 2) in ops.published_statuses
        assert not any(call[0] == "merge" for call in ops.calls)

    def test_a_poster_failure_refuses_as_gate_status_unpublished_and_never_merges(self, tmp_path, request_factory):
        ops = _green(tmp_path, publish_status_rc=1)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 2
        assert landing.refusal_code == "gate-status-unpublished"
        assert not any(call[0] == "merge" for call in ops.calls)
        assert not any(call[0] == "landed-contract" for call in ops.calls)

    def test_a_poster_exception_is_also_gate_status_unpublished(self, tmp_path, request_factory):
        from governance.landing.ports import PortError

        ops = _green(tmp_path, publish_status_raises=PortError("gh not found"))
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        assert landing.rc == 2
        assert landing.refusal_code == "gate-status-unpublished"
        assert not any(call[0] == "merge" for call in ops.calls)

    def test_a_dry_run_plans_the_step_and_posts_nothing(self, tmp_path, request_factory):
        real = _green(tmp_path)
        recording = RecordingOps(reads=real)
        landing = LandingEngine(recording, request_factory(apply=False)).land()
        assert landing.rc == 0 and landing.granted
        assert any(step.action == "gate-status" and step.outcome == "planned" for step in landing.steps)
        assert real.published_statuses == [], "a dry run never posts a status"
        assert recording.planned == [], "a dry run does not even ask the ops layer to write"

    def test_the_step_is_recorded_in_the_evidence_output(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        landing = LandingEngine(ops, request_factory(apply=True)).land()
        as_dict = landing.as_dict()
        assert any(step["action"] == "gate-status" for step in as_dict["steps"])


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
            "gate-status",
            "landed-contract",
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


class TestTheMasterAttestationWriter:
    """RCA 2026-09-17 fix #5 (#1114): a successful land publishes master's own
    health, at the exact seam `fleet/brain.py`'s dispatch pre-check reads, so a
    lane that just landed is never followed by dispatch reading a stale (or
    never-written) verdict. Injected so the write is asserted without disk.
    """

    def _recorder(self):
        calls: list = []

        def writer(path, attestation, *, commit):
            calls.append({"path": path, "attestation": attestation, "commit": commit})
            return path

        return calls, writer

    def test_a_successful_land_writes_it_naming_the_squash_commit(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        calls, writer = self._recorder()
        landing = LandingEngine(ops, request_factory(apply=True), master_attestation_writer=writer).land()
        assert landing.rc == 0, landing.refusal or describe(landing)
        assert len(calls) == 1
        assert calls[0]["path"] == tmp_path / evidence_mod.MASTER_ATTESTATION_REL
        # FakeOps.merge_pr always returns "c" * 40 (the squash commit) — the
        # writer must be told THAT sha, not the pre-squash lane head (HEAD).
        assert calls[0]["commit"] == "c" * 40
        assert calls[0]["commit"] != HEAD
        assert calls[0]["attestation"].readable and calls[0]["attestation"].green
        assert ("master-attestation", tmp_path / evidence_mod.MASTER_ATTESTATION_REL) not in ops.calls
        assert any(step.action == "master-attestation" and step.outcome == "performed" for step in landing.steps)

    def test_a_refused_land_never_writes_it(self, tmp_path, request_factory):
        """A red contract refuses before any merge — nothing is published."""
        ops = _green(tmp_path, contract_rc=1, contract_output="MERGE-GATE: NOT-OK")
        calls, writer = self._recorder()
        landing = LandingEngine(ops, request_factory(apply=True), master_attestation_writer=writer).land()
        assert landing.rc != 0
        assert calls == []
        assert not any(step.action == "master-attestation" for step in landing.steps)

    def test_a_dry_run_never_writes_it(self, tmp_path, request_factory):
        ops = _green(tmp_path)
        calls, writer = self._recorder()
        landing = LandingEngine(ops, request_factory(apply=False), master_attestation_writer=writer).land()
        assert calls == []
        assert not any(step.action == "master-attestation" for step in landing.steps)

    def test_a_lane_that_was_behind_master_skips_the_write_and_names_why(self, tmp_path, request_factory):
        """The honesty guard (#1114 follow-up): the attestation measured the
        LANE head, not master's post-merge head — relabelling it is only fair
        when the lane already contained master's pre-merge tip. `FakeOps`'s
        `lane_behind_master=True` makes `merge_base` report no common tip.
        """
        ops = _green(tmp_path, lane_behind_master=True)
        calls, writer = self._recorder()
        landing = LandingEngine(ops, request_factory(apply=True), master_attestation_writer=writer).land()
        assert landing.rc == 0, landing.refusal or describe(landing)
        assert calls == []
        skip_steps = [step for step in landing.steps if step.action == "master-attestation"]
        assert len(skip_steps) == 1
        assert skip_steps[0].outcome == "skipped"
        assert "behind master" in skip_steps[0].detail

    def test_a_lane_that_was_already_at_master_writes_it(self, tmp_path, request_factory):
        """The positive control for the same guard: the default `FakeOps`
        (`lane_behind_master=False`) reports the lane head as already
        containing master's tip, so the write proceeds as in the base case."""
        ops = _green(tmp_path)  # lane_behind_master=False by default
        calls, writer = self._recorder()
        landing = LandingEngine(ops, request_factory(apply=True), master_attestation_writer=writer).land()
        assert landing.rc == 0, landing.refusal or describe(landing)
        assert len(calls) == 1
        assert not any(step.action == "master-attestation" and step.outcome == "skipped" for step in landing.steps)

    def test_the_real_writer_is_atomic_and_reusable_by_read_attestation(self, tmp_path):
        """No injected fake: the production writer really writes a file
        `read_attestation` accepts, and it never leaves a `.tmp-*` file behind.
        """
        source = write_attestation(tmp_path / "source-attestation.json", commit=HEAD)
        attestation = evidence_mod.read_attestation(source)
        target = tmp_path / evidence_mod.MASTER_ATTESTATION_REL
        written = evidence_mod.write_master_attestation(target, attestation, commit="c" * 40)
        assert written == target and target.is_file()
        assert list(target.parent.glob(".*tmp*")) == []
        reread = evidence_mod.read_attestation(target)
        assert reread.readable and reread.green
        assert reread.commit == "c" * 40
