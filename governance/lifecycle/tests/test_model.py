"""The invariant vocabulary: closed, complete, and honest about applicability.

A gate can only provoke a violation class it has been taught; so the vocabulary
must be closed (no invented codes), every entry must carry the requirement it
enforces and the remediation that clears it, and applicability must be a
function of state — an open item cannot be charged with a missing merge.
"""

from __future__ import annotations

import pytest

from governance.lifecycle.model import (
    INVARIANTS,
    INVARIANTS_BY_CODE,
    STAGES,
    TERMINAL_STAGE,
    invariant,
    invariants_for,
    stage_of,
)

from conftest import HEAD_COMMIT, clean_item  # noqa: E402  (suite-local helper)


def test_the_stage_vocabulary_is_ordered_and_terminal_is_last():
    assert STAGES[0] == "filed"
    assert STAGES[-1] == TERMINAL_STAGE == "reclaimed"
    assert len(set(STAGES)) == len(STAGES)


def test_every_invariant_declares_a_requirement_and_a_remediation():
    for entry in INVARIANTS:
        assert entry.code == entry.code.upper()
        assert len(entry.requires) > 20, entry.code
        assert len(entry.remediation) > 20, entry.code


def test_invariant_codes_are_unique():
    codes = [entry.code for entry in INVARIANTS]
    assert len(set(codes)) == len(codes)


def test_the_vocabulary_is_closed_and_says_so():
    with pytest.raises(KeyError) as caught:
        invariant("SOMETHING_INVENTED")
    assert "closed" in str(caught.value)


def test_a_closed_item_owes_every_closure_invariant():
    codes = {entry.code for entry in invariants_for("closed")}
    assert "PR_NOT_MERGED" in codes
    assert "LANE_NOT_RECLAIMED" in codes
    assert "FILING_LABELS_MISSING" not in codes  # it is closed; filing no longer applies


def test_an_open_item_owes_only_the_filing_rule():
    codes = {entry.code for entry in invariants_for("open")}
    assert codes == {"FILING_LABELS_MISSING"}


def test_stage_of_an_untouched_item_is_filed():
    assert stage_of(clean_item(state="open", claim={}, pr={}, lane={})) == "filed"


def test_stage_of_a_claimed_item_is_claimed():
    assert stage_of(clean_item(state="open", claim={"agent": "copilot-brain", "live": True}, pr={})) == "claimed"


def test_stage_of_a_laned_item_names_the_lane():
    item = clean_item(state="open", lane={"session_id": "s-1", "present": True}, claim={}, pr={})
    assert stage_of(item) == "laned"


def test_stage_of_a_closed_item_whose_branch_survived_is_merged():
    """The measured failure: merged, but the branch outlived the close."""
    assert stage_of(clean_item(branch_deleted=False)) == "merged"


def test_stage_of_a_hygienic_item_is_reclaimed():
    assert stage_of(clean_item()) == TERMINAL_STAGE


def test_stage_of_a_closed_item_with_a_live_claim_is_not_reclaimed():
    assert stage_of(clean_item(claim={"agent": "subagent-dead", "live": True})) == "closed"


def test_the_head_commit_is_what_evidence_names():
    """A squash merge creates a new commit, so the invariant cannot name it."""
    item = clean_item()
    assert item["pr"]["head_commit"] == HEAD_COMMIT
    assert item["pr"]["head_commit"] != item["pr"]["merge_commit"]
    assert INVARIANTS_BY_CODE["VERIFY_EVIDENCE_MISSING"].requires.count("head commit") == 1
