"""Close-out: dependency order, idempotence, and no success by assertion.

The load-bearing behaviours are that a step which cannot be completed is reported
rather than swallowed, that the remaining steps still run, that re-running changes
nothing, and that ``ok`` reflects the *re-derived* invariants — not the fact that
the operations did not raise.
"""

from __future__ import annotations

from governance.lifecycle.closeout import PERFORMED, SKIPPED, closeout, describe

from conftest import FakeOps, HEAD_COMMIT, MERGE_COMMIT, clean_item  # noqa: E402


def test_a_hygienic_item_performs_nothing():
    item = clean_item()
    result = closeout(item, FakeOps(item))
    assert result.ok
    assert result.steps
    assert all(step.outcome == SKIPPED for step in result.steps)
    assert result.failed_steps == []


def test_an_open_item_is_not_closed_out():
    item = clean_item(state="open", labels=[], milestone="M26", pr={}, verify={})
    result = closeout(item, FakeOps(item))
    assert not result.ok
    assert result.steps[0].action == "inspect"
    assert "still open" in result.steps[0].detail


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
    assert ops.calls == ["close-issue"]


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
    assert len(result.steps) == 7
    assert all(step.outcome == PERFORMED for step in result.steps)


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
