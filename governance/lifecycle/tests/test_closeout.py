"""Close-out: dependency order, idempotence, and no success by assertion.

The load-bearing behaviours are that a step which cannot be completed is reported
rather than swallowed, that the remaining steps still run, that re-running changes
nothing, and that ``ok`` reflects the *re-derived* invariants — not the fact that
the operations did not raise.

Since #840 there is a third way a step can end: it can be **unassessed** — the gate
was parked at its box-wide permit cap, its permit store was unusable, or it was
killed by a signal — and that is neither a pass nor a failure. The verdicts are
therefore a tri-state, and the tests below pin each of the four demanded cases: a
park, a genuine failure, a free permit, and an exhausted retry.
"""

from __future__ import annotations

from pathlib import Path

from governance.lifecycle.closeout import (
    CANNOT_ASSESS,
    NOT_OK,
    OK,
    PERFORMED,
    SKIPPED,
    closeout,
    describe,
)

import importlib.util as _importlib_util  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_lifecycle_tests_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
HEAD_COMMIT = _conftest.HEAD_COMMIT
MERGE_COMMIT = _conftest.MERGE_COMMIT
FakeOps = _conftest.FakeOps
clean_item = _conftest.clean_item
parked_verification = _conftest.parked_verification


def test_a_hygienic_item_performs_nothing():
    item = clean_item()
    result = closeout(item, FakeOps(item))
    assert result.ok
    assert len(result.steps) == 8
    assert all(step.outcome == SKIPPED for step in result.steps)
    assert result.failed_steps == []


def test_work_in_flight_is_not_closed_out():
    item = clean_item(state="open", pr={}, verify={}, labels=[], milestone="M26")
    result = closeout(item, FakeOps(item))
    assert not result.ok
    assert result.steps[0].action == "inspect"
    assert "has not landed" in result.steps[0].detail


def test_an_open_issue_whose_change_landed_IS_closed_out():
    """The dogfood bug: close-out must finish an item, not skip it for being open.

    Both the evidence and the close are owed here, and each is its own idempotent
    action - fusing them made an already-closed issue look like a failed step.
    """
    item = clean_item(state="open", closing_evidence=False)
    ops = FakeOps(item)
    result = closeout(item, ops)
    assert ops.calls == ["record-closing-evidence", "close-issue"]
    assert result.ok
    assert item["state"] == "closed"


def test_a_surviving_branch_is_deleted():
    item = clean_item(branch_deleted=False)
    ops = FakeOps(item)
    result = closeout(item, ops)
    assert ops.calls == ["delete-branch"]
    assert result.ok


def test_a_live_claim_is_released():
    item = clean_item(claim={"agent": "subagent-dead", "live": True})
    ops = FakeOps(item)
    assert closeout(item, ops).ok
    assert ops.calls == ["release-claim"]


def test_a_pending_directive_is_consumed():
    item = clean_item(directive={"id": "d-269", "state": "sent"})
    ops = FakeOps(item)
    assert closeout(item, ops).ok
    assert ops.calls == ["consume-directive"]


def test_a_provisioned_lane_is_reclaimed():
    item = clean_item(lane={"session_id": "s-269", "present": True})
    ops = FakeOps(item)
    assert closeout(item, ops).ok
    assert ops.calls == ["reclaim-lane"]


def test_missing_evidence_is_recorded_against_the_verified_head():
    item = clean_item(verify={})
    ops = FakeOps(item)
    closeout(item, ops)
    assert ops.calls == ["record-verification"]
    assert item["verify"] == {"ok": True, "commit": HEAD_COMMIT}


def test_a_close_without_evidence_is_repaired():
    item = clean_item(closing_evidence=False)
    ops = FakeOps(item)
    assert closeout(item, ops).ok
    assert ops.calls == ["record-closing-evidence"]


def test_the_directive_is_consumed_before_the_claim_is_released():
    """Ordering is load-bearing: a pending directive re-executes on claim release."""
    item = clean_item(
        claim={"agent": "subagent-dead", "live": True},
        directive={"id": "d-269", "state": "sent"},
    )
    ops = FakeOps(item)
    closeout(item, ops)
    assert ops.calls.index("consume-directive") < ops.calls.index("release-claim")


def test_the_merge_precedes_the_evidence_that_names_it():
    item = clean_item(pr={"number": 271, "state": "open", "branch": "issue-269", "head_commit": HEAD_COMMIT}, verify={})
    ops = FakeOps(item)
    closeout(item, ops)
    assert ops.calls.index("merge-pull-request") < ops.calls.index("record-verification")


def test_the_lane_is_reclaimed_last():
    """A failure anywhere earlier must leave the worktree for the re-run."""
    item = clean_item(
        branch_deleted=False,
        lane={"session_id": "s-269", "present": True},
    )
    ops = FakeOps(item)
    closeout(item, ops)
    assert ops.calls[-1] == "reclaim-lane"


def test_a_broken_item_is_fully_repaired_in_one_pass():
    """Every artifact broken at once: one pass drives them all to terminal.

    ``close-issue`` is correctly *skipped* here rather than performed: one item
    cannot both have an unmerged pull request and a landed change, so an item that
    provokes PR_NOT_MERGED is necessarily already closed.
    """
    item = clean_item(
        pr={"number": 271, "state": "open", "branch": "issue-269", "head_commit": HEAD_COMMIT},
        verify={},
        branch_deleted=False,
        claim={"agent": "subagent-dead", "live": True},
        directive={"id": "d-269", "state": "sent"},
        lane={"session_id": "s-269", "present": True},
        closing_evidence=False,
    )
    result = closeout(item, FakeOps(item))
    assert result.ok
    assert [step.action for step in result.steps] == [
        "merge-pull-request",
        "record-verification",
        "delete-branch",
        "consume-directive",
        "release-claim",
        "record-closing-evidence",
        "close-issue",
        "reclaim-lane",
    ]
    performed = [step.action for step in result.steps if step.outcome == PERFORMED]
    assert "close-issue" not in performed
    assert len(performed) == 7


def test_close_out_always_reports_the_same_eight_steps():
    """A uniform step set, whether or not each step had anything to do."""
    broken = clean_item(branch_deleted=False)
    clean = clean_item()
    actions = [step.action for step in closeout(broken, FakeOps(broken)).steps]
    assert len(actions) == 8
    assert actions == [step.action for step in closeout(clean, FakeOps(clean)).steps]


def test_the_final_audit_reads_the_refreshed_item_not_the_stale_one():
    """The real bug: a fully successful close read as NOT-OK because the audit
    re-read the pre-close item while the effects happened on GitHub."""

    class ExternalOps(FakeOps):
        """Effects happen outside the item (as real GhOps does); refresh supplies truth."""

        def __init__(self, item):
            super().__init__(item)
            self.done = dict(item)

        def delete_branch(self, branch: str) -> str:
            self._record("delete-branch")
            self.done["branch_deleted"] = True
            return "deleted"

        def refresh(self, item: dict) -> dict:
            return self.done

    stale = clean_item(branch_deleted=False)
    result = closeout(stale, ExternalOps(stale))
    assert result.ok
    assert result.remaining == []


def test_a_failing_step_is_reported_and_the_rest_still_run():
    item = clean_item(branch_deleted=False, lane={"session_id": "s-269", "present": True})
    ops = FakeOps(item, fail=("delete-branch",))
    result = closeout(item, ops)
    assert not result.ok
    assert [step.action for step in result.failed_steps] == ["delete-branch"]
    assert "reclaim-lane" in ops.calls  # the later steps were not abandoned
    assert "BRANCH_NOT_DELETED" in {finding.code for finding in result.remaining}


def test_success_is_never_asserted_when_the_item_still_shows_the_violation():
    """An ops port that claims success without acting must not produce a pass."""
    class LyingOps(FakeOps):
        def delete_branch(self, branch: str) -> str:
            self._record("delete-branch")
            return "deleted"  # reports success; the item is left broken

    item = clean_item(branch_deleted=False)
    result = closeout(item, LyingOps(item))
    assert not result.ok
    assert "BRANCH_NOT_DELETED" in {finding.code for finding in result.remaining}


def test_close_out_is_idempotent():
    item = clean_item(branch_deleted=False, lane={"session_id": "s-269", "present": True})
    first = closeout(item, FakeOps(item))
    assert first.ok
    second = closeout(item, FakeOps(item))
    assert second.ok
    assert all(step.outcome == SKIPPED for step in second.steps)


def test_the_description_shows_what_remains_and_how_to_clear_it():
    item = clean_item(branch_deleted=False)
    result = closeout(item, FakeOps(item, fail=("delete-branch",)))
    text = describe(result)
    assert "close-out #269: NOT-OK" in text
    assert "REMAINS" in text
    assert "git push origin --delete" in text


def test_an_already_merged_pull_request_is_not_merged_again():
    ops = FakeOps(clean_item())
    closeout(clean_item(), ops)
    assert "merge-pull-request" not in ops.calls
    assert MERGE_COMMIT  # documented: the merge commit is provenance, not evidence


# --- the gate's admission control, read by the consumer (#840) ---------------


def test_a_parked_verification_is_cannot_assess_not_missing_evidence():
    """Demanded case 1: PARKED ⇒ CANNOT-ASSESS naming PARKED, never "missing".

    The defect: a park (rc 10/11) means the gate ran nothing, so the verification
    is *unmeasured*. Reporting it as ``VERIFY_EVIDENCE_MISSING`` asserts that
    evidence was measured and came back absent — a fact nobody measured — and
    files it on the board as a defect generated by capacity.
    """
    item = clean_item(verify={})
    ops = FakeOps(item, cannot_assess={"record-verification": parked_verification(11)})
    result = closeout(item, ops)

    assert ops.calls == ["record-verification"]
    assert result.cannot_assess and not result.ok
    assert result.verdict == CANNOT_ASSESS
    assert [step.outcome for step in result.unassessed_steps] == ["parked"]
    assert "VERIFY_EVIDENCE_MISSING" not in {finding.code for finding in result.remaining}

    text = describe(result)
    assert "close-out #269: CANNOT-ASSESS" in text
    assert "PARKED" in text
    assert "AO_GATE_MAX_CONCURRENT" in text
    assert "retry when capacity is free" in text
    assert "REMAINS  VERIFY_EVIDENCE_MISSING" not in text
    assert "run `make verify` on the branch head" not in text


def test_a_genuine_gate_failure_still_reports_missing_evidence():
    """Demanded case 2: rc 1 is an answer, so it stays a failure.

    The parked fix must not weaken the gate: a run that happened and failed leaves
    the item without a green attestation, and that is reported as missing evidence.
    """
    item = clean_item(verify={})
    ops = FakeOps(item, fail=("record-verification",))
    result = closeout(item, ops)

    assert not result.ok and not result.cannot_assess
    assert result.verdict == NOT_OK
    assert "VERIFY_EVIDENCE_MISSING" in {finding.code for finding in result.remaining}
    assert result.not_assessed == []
    assert "close-out #269: NOT-OK" in describe(result)


def test_a_free_permit_records_the_attestation_and_reaches_ok():
    """Demanded case 3: with capacity free the item still closes out green."""
    item = clean_item(verify={})
    ops = FakeOps(item)
    result = closeout(item, ops)

    assert ops.calls == ["record-verification"]
    assert item["verify"] == {"ok": True, "commit": HEAD_COMMIT}
    assert result.verdict == OK and result.ok
    assert result.not_assessed == []
    assert "close-out #269: OK" in describe(result)


def test_an_exhausted_retry_is_cannot_assess_with_the_retry_visible():
    """Demanded case 4: the bounded retry must be recorded, never silent."""
    item = clean_item(verify={})
    ops = FakeOps(
        item,
        cannot_assess={"record-verification": parked_verification(11, retries=1, wait=2.5)},
    )
    result = closeout(item, ops)

    assert result.cannot_assess
    assert ops.calls == ["record-verification"]  # one step, two attempts inside it
    text = describe(result)
    assert "2 attempt(s)" in text
    assert "2.5s" in text
    assert "PARKED" in text


def test_a_parked_verification_never_becomes_a_pass():
    """Nothing was measured, so the verdict may be neither OK nor NOT-OK."""
    item = clean_item(verify={})
    result = closeout(item, FakeOps(item, cannot_assess={"record-verification": parked_verification(10)}))

    assert not result.ok
    assert result.verdict not in (OK, NOT_OK)
    assert result.remaining == []
    assert "rc 10" in describe(result)


def test_a_recorded_attestation_naming_the_wrong_commit_is_not_retired():
    """Only an *absence* is explained by a gate that never ran.

    An attestation that exists and names the wrong commit is a measured mismatch:
    it stays a finding even though the gate parked, so the fix cannot be used to
    hide a real mismatch behind a capacity condition.
    """
    item = clean_item(verify={"ok": True, "commit": MERGE_COMMIT})
    result = closeout(item, FakeOps(item, cannot_assess={"record-verification": parked_verification(11)}))

    assert result.verdict == NOT_OK
    missing = [finding for finding in result.remaining if finding.code == "VERIFY_EVIDENCE_MISSING"]
    assert missing and "not the verified head commit" in missing[0].detail


def test_an_item_with_no_verified_head_commit_is_not_retired():
    """No head commit is a broken item, not an unmeasured one: the gate cannot fix it."""
    item = clean_item(pr={"number": 271, "state": "merged", "branch": "issue-269"}, verify={})
    result = closeout(item, FakeOps(item, cannot_assess={"record-verification": parked_verification(11)}))

    assert result.verdict == NOT_OK
    assert "VERIFY_EVIDENCE_MISSING" in {finding.code for finding in result.remaining}


def test_an_unassessed_step_does_not_abort_the_steps_that_can_run():
    """A park at step 2 must not stop the branch, claim, evidence and close."""
    item = clean_item(
        verify={},
        branch_deleted=False,
        claim={"agent": "subagent-dead", "live": True},
        closing_evidence=False,
        state="open",
    )
    ops = FakeOps(item, cannot_assess={"record-verification": parked_verification(11)})
    result = closeout(item, ops)

    assert result.cannot_assess
    assert "delete-branch" in ops.calls
    assert "release-claim" in ops.calls
    assert "close-issue" in ops.calls
    assert item["state"] == "closed"


def test_the_parked_verdict_is_reported_before_the_unmeasured_rule():
    """The step that was not assessed and the invariant it left unmeasured are both named."""
    item = clean_item(verify={})
    result = closeout(item, FakeOps(item, cannot_assess={"record-verification": parked_verification(11)}))

    assert [record.action for record in result.not_assessed] == [
        "record-verification",
        "record-verification",
    ]
    codes = [record.code for record in result.not_assessed]
    assert codes == ["", "VERIFY_EVIDENCE_MISSING"]
    assert "unmeasured, not missing" in result.not_assessed[1].detail
    assert PERFORMED not in {step.outcome for step in result.steps}
