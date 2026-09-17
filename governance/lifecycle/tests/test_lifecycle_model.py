"""The invariant vocabulary: closed, complete, and honest about applicability.

A gate can only provoke a violation class it has been taught; so the vocabulary
must be closed (no invented codes), every entry must carry the requirement it
enforces and the remediation that clears it, and applicability must be a
function of state — an open item cannot be charged with a missing merge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.lifecycle.model import (
    INVARIANTS,
    INVARIANTS_BY_CODE,
    STAGES,
    TERMINAL_STAGE,
    invariant,
    invariants_for,
    owes_closure,
    stage_of,
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


def test_a_landed_change_owes_every_closure_invariant():
    codes = {entry.code for entry in invariants_for(clean_item())}
    assert "PR_NOT_MERGED" in codes
    assert "ISSUE_NOT_CLOSED" in codes
    assert "LANE_NOT_RECLAIMED" in codes
    assert "FILING_LABELS_MISSING" not in codes


def test_a_merged_pull_request_owes_closure_even_though_the_issue_is_open():
    """The bug the first end-to-end run found: closing the issue IS a closure step.

    Keying applicability on ``state == 'closed'`` exempted the very items that
    needed closing, so close-out reported OK on an item it had not closed.
    """
    landed = clean_item(state="open", closing_evidence=False)
    assert owes_closure(landed) is True
    assert "ISSUE_NOT_CLOSED" in {entry.code for entry in invariants_for(landed)}


def test_work_still_in_flight_owes_only_the_filing_rule():
    codes = {entry.code for entry in invariants_for(clean_item(state="open", pr={}, verify={}))}
    assert codes == {"FILING_LABELS_MISSING"}


def test_a_closed_item_that_never_merged_still_owes_the_closure_invariants():
    assert owes_closure(clean_item(pr={"number": 271, "state": "closed"})) is True


def test_github_casing_is_normalised_to_lowercase():
    """The collector returns OPEN/CLOSED/MERGED; the model must read lowercase.

    This is the bug the second end-to-end run found: GitHub's canonical casing
    made ``owes_closure`` false for a closed item whose PR was merged, so close-out
    reported "the change has not landed" on an item it had itself just merged.
    """
    landed = clean_item(state="CLOSED", pr={**clean_item()["pr"], "state": "MERGED"})
    assert owes_closure(landed) is True
    assert owes_closure(clean_item(state="OPEN", pr={**clean_item()["pr"], "state": "MERGED"})) is True
    assert owes_closure(clean_item(state="OPEN", pr={})) is False


def test_stage_of_an_untouched_item_is_filed():
    assert stage_of(clean_item(state="open", claim={}, pr={}, lane={})) == "filed"


def test_stage_of_a_verified_but_unmerged_item_is_verified():
    item = clean_item(pr={"number": 271, "state": "open", "branch": "issue-269", "head_commit": HEAD_COMMIT})
    assert stage_of(item) == "verified"


def test_stage_of_a_landed_but_unclosed_item_is_merged():
    assert stage_of(clean_item(state="open", closing_evidence=False)) == "merged"


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
