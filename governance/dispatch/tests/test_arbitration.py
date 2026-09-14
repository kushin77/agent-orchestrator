"""A2A dispatch arbitration: prove issue -> epic -> lane ownership (issue #726).

A directive names an issue, but nothing used to bind it to the epic that owns the
work or to the lane that holds it. These tests pin the proof: every refusal names
the evidence it checked, the granted verdict records the provenance, and the
controls that keep the refusals honest can themselves fail.
"""

from __future__ import annotations

import json
from datetime import timedelta

import claims
import order
import pytest
from model import (
    ARBITRATION_REFUSALS,
    REASON_ALREADY_CLAIMED,
    REASON_BLOCKED,
    REASON_EPIC_CLOSED,
    REASON_EPIC_NOT_WORKABLE,
    REASON_ISSUE_CLOSED,
    REASON_NEXT_IN_MILESTONE,
    REASON_PROVENANCE_MISMATCH,
    REASON_SNAPSHOT_STALE,
    REASON_UNKNOWN_ISSUE,
    REASON_UNOWNED,
    ClaimEvent,
    Issue,
    Provenance,
    Snapshot,
)


@pytest.fixture
def board(base_time) -> Snapshot:
    """#701 dispatchable, #702 closed, #703 a child of the open epic #700,
    #705 a child of the closed epic #704, #706 blocked by #707."""
    issues = {
        700: Issue(700, "epic", milestone="A2A", labels=("type:epic",)),
        701: Issue(701, "dispatchable", milestone="A2A"),
        702: Issue(702, "closed", state="closed", milestone="A2A",
                   closed_at=(base_time - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        703: Issue(703, "child of the epic", milestone="A2A", parent=700),
        704: Issue(704, "closed epic", state="closed", milestone="A2A", labels=("type:epic",),
                   closed_at=(base_time - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        705: Issue(705, "child of a closed epic", milestone="A2A", parent=704),
        706: Issue(706, "blocked", milestone="A2A", blocked_by=(707,)),
        707: Issue(707, "blocker", milestone="A2A"),
    }
    return Snapshot(
        generated_at=base_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="arbitration-test",
        issues=issues,
    )


def _paths(tmp_path) -> tuple[str, str]:
    return str(tmp_path / "claims"), str(tmp_path / "locks")


def _arbitrate(board, issue, tmp_path, base_time, *, lane="lane-b", now=None, **kwargs):
    ledger, locks = _paths(tmp_path)
    return claims.arbitrate(issue, "agent-b", lane, board, ledger=ledger, lock_dir=locks,
                            now=now or base_time, **kwargs)


def _hold(board, issue, tmp_path, base_time, agent="holder-agent", lane="holder-lane") -> None:
    ledger, locks = _paths(tmp_path)
    claims.claim(issue, agent, lane, board, ledger=ledger, lock_dir=locks, now=base_time)


# --- the issue half of the provenance ---------------------------------------


def test_an_unknown_issue_is_refused_naming_the_board(board, tmp_path, base_time):
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 999, tmp_path, base_time, snapshot_path=str(tmp_path / "snapshot.json"))

    assert excinfo.value.reason == REASON_UNKNOWN_ISSUE
    assert "#999" in excinfo.value.detail
    assert "arbitration-test" in excinfo.value.detail


def test_a_closed_issue_is_refused_and_the_evidence_is_named(board, tmp_path, base_time):
    snapshot_path = str(tmp_path / "snapshot.json")
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 702, tmp_path, base_time, snapshot_path=snapshot_path)

    assert excinfo.value.reason == REASON_ISSUE_CLOSED
    assert snapshot_path in excinfo.value.detail
    assert "state=closed" in excinfo.value.detail


def test_a_closed_epic_is_refused_and_the_epic_is_named(board, tmp_path, base_time):
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 705, tmp_path, base_time)

    detail = excinfo.value.detail
    assert excinfo.value.reason == REASON_EPIC_CLOSED
    assert "#705's epic is closed" in detail
    assert "parent=#704" in detail
    assert "issue #704 state=closed" in detail


def test_the_pre_flight_agrees_that_a_closed_epic_is_not_eligible(board):
    """`eligible` and the arbitration must not disagree — the trap this closes."""
    verdict = order.eligible(board, 705)
    assert verdict.eligible is False
    assert verdict.reason == REASON_EPIC_CLOSED


def test_an_open_issue_under_an_open_epic_is_eligible(board):
    assert order.eligible(board, 703, active_claims=frozenset({700})).eligible is True


# --- the lane half of the provenance ----------------------------------------


def test_a_unit_another_lane_holds_is_refused_and_the_holder_is_named(board, tmp_path, base_time):
    # The held unit is a child of the active epic #700: with an epic active, the
    # board pools everything OUTSIDE it (epic focus, #707/#721), so a standalone
    # unit could not be claimed here at all.
    _hold(board, 703, tmp_path, base_time)
    ledger, locks = _paths(tmp_path)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 703, tmp_path, base_time)

    detail = excinfo.value.detail
    assert excinfo.value.reason == REASON_ALREADY_CLAIMED
    assert "held by another lane" in detail
    assert "holder-agent" in detail
    assert "lane holder-lane" in detail
    assert ledger in detail
    assert f"{locks}/703.lock" in detail


def test_the_holder_is_refused_its_own_second_dispatch(board, tmp_path, base_time):
    _hold(board, 703, tmp_path, base_time)
    ledger, locks = _paths(tmp_path)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.arbitrate(703, "holder-agent", "holder-lane", board, ledger=ledger, lock_dir=locks,
                         now=base_time)

    assert excinfo.value.reason == REASON_ALREADY_CLAIMED
    assert "already holds #703" in excinfo.value.detail


def test_a_released_unit_is_dispatchable_again(board, tmp_path, base_time):
    """The refusal is about a *live* claim, not about history: release frees it."""
    _hold(board, 703, tmp_path, base_time)
    ledger, locks = _paths(tmp_path)
    claims.release(703, "holder-agent", ledger=ledger, lock_dir=locks, now=base_time)

    arbitration = claims.arbitrate(703, "agent-b", "lane-b", board, ledger=ledger, lock_dir=locks,
                                   now=base_time)

    assert arbitration.issue == 703
    assert arbitration.lane == "lane-b"


# --- the granted verdict and what it records --------------------------------


def test_a_granted_dispatch_proves_issue_epic_lane(board, tmp_path, base_time):
    arbitration = _arbitrate(board, 703, tmp_path, base_time)
    provenance = arbitration.provenance

    assert arbitration.issue == 703
    assert arbitration.epic == 700
    assert arbitration.lane == "lane-b"
    assert provenance.issue == 703
    assert provenance.epic == 700
    assert provenance.lane == "lane-b"
    assert provenance.owned is True
    assert provenance.evidence, "a grant must carry the evidence it was judged against"
    assert any("issue #703 state=open" in line for line in provenance.evidence)
    assert any("#700" in line for line in provenance.evidence)
    assert any("lane-b" in line for line in provenance.evidence)


def test_the_claim_records_the_provenance_on_the_ledger(board, tmp_path, base_time, monkeypatch):
    """Acceptance: provenance is recorded on the *claim*, not only in memory.

    A routed unit (#726's actual case): the claim is taken via a directive, so the
    record carries the epic the board gives the issue, not just the issue number.
    """
    sent = tmp_path / "sent"
    sent.mkdir()
    (sent / "d-good.json").write_text(
        json.dumps({"from": "brain", "to": "lane", "type": "directive", "id": "d-good",
                    "task": {"issue": 703, "epic": 700, "lane": "lane-b"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    ledger, locks = _paths(tmp_path)
    claims.claim(703, "agent-b", "lane-b", board, ledger=ledger, lock_dir=locks, now=base_time,
                 directive_id="d-good")

    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((tmp_path / "claims").glob("*.json"))]
    assert len(records) == 1
    recorded = records[0]["provenance"]
    assert recorded["issue"] == 703
    assert recorded["epic"] == 700
    assert recorded["lane"] == "lane-b"
    assert recorded["evidence"]
    assert claims.read_ledger(ledger)[0].provenance.epic == 700


def test_the_claim_adopts_the_lane_the_directive_declares(board, tmp_path, base_time, monkeypatch):
    """A dispatch that names no lane inherits the directive's — the unit is owned."""
    sent = tmp_path / "sent"
    sent.mkdir()
    (sent / "d-lane.json").write_text(
        json.dumps({"from": "brain", "to": "lane", "type": "directive", "id": "d-lane",
                    "task": {"issue": 701, "lane": "docs"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(claims, "SENT_DIR", sent)
    ledger, locks = _paths(tmp_path)

    arbitration = claims.arbitrate(701, "agent-b", "", board, ledger=ledger, lock_dir=locks,
                                   directive_id="d-lane", require_lane=True, now=base_time)
    assert arbitration.lane == "docs"

    event = claims.claim(701, "agent-b", "", board, ledger=ledger, lock_dir=locks,
                         directive_id="d-lane", now=base_time)
    assert event.lane == "docs"
    assert event.provenance.lane == "docs"


# --- an unowned unit ---------------------------------------------------------


def test_an_unowned_unit_is_refused_at_the_dispatch_seam(board, tmp_path, base_time):
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 701, tmp_path, base_time, lane="", require_lane=True)

    assert excinfo.value.reason == REASON_UNOWNED
    assert "no lane owns #701" in excinfo.value.detail
    assert "evidence:" in excinfo.value.detail


def test_the_claim_path_keeps_its_documented_lane_default(board, tmp_path, base_time):
    """`--lane` defaults to empty on the legacy claim path; only dispatch requires one."""
    arbitration = _arbitrate(board, 701, tmp_path, base_time, lane="", require_lane=False)
    assert arbitration.lane == ""
    assert arbitration.provenance.owned is False


# --- a directive that contradicts the board ---------------------------------


def test_a_directive_declaring_an_unrelated_epic_is_refused(board, tmp_path, base_time, monkeypatch):
    sent = tmp_path / "sent"
    sent.mkdir()
    (sent / "d-bad-epic.json").write_text(
        json.dumps({"from": "brain", "to": "lane", "type": "directive", "id": "d-bad-epic",
                    "task": {"issue": 701, "epic": 999}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(claims, "SENT_DIR", sent)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 701, tmp_path, base_time, directive_id="d-bad-epic")

    detail = excinfo.value.detail
    assert excinfo.value.reason == REASON_PROVENANCE_MISMATCH
    assert "declares epic #999" in detail
    assert "no Parent edge" in detail
    assert "task.epic=999" in detail
    assert "d-bad-epic.json" in detail


def test_a_directive_declaring_another_lane_is_refused(board, tmp_path, base_time, monkeypatch):
    sent = tmp_path / "sent"
    sent.mkdir()
    (sent / "d-bad-lane.json").write_text(
        json.dumps({"from": "brain", "to": "lane", "type": "directive", "id": "d-bad-lane",
                    "task": {"issue": 701, "lane": "elsewhere"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(claims, "SENT_DIR", sent)

    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 701, tmp_path, base_time, lane="mine", directive_id="d-bad-lane")

    detail = excinfo.value.detail
    assert excinfo.value.reason == REASON_PROVENANCE_MISMATCH
    assert "declares lane 'elsewhere'" in detail
    assert "requested lane=mine" in detail


def test_a_directive_declaring_the_records_epic_is_accepted(board, tmp_path, base_time, monkeypatch):
    """The same directive shape passes when the board corroborates it."""
    sent = tmp_path / "sent"
    sent.mkdir()
    (sent / "d-good.json").write_text(
        json.dumps({"from": "brain", "to": "lane", "type": "directive", "id": "d-good",
                    "task": {"issue": 703, "epic": 700, "lane": "lane-b"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(claims, "SENT_DIR", sent)

    arbitration = _arbitrate(board, 703, tmp_path, base_time, directive_id="d-good")
    assert arbitration.epic == 700
    assert arbitration.lane == "lane-b"


# --- a stale board -----------------------------------------------------------


def test_a_stale_board_is_refused_rather_than_judged(board, tmp_path, base_time):
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _arbitrate(board, 701, tmp_path, base_time, now=base_time + timedelta(minutes=30))

    assert excinfo.value.reason == REASON_SNAPSHOT_STALE
    assert "refresh first" in excinfo.value.detail


# --- the controls that keep the refusals honest ------------------------------


def test_arbitration_self_control_is_clean():
    assert claims.arbitration_self_control() == []


def test_the_refusal_vocabulary_is_the_declared_set():
    """Every reason the arbitration can raise is declared, so no refusal is silent."""
    assert set(ARBITRATION_REFUSALS) == {
        REASON_UNKNOWN_ISSUE,
        REASON_ISSUE_CLOSED,
        REASON_EPIC_CLOSED,
        REASON_EPIC_NOT_WORKABLE,
        REASON_BLOCKED,
        REASON_ALREADY_CLAIMED,
        REASON_PROVENANCE_MISMATCH,
        REASON_UNOWNED,
        REASON_SNAPSHOT_STALE,
    }


def test_arbitration_self_control_reports_an_unprovoked_refusal(monkeypatch):
    monkeypatch.setattr(claims, "ARBITRATION_REFUSALS", (*ARBITRATION_REFUSALS, "invented-refusal"))
    problems = claims.arbitration_self_control()
    assert any("no provoked refusal for invented-refusal" in problem for problem in problems)


def test_arbitration_self_control_fails_if_the_refusals_stop_refusing(monkeypatch):
    """Anti-formality: a neutered arbitration must not install itself as green."""
    monkeypatch.setattr(claims, "arbitrate", lambda *args, **kwargs: None)
    problems = claims.arbitration_self_control()
    assert problems
    assert any("did not bite" in problem for problem in problems)


def test_arbitration_self_control_fails_if_a_refusal_stops_naming_evidence(monkeypatch):
    """A refusal that no longer quotes its evidence must fail the control."""

    def mute(*args, **kwargs):
        raise claims.ClaimRefused("issue-closed", "closed")

    monkeypatch.setattr(claims, "arbitrate", mute)
    problems = claims.arbitration_self_control()
    assert any("names no evidence" in problem for problem in problems)


def test_arbitration_self_control_does_not_touch_the_repository_state():
    """The control runs in a temp dir and rebinds the mailbox only for its own run."""
    board_dir = claims.ROOT / ".board" / "claims"
    before = sorted(path.name for path in board_dir.glob("*.json")) if board_dir.is_dir() else []
    sent_before = claims.SENT_DIR

    claims.arbitration_self_control()

    after = sorted(path.name for path in board_dir.glob("*.json")) if board_dir.is_dir() else []
    assert before == after
    assert claims.SENT_DIR == sent_before


# --- the audit's provenance rules -------------------------------------------


def test_audit_rejects_a_provenance_the_board_contradicts(board, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        ClaimEvent(event="claim", issue=701, agent="a", at=moment, lane="lane-b",
                   reason=REASON_NEXT_IN_MILESTONE,
                   provenance=Provenance(issue=701, epic=999, lane="lane-b"))
    ]
    problems = claims.audit(events, board, base_time)
    assert any("provenance records epic #999" in problem for problem in problems)


def test_audit_rejects_a_claim_whose_lane_the_provenance_contradicts(board, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        ClaimEvent(event="claim", issue=701, agent="a", at=moment, lane="lane-b",
                   reason=REASON_NEXT_IN_MILESTONE,
                   provenance=Provenance(issue=701, lane="another-lane"))
    ]
    problems = claims.audit(events, board, base_time)
    assert any("provenance records lane another-lane" in problem for problem in problems)


def test_audit_rejects_a_routed_claim_that_records_no_provenance(board, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        ClaimEvent(event="claim", issue=701, agent="a", at=moment, lane="lane-b",
                   reason=claims.REASON_BRAIN_DIRECTED, directive_id="d-1", directive_from="brain")
    ]
    problems = claims.audit(events, board, base_time)
    assert any("records no issue -> epic -> lane provenance" in problem for problem in problems)


def test_audit_leaves_settled_pre_provenance_history_alone(board, base_time):
    """A released routed claim predating #726 is history, not a live violation.

    The committed ledger carries such records (two brain-directed claims on #232
    with no provenance), and an audit that retro-blames them cannot be green on
    this repository — measured, not assumed.
    """
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        ClaimEvent(event="claim", issue=701, agent="a", at=moment, lane="lane-b",
                   reason=claims.REASON_BRAIN_DIRECTED, directive_id="d-1", directive_from="brain"),
        ClaimEvent(event="release", issue=701, agent="a", at=moment, lane="lane-b"),
    ]
    assert claims.audit(events, board, base_time) == []


def test_audit_accepts_a_claim_that_records_a_consistent_provenance(board, base_time):
    moment = base_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        ClaimEvent(event="claim", issue=703, agent="a", at=moment, lane="lane-b",
                   reason=claims.REASON_BRAIN_DIRECTED, directive_id="d-1", directive_from="brain",
                   provenance=Provenance(issue=703, epic=700, lane="lane-b",
                                         evidence=("arbitration-test",))),
    ]
    assert claims.audit(events, board, base_time) == []
