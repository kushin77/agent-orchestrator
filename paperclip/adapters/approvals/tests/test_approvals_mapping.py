"""The deterministic projection (issue #416)."""

from __future__ import annotations

from pathlib import Path

from paperclip.adapters.approvals import fixtures, mapping
from paperclip.adapters.approvals.model import STATE_DENIED, STATE_GRANTED, STATE_PENDING


def _states(tree: Path) -> dict:
    return {(a.kind, a.subject): a.state for a in mapping.project(tree).approvals}


def test_projection_derives_grant_deny_and_pending(tree: Path) -> None:
    states = _states(tree)
    assert states[("hire", "issue:416")] == STATE_GRANTED
    assert states[("hire", "issue:555")] == STATE_DENIED
    assert states[("top-up", "budget:agent/paperclip")] == STATE_GRANTED
    assert states[("override", "issue:999")] == STATE_GRANTED
    assert states[("top-up", "budget:agent/secrets")] == STATE_PENDING


def test_projection_is_deterministic(tree: Path) -> None:
    assert mapping.render(mapping.project(tree)) == mapping.render(mapping.project(tree))


def test_the_decision_ref_names_the_authoritative_record(tree: Path) -> None:
    approvals = {(a.kind, a.subject): a for a in mapping.project(tree).approvals}
    hire = approvals[("hire", "issue:416")]
    assert hire.authority == "governance/dispatch"
    assert hire.decision_ref == ".board/claims/claim-416.json"
    assert hire.actor == "ao-session-416"
    override = approvals[("override", "issue:999")]
    assert override.authority == "fleet/control.py"
    assert override.role == "operator"


def test_a_pending_approval_carries_no_decision_ref(tree: Path) -> None:
    approvals = {(a.kind, a.subject): a for a in mapping.project(tree).approvals}
    pending = approvals[("top-up", "budget:agent/secrets")]
    assert pending.state == STATE_PENDING
    assert pending.decision_ref == ""
    assert pending.request_ref.endswith("req-pending.json")


def test_a_kind_with_no_authority_becomes_a_finding(tmp_path: Path) -> None:
    fixtures.build_tree(tmp_path)
    fixtures.write_record(
        tmp_path,
        ".fleet/brain/inbox/req-transfer.json",
        {"from": "operator", "to": "brain", "type": "directive", "id": "o1", "task": {"issue": 1},
         "approval": {"kind": "transfer", "subject": "issue:1"}},
    )
    codes = {f.code for f in mapping.project(tmp_path).findings}
    assert "no-authority" in codes


def test_a_malformed_record_is_not_skipped_silently(tmp_path: Path) -> None:
    fixtures.build_tree(tmp_path)
    fixtures.write_record(tmp_path, ".board/claims/broken.json", {"event": "claim"})
    codes = {f.code for f in mapping.project(tmp_path).findings}
    assert "malformed-record" in codes


def test_release_is_not_a_decision(tmp_path: Path) -> None:
    fixtures.build_tree(tmp_path)
    fixtures.write_record(
        tmp_path,
        ".board/claims/release-416.json",
        {"event": "release", "issue": 416, "agent": "ao-session-416", "at": "2026-09-14T00:06:00Z"},
    )
    # The grant stands (history), and `release` adds no second decision.
    approvals = {(a.kind, a.subject): a for a in mapping.project(tmp_path).approvals}
    assert approvals[("hire", "issue:416")].state == STATE_GRANTED
    assert not any(f.code == "double-approval" for f in mapping.project(tmp_path).findings)


def test_an_empty_tree_projects_nothing(tmp_path: Path) -> None:
    assert mapping.project(tmp_path).approvals == ()
