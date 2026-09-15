"""The append-only, hash-chained audit rail (issue #650, AC1's "audited")."""

from __future__ import annotations

import pytest

from integrations.erp.crm import audit
from integrations.erp.crm.model import Refused

AT = "2026-09-01T09:00:00Z"


def record(rail: audit.Rail, ref: str = "LEAD-0001", target: str = "contacted") -> audit.Rail:
    return rail.append(
        at=AT,
        actor="rep-1",
        action="advance",
        kind="lead",
        ref=ref,
        from_state="new",
        to_state=target,
    )


def test_an_empty_rail_is_genesis_and_verifies() -> None:
    rail = audit.Rail()
    assert len(rail) == 0
    assert rail.head == audit.GENESIS
    assert rail.verify() == []


def test_append_returns_a_new_rail_and_leaves_the_original_alone() -> None:
    first = audit.Rail()
    second = record(first)
    assert len(first) == 0 and len(second) == 1
    assert first.head == audit.GENESIS and second.head != audit.GENESIS


def test_entries_are_sequenced_and_back_linked() -> None:
    rail = record(audit.Rail())
    rail = record(rail, target="qualified")
    first, second = rail.entries
    assert (first.seq, second.seq) == (1, 2)
    assert first.prev == audit.GENESIS
    assert second.prev == first.digest
    assert rail.verify() == []


def test_the_chain_is_deterministic() -> None:
    """The clock is injected, so the same scenario always produces the same digest."""
    assert record(audit.Rail()).head == record(audit.Rail()).head


def test_a_rewritten_entry_is_reported_by_name() -> None:
    rail = record(record(audit.Rail()))
    tampered = [dict(entry) for entry in rail.to_list()]
    tampered[1]["to_state"] = "sprocketed"
    findings = audit.Rail.from_list(tampered).verify()
    assert [finding.code for finding in findings] == ["audit-broken"]
    assert "does not reproduce" in findings[0].detail
    assert "seq 2" in findings[0].detail


def test_a_removed_entry_is_reported() -> None:
    rail = record(record(audit.Rail()))
    kept = [dict(entry) for entry in rail.to_list() if entry["seq"] != 1]
    findings = audit.Rail.from_list(kept).verify()
    assert any(finding.code == "audit-broken" for finding in findings)
    assert any("claims seq 2" in finding.detail for finding in findings)


def test_a_reordered_entry_is_reported() -> None:
    rail = record(record(audit.Rail()))
    entries = [dict(entry) for entry in rail.to_list()]
    findings = audit.Rail.from_list([entries[0], {**entries[0], "seq": 2}, entries[1]]).verify()
    assert any(finding.code == "audit-broken" for finding in findings)


def test_a_rebuild_does_not_launder_a_tampered_chain() -> None:
    """Re-deriving the digests on load would erase the evidence verify exists to find."""
    rail = record(audit.Rail())
    entries = [dict(entry) for entry in rail.to_list()]
    entries[0]["actor"] = "somebody-else"
    rebuilt = audit.Rail.from_list(entries)
    assert rebuilt.entries[0].digest == rail.entries[0].digest
    assert [finding.code for finding in rebuilt.verify()] == ["audit-broken"]


def test_an_undeclared_action_is_refused_by_name() -> None:
    with pytest.raises(Refused, match="unknown-action") as caught:
        audit.Rail().append(at=AT, actor="rep-1", action="launch", kind="lead", ref="LEAD-0001")
    assert "launch" in caught.value.detail


def test_an_entry_without_an_actor_or_an_instant_is_refused() -> None:
    with pytest.raises(Refused, match="invalid-value"):
        audit.Rail().append(at=AT, actor="", action="create", kind="lead", ref="LEAD-0001")
    with pytest.raises(Refused, match="invalid-timestamp"):
        audit.Rail().append(at="", actor="rep-1", action="create", kind="lead", ref="LEAD-0001")


def test_for_ref_selects_one_documents_history() -> None:
    rail = record(record(audit.Rail(), ref="LEAD-0001"), ref="OPP-0001")
    assert [entry.ref for entry in rail.for_ref("LEAD-0001")] == ["LEAD-0001"]


def test_an_unreadable_entry_is_refused_by_name() -> None:
    with pytest.raises(Refused, match="audit-broken"):
        audit.Rail.from_list([{"seq": 1, "nonsense": True}])


def test_an_entry_digest_covers_its_payload_and_its_predecessor() -> None:
    rail = record(audit.Rail())
    entry = rail.entries[0]
    assert entry.recompute(entry.prev) == entry.digest
    assert entry.recompute("f" * 64) != entry.digest
    assert entry.to_dict()["digest"] == entry.digest
