"""Close-out's reclaim is *gated*, not merely ordered last (#786).

The wedge this pins, measured three times on the lanes of epic #616 (#622, #623,
#626) and again on #793:

    record-verification measures the lane worktree
    reclaim-lane removes the lane worktree

Nothing enforced the order between them, and it is irreversible. A park, or a failure,
left the attestation unrecorded; step 8 removed the tree anyway; and from then on the
invariant could never be satisfied — ``REMAINS VERIFY_EVIDENCE_MISSING`` forever, on
work that *was* verified, with a remediation ("run make verify on the branch head")
that no longer had a branch to run it on. A control that cannot succeed is a
formality; this one inverted.

Two claims are pinned here, and each has a negative control:

* the lane is **kept** while the item still owes its verification, refused by name;
* the lane is reclaimed when nothing depends on it — including when a *different*
  step failed, so the gate is not simply "any failure keeps every lane";
* a lane another process left behind is still charged ``LANE_NOT_RECLAIMED``: the
  retirement is the driver's own refusal, never a blanket excuse.
"""

from __future__ import annotations

from governance.lifecycle.closeout import (
    CANNOT_ASSESS,
    NOT_OK,
    OK,
    PERFORMED,
    REFUSED,
    SKIPPED,
    closeout,
    describe,
)

from conftest import FakeOps, clean_item, parked_verification

LANE = {"session_id": "s-269", "present": True}


def _step(result, action: str):
    return next(step for step in result.steps if step.action == action)


def test_the_lane_is_not_reclaimed_while_the_verification_is_owed():
    """The wedge itself: a failed verification must not take the tree with it."""
    item = clean_item(verify={}, lane=LANE)
    ops = FakeOps(item, fail=("record-verification",))
    result = closeout(item, ops)

    assert _step(result, "record-verification").outcome == "failed"
    assert _step(result, "reclaim-lane").outcome == REFUSED
    assert "reclaim-lane" not in ops.calls
    assert "VERIFY_EVIDENCE_MISSING" in {finding.code for finding in result.remaining}


def test_the_withheld_lane_is_reported_and_the_harmful_remediation_is_not():
    """A kept lane is not the LANE_NOT_RECLAIMED finding, whose fix is the defect.

    ``LANE_NOT_RECLAIMED``'s remediation is "close the lane, committing or discarding
    its work first" — in this state that destroys the tree the missing evidence has to
    come from. Reporting it would instruct the operator to perform the wedge. The
    refusal is therefore printed as a step and named in ``withheld``; nothing is
    hidden, and the finding that remains is the root cause.
    """
    item = clean_item(verify={}, lane=LANE)
    result = closeout(item, FakeOps(item, fail=("record-verification",)))

    assert {finding.code for finding in result.remaining} == {"VERIFY_EVIDENCE_MISSING"}
    assert len(result.withheld) == 1
    assert "record-verification" in result.withheld[0]
    text = describe(result)
    assert "WITHHELD" in text
    assert "close-out #269: NOT-OK" in text
    assert "committing or discarding its work first" not in text


def test_a_parked_verification_keeps_the_lane_and_still_reads_cannot_assess():
    """#840's verdict must not regress: a park measures nothing, so it is not NOT-OK.

    The withheld lane would otherwise surface as ``LANE_NOT_RECLAIMED`` and turn the
    whole item NOT-OK — a capacity condition reported as a broken invariant, which is
    exactly what #840 removed.
    """
    item = clean_item(verify={}, lane=LANE)
    result = closeout(item, FakeOps(item, cannot_assess={"record-verification": parked_verification(11)}))

    assert result.verdict == CANNOT_ASSESS
    assert not result.ok and not result.remaining
    assert _step(result, "reclaim-lane").outcome == REFUSED
    assert "PARKED" in describe(result)


def test_the_retry_the_design_assumed_now_finishes_the_job():
    """The second pass is reachable, because the first pass kept what it needs.

    ``closeout`` has always said the worktree is left "for the re-run that finishes
    the job", and until #786 that was false: the re-run had no tree to re-run in.
    """
    item = clean_item(verify={}, lane=LANE)
    first = closeout(item, FakeOps(item, cannot_assess={"record-verification": parked_verification(11)}))
    assert first.verdict == CANNOT_ASSESS
    assert item["lane"] == LANE, "the lane must survive the parked pass"

    retry = FakeOps(item)
    second = closeout(item, retry)
    assert second.ok
    assert retry.calls == ["record-verification", "reclaim-lane"]
    assert _step(second, "reclaim-lane").outcome == PERFORMED


def test_a_lane_is_reclaimed_when_a_different_step_failed():
    """The gate is the *verification*, not "any failure": an unrelated failure must
    not strand a lane whose evidence is already on record."""
    item = clean_item(branch_deleted=False, lane=LANE)
    ops = FakeOps(item, fail=("delete-branch",))
    result = closeout(item, ops)

    assert "reclaim-lane" in ops.calls
    assert _step(result, "reclaim-lane").outcome == PERFORMED
    assert "BRANCH_NOT_DELETED" in {finding.code for finding in result.remaining}


def test_a_lane_that_merely_failed_to_reclaim_is_still_charged():
    """Negative control for the retirement: a lane the driver *tried* to remove and
    could not is a real finding, and must keep its own remediation."""
    item = clean_item(lane=LANE)
    ops = FakeOps(item, fail=("reclaim-lane",))
    result = closeout(item, ops)

    assert _step(result, "reclaim-lane").outcome == "failed"
    assert result.withheld == []
    assert "LANE_NOT_RECLAIMED" in {finding.code for finding in result.remaining}
    assert "committing or discarding its work first" in describe(result)


def test_a_clean_item_still_skips_the_reclaim_as_already_satisfied():
    """The eight-step report is unchanged where nothing is owed."""
    item = clean_item()
    result = closeout(item, FakeOps(item))

    assert result.ok
    assert len(result.steps) == 8
    assert all(step.outcome == SKIPPED for step in result.steps)
    assert result.withheld == []


def test_the_verification_is_recorded_before_the_lane_can_go():
    """The order the fix depends on, asserted on the calls rather than the steps."""
    item = clean_item(verify={}, lane=LANE)
    ops = FakeOps(item)
    closeout(item, ops)

    assert ops.calls == ["record-verification", "reclaim-lane"]
    assert ops.calls.index("record-verification") < ops.calls.index("reclaim-lane")


def test_an_open_issue_with_a_parked_gate_keeps_its_lane_too():
    """The gate is on the artifact, not the issue's state."""
    item = clean_item(state="open", closing_evidence=False, verify={}, lane=LANE)
    result = closeout(item, FakeOps(item, cannot_assess={"record-verification": parked_verification(10)}))

    assert _step(result, "reclaim-lane").outcome == REFUSED
    assert result.verdict not in (OK, NOT_OK)
    assert "rc 10" in describe(result)
