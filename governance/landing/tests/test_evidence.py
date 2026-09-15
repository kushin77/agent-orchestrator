"""The evidence rule: a green, commit-named attestation, or a named gap (#764).

The refusal must be able to say *what it checked*. These tests pin each named
gap, and pin the two directions of the commit comparison in particular — a
control that accepted any commit, or none, would let the merge proceed on
evidence for a tree nobody is merging.
"""

from __future__ import annotations

from conftest import HEAD, PARENT, write_attestation

from governance.landing import evidence as ev


class TestTheEvidenceGap:
    def test_green_and_named_is_no_gap(self, tmp_path):
        path = write_attestation(tmp_path / "att.json", commit=HEAD)
        assert ev.evidence_gap(ev.read_attestation(path), HEAD) is None

    def test_a_short_sha_from_either_side_still_matches(self, tmp_path):
        path = write_attestation(tmp_path / "att.json", commit=HEAD[:9])
        assert ev.evidence_gap(ev.read_attestation(path), HEAD) is None

    def test_a_red_attestation_is_refused(self, tmp_path):
        path = write_attestation(tmp_path / "att.json", result="NOT-OK", rc=1)
        gap = ev.evidence_gap(ev.read_attestation(path), HEAD)
        assert gap is not None and gap.code == ev.GAP_NOT_GREEN
        assert not gap.cannot_assess

    def test_a_green_attestation_with_no_commit_is_refused(self, tmp_path):
        path = write_attestation(tmp_path / "att.json", commit=None)
        gap = ev.evidence_gap(ev.read_attestation(path), HEAD)
        assert gap is not None and gap.code == ev.GAP_UNNAMED_COMMIT

    def test_a_green_attestation_naming_another_commit_is_refused(self, tmp_path):
        path = write_attestation(tmp_path / "att.json", commit=PARENT)
        gap = ev.evidence_gap(ev.read_attestation(path), HEAD)
        assert gap is not None and gap.code == ev.GAP_OTHER_COMMIT
        assert PARENT[:8] in str(gap) and HEAD[:8] in str(gap)

    def test_a_missing_attestation_is_cannot_assess_not_a_pass(self, tmp_path):
        gap = ev.evidence_gap(ev.read_attestation(tmp_path / "absent.json"), HEAD)
        assert gap is not None and gap.code == ev.GAP_ABSENT
        assert gap.cannot_assess

    def test_an_unreadable_attestation_is_cannot_assess(self, tmp_path):
        path = tmp_path / "att.json"
        path.write_text("{not json", encoding="utf-8")
        attestation = ev.read_attestation(path)
        assert attestation.state == ev.STATE_UNREADABLE
        gap = ev.evidence_gap(attestation, HEAD)
        assert gap is not None and gap.cannot_assess

    def test_an_attestation_with_no_verdict_is_cannot_assess(self, tmp_path):
        path = tmp_path / "att.json"
        path.write_text('{"result": "MAYBE", "commit": "abc"}', encoding="utf-8")
        gap = ev.evidence_gap(ev.read_attestation(path), HEAD)
        assert gap is not None and gap.code == ev.GAP_NO_VERDICT and gap.cannot_assess

    def test_the_result_string_carries_the_verdict_when_the_code_is_absent(self, tmp_path):
        path = tmp_path / "att.json"
        path.write_text('{"result": "PASS", "commit": "%s"}' % HEAD, encoding="utf-8")
        attestation = ev.read_attestation(path)
        assert attestation.rc == 0 and attestation.green


class TestCommitIdentity:
    def test_short_shas_match_in_both_directions(self):
        assert ev.same_commit(HEAD[:8], HEAD)
        assert ev.same_commit(HEAD, HEAD[:8])

    def test_a_too_short_name_is_not_identity_evidence(self):
        assert not ev.same_commit("abc12", HEAD)

    def test_different_commits_never_match(self):
        assert not ev.same_commit(PARENT, HEAD)
        assert not ev.same_commit(None, HEAD)
        assert not ev.same_commit(HEAD, "")
