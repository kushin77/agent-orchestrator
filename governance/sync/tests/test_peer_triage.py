"""Peer-board triage tests (issue #427).

The pass must be offline, deterministic, and honest: every surfaced item has a
disposition, everything adopted from a peer carries provenance, a peer issue is
never closed from here, and an input the pass cannot read is CANNOT-ASSESS
(never a pass). Each negative below is a case the gate must genuinely fail on.
"""

from __future__ import annotations

import json
import os

import pytest

import peer_triage
from peer_triage import (
    CannotAssess,
    OWNERSHIP_SCHEMA,
    REPORT_SCHEMA,
    SNAPSHOT_SCHEMA,
    TRIAGE_SCHEMA,
)

SHA_A = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
SHA_B = "b2c3d4e5f60718293a4b5c6d7e8f9012345678a1"

OWNERSHIP = {
    "schema": OWNERSHIP_SCHEMA,
    "owner_repo": "kushin77/agent-orchestrator",
    "name_tokens": ["agent-orchestrator"],
    "peers": ["kushin77/CMR"],
    "lanes": {"governance": "governance/"},
}


def issue(number, *, state="open", title="", body="", labels=None):
    return {"number": number, "title": title, "state": state, "body": body,
            "labels": labels or []}


def snap(issues, repo="kushin77/CMR", generated="2026-09-14T00:00:00Z"):
    return {"schema": SNAPSHOT_SCHEMA, "generated": generated,
            "peers": [{"repo": repo, "issues": issues}]}


def empty_snapshot(generated="2026-09-14T00:00:00Z"):
    return {"schema": SNAPSHOT_SCHEMA, "generated": generated,
            "peers": [{"repo": "kushin77/CMR", "issues": []}]}


def triages(items):
    return {"schema": TRIAGE_SCHEMA, "items": items}


NAMED = issue(1, title="needs kushin77/agent-orchestrator", body="blocks us")
LANE = issue(2, title="board housekeeping", labels=["governance"])


def run(snap_doc, base_doc, triages_doc, own_doc=OWNERSHIP):
    ownership = peer_triage.load_ownership(own_doc)
    snapshot = peer_triage.index_issues(snap_doc, "snapshot")
    baseline = peer_triage.index_issues(base_doc, "baseline")
    ledger = peer_triage.load_triages(triages_doc)
    generated = {"snapshot": snap_doc.get("generated"),
                 "baseline": base_doc.get("generated")}
    return peer_triage.run_pass(snapshot, baseline, ledger, ownership,
                                generated=generated)


def codes(report):
    return sorted(f["code"] for f in report["findings"])


def refs(report, code):
    return sorted(f["ref"] for f in report["findings"] if f["code"] == code)


class TestHappyPath:
    def test_all_dispositions_is_ok(self):
        report = run(
            snap([NAMED, LANE]),
            snap([NAMED, LANE], generated="2026-09-07T00:00:00Z"),
            triages({
                "kushin77/CMR#1": {
                    "disposition": "track-here", "reason": "our half",
                    "local_issue": 427,
                    "provenance": {"repo": "kushin77/CMR", "issue": 1,
                                   "sha": SHA_A}},
                "kushin77/CMR#2": {
                    "disposition": "no-action", "reason": "not ours"},
            }))
        assert report["verdict"] == "ok"
        assert report["findings"] == []
        assert report["summary"]["track-here"] == 1
        assert report["summary"]["no-action"] == 1

    def test_committed_fixtures_pass(self, tmp_path):
        out = tmp_path / "report.json"
        rc = peer_triage.main(["--quiet", "--report", str(out)])
        assert rc == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["schema"] == REPORT_SCHEMA
        assert report["verdict"] == "ok"
        assert report["summary"]["candidates"] == 4
        assert report["summary"]["findings"] == 0

    def test_direction_is_reported_never_filed(self):
        report = run(
            snap([LANE]),
            snap([LANE], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#2": {
                "disposition": "direction", "reason": "they own it",
                "provenance": {"repo": "kushin77/CMR", "issue": 2,
                               "sha": SHA_A},
                "direction": {"target_repo": "kushin77/CMR",
                              "title": "own this", "labels": ["cmr:direction"],
                              "status": "held(guard)"}}}))
        assert report["verdict"] == "ok"
        assert report["direction_issues_to_file"][0]["filed"] is False


class TestDispositions:
    def test_missing_disposition_fails_naming_the_item(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({}))
        assert report["verdict"] == "not-ok"
        assert refs(report, "no-disposition") == ["kushin77/CMR#1"]
        assert peer_triage._report_exit(report) == 1

    def test_unknown_disposition_fails(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#1": {"disposition": "maybe",
                                        "reason": "x"}}))
        assert codes(report) == ["bad-disposition"]

    def test_no_action_without_reason_fails(self):
        report = run(
            snap([LANE]),
            snap([LANE], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#2": {"disposition": "no-action"}}))
        assert "no-reason" in codes(report)


class TestProvenance:
    def test_track_here_without_provenance_fails(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#1": {
                "disposition": "track-here", "reason": "our half",
                "local_issue": 427}}))
        assert "missing-provenance" in codes(report)

    def test_provenance_without_sha_fails(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#1": {
                "disposition": "track-here", "reason": "our half",
                "local_issue": 427,
                "provenance": {"repo": "kushin77/CMR", "issue": 1}}}))
        assert "missing-provenance" in codes(report)

    def test_provenance_naming_another_item_fails(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#1": {
                "disposition": "track-here", "reason": "our half",
                "local_issue": 427,
                "provenance": {"repo": "kushin77/CMR", "issue": 99,
                               "sha": SHA_A}}}))
        assert "bad-provenance" in codes(report)

    def test_track_here_without_link_back_fails(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#1": {
                "disposition": "track-here", "reason": "our half",
                "provenance": {"repo": "kushin77/CMR", "issue": 1,
                               "sha": SHA_A}}}))
        assert "no-link-back" in codes(report)


class TestPeerCloseRefused:
    def test_explicit_close_flag_is_refused(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#1": {
                "disposition": "direction", "reason": "hand off",
                "closes_peer": True,
                "provenance": {"repo": "kushin77/CMR", "issue": 1,
                               "sha": SHA_A}}}))
        assert "peer-close-refused" in codes(report)

    def test_textual_peer_closes_is_refused(self):
        report = run(
            snap([NAMED]),
            snap([NAMED], generated="2026-09-07T00:00:00Z"),
            triages({"kushin77/CMR#1": {
                "disposition": "direction", "reason": "hand off",
                "provenance": {"repo": "kushin77/CMR", "issue": 1,
                               "sha": SHA_A},
                "direction": {"target_repo": "kushin77/CMR",
                              "title": "Closes kushin77/CMR#1 from here"}}}))
        assert "peer-close-refused" in codes(report)
        assert report["direction_issues_to_file"] == []


class TestCannotAssess:
    def test_missing_snapshot_is_cannot_assess(self, tmp_path):
        with pytest.raises(CannotAssess):
            peer_triage.load_inputs(str(tmp_path / "nope.json"),
                                    str(tmp_path / "nope.json"),
                                    str(tmp_path / "nope.json"),
                                    str(tmp_path / "nope.json"))

    def test_main_returns_two_for_missing_snapshot(self, tmp_path):
        missing = str(tmp_path / "nope.json")
        rc = peer_triage.main(["--quiet", "--snapshot", missing])
        assert rc == 2

    def test_empty_snapshot_is_cannot_assess(self):
        with pytest.raises(CannotAssess):
            peer_triage.index_issues(empty_snapshot(), "snapshot")

    def test_bad_schema_is_cannot_assess(self):
        doc = snap([NAMED])
        doc["schema"] = "wrong"
        with pytest.raises(CannotAssess):
            peer_triage.index_issues(doc, "snapshot")

    def test_missing_baseline_is_cannot_assess(self, tmp_path):
        snapshot = tmp_path / "snap.json"
        snapshot.write_text(json.dumps(snap([NAMED])), encoding="utf-8")
        triage_file = tmp_path / "triages.json"
        triage_file.write_text(json.dumps(triages({})), encoding="utf-8")
        own_file = tmp_path / "own.json"
        own_file.write_text(json.dumps(OWNERSHIP), encoding="utf-8")
        with pytest.raises(CannotAssess):
            peer_triage.load_inputs(str(snapshot),
                                    str(tmp_path / "no-baseline.json"),
                                    str(triage_file), str(own_file))


class TestDeterminismAndLive:
    def test_report_is_deterministic(self):
        args = (snap([NAMED, LANE]),
                snap([NAMED, LANE], generated="2026-09-07T00:00:00Z"),
                triages({"kushin77/CMR#1": {
                    "disposition": "track-here", "reason": "our half",
                    "local_issue": 427,
                    "provenance": {"repo": "kushin77/CMR", "issue": 1,
                                   "sha": SHA_A}},
                    "kushin77/CMR#2": {"disposition": "no-action",
                                       "reason": "not ours"}}))
        first = json.dumps(run(*args), sort_keys=True)
        second = json.dumps(run(*args), sort_keys=True)
        assert first == second

    def test_live_fetch_uses_injected_runner(self):
        seen = []

        def runner(repo):
            seen.append(repo)
            return json.dumps([issue(7, title="live",
                                     body="kushin77/agent-orchestrator")])

        doc = peer_triage.fetch_snapshot(["kushin77/CMR"], "stamp",
                                         runner=runner)
        assert seen == ["kushin77/CMR"]
        assert doc["schema"] == SNAPSHOT_SCHEMA
        assert doc["peers"][0]["issues"][0]["number"] == 7

    def test_live_fetch_rejects_garbage(self):
        with pytest.raises(CannotAssess):
            peer_triage.fetch_snapshot(["kushin77/CMR"], "stamp",
                                       runner=lambda _repo: "not json")

    def test_default_paths_exist(self):
        paths = peer_triage._default_paths()
        for label, path in sorted(paths.items()):
            assert os.path.isfile(path), "%s missing: %s" % (label, path)


class TestClassify:
    def test_lane_label_surfaces_a_candidate(self):
        ownership = peer_triage.load_ownership(OWNERSHIP)
        index = peer_triage.index_issues(snap([LANE]), "snapshot")
        named, lanes = peer_triage.classify(index["kushin77/CMR#2"], ownership)
        assert named is False
        assert lanes == ["governance/"]

    def test_unrelated_item_is_not_surfaced(self):
        ownership = peer_triage.load_ownership(OWNERSHIP)
        other = issue(3, title="compliance report", labels=["compliance"])
        index = peer_triage.index_issues(snap([other]), "snapshot")
        named, lanes = peer_triage.classify(index["kushin77/CMR#3"], ownership)
        assert named is False
        assert lanes == []
