"""Verification against the authority (issue #416)."""

from __future__ import annotations

from pathlib import Path

from paperclip.adapters.approvals import fixtures, mapping, verify
from paperclip.adapters.approvals.model import STATE_GRANTED, STATE_PENDING, Approval


def _codes(findings) -> set:
    return {f.code for f in findings}


def test_a_clean_tree_verifies(tree: Path) -> None:
    assert verify.verify(tree) == []


def test_the_projection_is_deterministic(tree: Path) -> None:
    assert verify.deterministic(tree)


def test_a_grant_with_no_record_is_refused(tree: Path) -> None:
    approval = next(a for a in mapping.project(tree).approvals if a.state == STATE_GRANTED)
    hollow = Approval(**{**approval.to_dict(), "decision_ref": ""})
    findings = verify.verify_approval(hollow, mapping.decision_index(tree))
    assert "projection-without-record" in _codes(findings)
    assert any(approval.id in f.detail for f in findings)


def test_a_removed_record_fails_a_built_projection(tree: Path) -> None:
    stale = mapping.project(tree)
    cited = next(a for a in stale.approvals if a.state == STATE_GRANTED)
    fixtures.remove_record(tree, cited.decision_ref)
    assert "record-absent" in _codes(verify.verify(tree, stale))


def test_a_contradicting_record_is_refused(tree: Path) -> None:
    stale = mapping.project(tree)
    cited = next(a for a in stale.approvals if a.state == STATE_GRANTED and a.kind == "hire")
    tampered = Approval(**{**cited.to_dict(), "actor": "not-the-decider"})
    findings = verify.verify_approval(tampered, mapping.decision_index(tree))
    assert "record-contradicts" in _codes(findings)


def test_a_double_approval_is_refused(tmp_path: Path) -> None:
    fixtures.build_tree(tmp_path)
    fixtures.write_record(
        tmp_path,
        ".board/claims/claim-416-again.json",
        {"event": "claim", "issue": 416, "agent": "another", "at": "2026-09-14T00:07:00Z"},
    )
    assert "double-approval" in _codes(verify.verify(tmp_path))


def test_an_unauthorised_decider_is_refused(tmp_path: Path) -> None:
    fixtures.build_tree(tmp_path)
    fixtures.write_record(
        tmp_path,
        ".fleet/sent/topup-sister.json",
        {"from": "brain", "to": "sister", "type": "directive", "id": "d1", "task": {"issue": 418},
         "approval": {"kind": "top-up", "subject": "budget:agent/routines", "decision": "grant", "actor": "sister"}},
    )
    findings = verify.verify(tmp_path)
    assert "unauthorised-decider" in _codes(findings)
    assert any("sister" in f.detail for f in findings)


def test_pending_is_never_promoted_by_editing_the_projection(tree: Path) -> None:
    pending = next(a for a in mapping.project(tree).approvals if a.state == STATE_PENDING)
    promoted = Approval(**{**pending.to_dict(), "state": STATE_GRANTED})
    assert "projection-without-record" in _codes(
        verify.verify_approval(promoted, mapping.decision_index(tree))
    )


def test_an_orphan_decision_is_reported(tmp_path: Path) -> None:
    fixtures.build_tree(tmp_path)
    projection = mapping.project(tmp_path)
    trimmed = type(projection)(
        approvals=tuple(a for a in projection.approvals if a.kind != "hire"),
        findings=projection.findings,
    )
    assert "orphan-decision" in _codes(verify.verify(tmp_path, trimmed))


def test_every_approval_conforms_to_the_schema(tree: Path) -> None:
    assert verify.schema_findings(mapping.project(tree)) == []
