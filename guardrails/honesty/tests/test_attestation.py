"""Attestation tests (issue #28, acceptance criterion 4).

An attestation is evidence, not vibes: the verdict is DERIVED from the exit
code through the tri-state contract, and a CANNOT-ASSESS run with no evidence
attests nothing (a merge on such a record is a merge without evidence).
"""

from __future__ import annotations

from honesty.attestation import GuardAttestation
from honesty.tristate import TriState


class TestRecordDerivesVerdict:
    def test_ok_verdict_from_exit_zero(self) -> None:
        att = GuardAttestation.record(
            guard_id="g", exit_code=0, evidence="clean", controls=["g_pos"]
        )
        assert att.verdict is TriState.OK
        assert att.exit_code == 0
        assert att.attested is True

    def test_not_ok_verdict_from_exit_one(self) -> None:
        att = GuardAttestation.record(
            guard_id="g", exit_code=1, evidence="violation found"
        )
        assert att.verdict is TriState.NOT_OK
        assert att.attested is True

    def test_cannot_assess_is_not_attestation(self) -> None:
        # CANNOT-ASSESS with no productive evidence is not attestation.
        att = GuardAttestation.record(guard_id="g", exit_code=2, evidence="")
        assert att.verdict is TriState.CANNOT_ASSESS
        assert att.attested is False

    def test_empty_evidence_never_attests(self) -> None:
        att = GuardAttestation.record(guard_id="g", exit_code=0, evidence="   ")
        assert att.attested is False


class TestRoundTrip:
    def test_dict_roundtrip_preserves_fields(self) -> None:
        original = GuardAttestation.record(
            guard_id="check_blocklist",
            exit_code=1,
            evidence="violation present",
            provenance="leaderboard scripts/guard",
            controls=["blocklist_violation", "blocklist_clean"],
        )
        clone = GuardAttestation.from_dict(original.to_dict())
        assert clone.guard_id == original.guard_id
        assert clone.verdict is original.verdict
        assert clone.exit_code == original.exit_code
        assert clone.evidence == original.evidence
        assert clone.provenance == original.provenance
        assert clone.controls == original.controls
        assert clone.timestamp == original.timestamp

    def test_json_roundtrip(self) -> None:
        original = GuardAttestation.record(
            guard_id="g", exit_code=1, evidence="boom", controls=["nc1"]
        )
        clone = GuardAttestation.from_json(original.to_json())
        assert clone.to_dict() == original.to_dict()

    def test_summary_is_compact(self) -> None:
        att = GuardAttestation.record(
            guard_id="g", exit_code=1, evidence="boom", controls=["nc1"]
        )
        summary = att.summary()
        assert "verdict=NOT-OK" in summary
        assert "exit=1" in summary
