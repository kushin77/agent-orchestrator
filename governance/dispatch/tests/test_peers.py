"""The A2A peer-check standard, unit-proven (issue #1549, EPIC #1510).

Each arm pins one property the standard claims, so the gate that runs this suite
is measuring the rule rather than the script's existence:

* enumeration is the LIVE claim set (an expired lease is not a sibling);
* the caller's own claim is never one of its siblings;
* the overlap decision is ``model.file_claims_conflict`` — a whole-file lease
  conflicts with anything on that path, two disjoint regions do not;
* a refusal NAMES the sibling and the specific path, and a sibling with no file
  evidence is reported ``unverifiable`` rather than silently ``disjoint``;
* the channel column is evidence-labelled and may be ``unknown``, because
  cross-vendor attribution from git/gh metadata alone is measured to be
  unreliable (the escalation clause of issue #1549).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import claims as claims_mod
import peers
from model import ClaimEvent, FileClaim

NOW = datetime(2026, 9, 20, 19, 0, 0, tzinfo=timezone.utc)


def event(issue, agent, *, lane="governance", at="2026-09-20T18:00:00Z", files=()):
    return ClaimEvent(event="claim", issue=issue, agent=agent, at=at, lane=lane, files=tuple(files))


def live_of(*events):
    return claims_mod.active_claims(list(events), NOW)


# --- channel classification -------------------------------------------------


def test_a_live_bus_member_is_claude_bus():
    channel, evidence = peers.classify_channel("subagent-brain-di", bus_members=["subagent-brain-di"])
    assert channel == "claude-bus"
    assert "ListAgents" in evidence


def test_the_owner_login_is_human():
    channel, evidence = peers.classify_channel("kushin77")
    assert channel == "human"
    assert "owner login" in evidence


def test_copilot_and_deepseek_prefixes_are_classified():
    assert peers.classify_channel("copilot-brain")[0] == "copilot"
    assert peers.classify_channel("agent-copilot-ao1446")[0] == "copilot"
    assert peers.classify_channel("deepseek-agent-7")[0] == "deepseek"
    assert peers.classify_channel("agent-deepseek-7")[0] == "deepseek"


def test_a_minted_author_email_is_not_evidence_of_a_human():
    channel, _ = peers.classify_channel(
        "ao-sub-1521", author="agent+ao-sub-1521@agents.invalid"
    )
    assert channel == "unknown"


def test_a_non_minted_author_email_is_evidence_of_a_human():
    channel, evidence = peers.classify_channel("some-handle", author="kushin77@gmail.com")
    assert channel == "human"
    assert "minted fleet domain" in evidence


def test_an_unattributable_id_is_unknown_and_names_what_was_checked():
    channel, evidence = peers.classify_channel("ao-sub-1521")
    assert channel == "unknown"
    # The refusal must be actionable: name every signal that was checked.
    assert "no runtime prefix" in evidence
    assert "author email" in evidence


# --- the one overlap predicate, reused --------------------------------------


def test_whole_file_leases_on_one_path_conflict():
    assert peers.overlap_paths((FileClaim("a.sh"),), (FileClaim("a.sh"),)) == ("a.sh",)


def test_different_paths_do_not_conflict():
    assert peers.overlap_paths((FileClaim("a.sh"),), (FileClaim("b.sh"),)) == ()


def test_disjoint_regions_on_the_same_path_do_not_conflict():
    mine = (FileClaim("a.sh", ((1, 10),)),)
    theirs = (FileClaim("a.sh", ((20, 30),)),)
    assert peers.overlap_paths(mine, theirs) == ()


def test_overlapping_regions_conflict_and_the_label_names_each_region():
    mine = (FileClaim("a.sh", ((1, 25),)),)
    theirs = (FileClaim("a.sh", ((20, 30),)),)
    paths = peers.overlap_paths(mine, theirs)
    assert len(paths) == 1
    assert "1-25" in paths[0] and "20-30" in paths[0]


def test_a_region_claim_conflicts_with_a_whole_file_claim():
    mine = (FileClaim("a.sh", ((29, 60),)),)
    theirs = (FileClaim("a.sh"),)
    assert len(peers.overlap_paths(mine, theirs)) == 1


# --- enumeration ------------------------------------------------------------


def test_an_expired_lease_is_not_a_sibling():
    events = [
        # Claimed three days before the pinned clock: past the 24h TTL.
        event(1, "stale", at="2026-09-17T18:00:00Z", files=[FileClaim("a.sh")]),
        # Claimed a minute before the pinned clock: alive.
        event(2, "fresh", at="2026-09-20T18:59:00Z", files=[FileClaim("a.sh")]),
    ]
    live = claims_mod.active_claims(events, NOW)
    assert 1 not in live
    report = peers.peer_check(
        (FileClaim("a.sh"),), live, caller_agent="me", caller_issue=99
    )
    assert [s.issue for s in report.siblings] == [2]


def test_the_callers_own_claim_is_never_a_sibling():
    live = live_of(event(99, "me", files=[FileClaim("a.sh")]))
    report = peers.peer_check(
        (FileClaim("a.sh"),), live, caller_agent="me", caller_issue=99
    )
    assert report.siblings == ()
    assert report.verdict == "disjoint"


# --- the verdict, and the refusal that names the sibling --------------------


def test_an_overlapping_sibling_refuses_by_name_and_path():
    live = live_of(event(12, "ao-sub-12", lane="portal", files=[FileClaim("portal/js/api.js")]))
    report = peers.peer_check(
        (FileClaim("portal/js/api.js"),), live, caller_agent="ao-sub-1549", caller_issue=1549
    )
    assert report.verdict == "OVERLAP"
    refusal = report.refusal()
    assert refusal.startswith("peer-check REFUSED: OVERLAP")
    assert "ao-sub-12" in refusal
    assert "#12" in refusal
    assert "portal" in refusal
    assert "portal/js/api.js" in refusal


def test_a_disjoint_sibling_does_not_refuse():
    live = live_of(event(12, "ao-sub-12", files=[FileClaim("other/file.py")]))
    report = peers.peer_check(
        (FileClaim("portal/js/api.js"),), live, caller_agent="ao-sub-1549", caller_issue=1549
    )
    assert report.verdict == "disjoint"
    assert report.refusal() is None


def test_every_overlapping_sibling_is_named_not_just_the_first():
    live = live_of(
        event(12, "ao-sub-12", files=[FileClaim("a.sh")]),
        event(13, "ao-sub-13", files=[FileClaim("a.sh")]),
    )
    refusal = peers.peer_check(
        (FileClaim("a.sh"),), live, caller_agent="ao-sub-1549", caller_issue=1549
    ).refusal()
    assert "ao-sub-12" in refusal
    assert "ao-sub-13" in refusal
    assert "1 further sibling" in refusal


def test_a_sibling_with_no_file_evidence_is_unverifiable_not_disjoint():
    live = live_of(event(12, "ao-sub-12", files=[]))
    report = peers.peer_check((FileClaim("a.sh"),), live, caller_agent="me", caller_issue=99)
    assert report.verdict == "disjoint"
    assert [s.verdict for s in report.siblings] == ["unverifiable"]
    assert report.unverifiable
    assert "unverifiable" in report.table()
    assert "no file evidence" in report.table()


def test_file_evidence_is_unioned_from_claim_issue_and_branch():
    live = live_of(event(12, "ao-sub-12", files=[FileClaim("from-claim.sh")]))
    report = peers.peer_check(
        (FileClaim("nothing.sh"),),
        live,
        caller_agent="me",
        caller_issue=99,
        issue_files={12: ["from-issue.sh"]},
        branch_files={12: ["from-branch.sh"]},
        branch_refs={12: "issue-12"},
    )
    sibling = report.siblings[0]
    assert [f.path for f in sibling.files] == ["from-claim.sh", "from-issue.sh", "from-branch.sh"]
    assert len(sibling.file_evidence) == 3
    assert any("issue-12" in item for item in sibling.file_evidence)


def test_an_empty_caller_file_set_is_named_as_a_note_not_a_silent_pass():
    live = live_of(event(12, "ao-sub-12", files=[FileClaim("a.sh")]))
    report = peers.peer_check((), live, caller_agent="me", caller_issue=99)
    assert report.verdict == "disjoint"
    assert any("declares no files" in note for note in report.notes)


# --- enhance, never clobber -------------------------------------------------


def test_two_live_siblings_holding_one_file_are_reported_as_a_collision():
    live = live_of(
        event(12, "ao-sub-12", files=[FileClaim("docs/README.md")]),
        event(13, "ao-sub-13", files=[FileClaim("docs/README.md")]),
    )
    report = peers.peer_check((FileClaim("mine.sh"),), live, caller_agent="me", caller_issue=99)
    collisions = report.collisions
    assert len(collisions) == 1
    assert collisions[0].paths == ("docs/README.md",)
    assert "docs/README.md" in report.table()


def test_the_enhancement_prefers_a_real_collision_and_names_both_lanes():
    live = live_of(
        event(12, "ao-sub-12", files=[FileClaim("docs/README.md")]),
        event(13, "ao-sub-13", files=[FileClaim("docs/README.md")]),
    )
    report = peers.peer_check((FileClaim("mine.sh"),), live, caller_agent="me", caller_issue=99)
    enhancement = report.enhancement()
    assert "ao-sub-12" in enhancement and "ao-sub-13" in enhancement
    assert "docs/README.md" in enhancement


def test_no_enhancement_rather_than_a_status_ping():
    live = live_of(event(12, "ao-sub-12", files=[FileClaim("other.sh")]))
    report = peers.peer_check((FileClaim("mine.sh"),), live, caller_agent="me", caller_issue=99)
    assert report.enhancement() is None


def test_an_unverifiable_sibling_earns_a_mechanical_enhancement():
    live = live_of(event(12, "ao-sub-12", files=[]))
    report = peers.peer_check((FileClaim("mine.sh"),), live, caller_agent="me", caller_issue=99)
    enhancement = report.enhancement()
    assert "ao-sub-12" in enhancement
    assert "declares no files" in enhancement


# --- ledger resolution ------------------------------------------------------


def test_a_lane_worktree_falls_back_to_the_main_worktrees_ledger(tmp_path):
    main = tmp_path / "shared"
    (main / ".board" / "claims").mkdir(parents=True)
    (main / ".board" / "claims" / "0001-00012-a-claim.json").write_text("{}", encoding="utf-8")
    lane = tmp_path / "lane"
    (lane / ".board").mkdir(parents=True)
    resolved, note = peers.resolve_ledger(lane, main)
    assert resolved == main / ".board" / "claims"
    assert "live claim ledger" in note


def test_an_empty_claims_directory_is_not_a_ledger(tmp_path):
    lane = tmp_path / "lane"
    (lane / ".board" / "claims").mkdir(parents=True)
    resolved, note = peers.resolve_ledger(lane)
    assert resolved is None
    assert "no *.json claim record" in note


def test_no_ledger_anywhere_names_every_location_checked(tmp_path):
    resolved, note = peers.resolve_ledger(tmp_path / "lane", tmp_path / "shared")
    assert resolved is None
    assert str(tmp_path / "lane" / ".board" / "claims") in note
    assert str(tmp_path / "shared" / ".board" / "claims") in note


# --- the command line -------------------------------------------------------


def test_the_files_argument_parses_regions():
    parsed = peers._parse_files_arg("a.sh:1-10,b.py")
    assert parsed == (FileClaim("a.sh", ((1, 10),)), FileClaim("b.py"))


def test_a_malformed_region_is_refused():
    try:
        peers._parse_files_arg("a.sh:oops")
    except ValueError as exc:
        assert "start-end" in str(exc)
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("a malformed region was accepted")


def test_standard_prints_the_rule_and_exits_zero(capsys):
    assert peers.main(["--standard"]) == peers.EXIT_OK
    assert "A2A peer-check standard" in capsys.readouterr().out


def test_no_caller_identity_is_cannot_assess(capsys):
    assert peers.main(["--standard", "--caller-agent", ""]) == peers.EXIT_OK
    assert peers.main(["--caller-agent", ""]) == peers.EXIT_CANNOT_ASSESS
    assert "no caller identity" in capsys.readouterr().err


def test_a_missing_ledger_is_cannot_assess_never_a_pass(tmp_path, capsys):
    rc = peers.main(
        [
            "--caller-agent",
            "me",
            "--caller-issue",
            "99",
            "--root",
            str(tmp_path),
            "--ledger",
            str(tmp_path / "absent"),
        ]
    )
    assert rc == peers.EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_an_overlap_on_the_command_line_exits_one(tmp_path, capsys):
    ledger = tmp_path / "claims"
    ledger.mkdir()
    # `peers.main()` below reads the ledger against the REAL clock (it takes
    # no `now` override), unlike the fixed-NOW helper tests in this file that
    # call `claims_mod.active_claims(..., NOW)` directly. `event()`'s default
    # `at` is a literal past timestamp, so a claim written with it ages past
    # the claim TTL as real time moves forward and this arm starts observing
    # a correctly-expired (dropped) claim instead of the live overlap it is
    # meant to plant (#1501). Use a claim made "just now" so it is live
    # regardless of which day the suite runs.
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    held = event(12, "ao-sub-12", at=now_iso, files=[FileClaim("a.sh")])
    (ledger / "0001-00012-ao-sub-12-claim.json").write_text(
        _json(held.to_json()), encoding="utf-8"
    )
    rc = peers.main(
        [
            "--caller-agent",
            "ao-sub-1549",
            "--caller-issue",
            "1549",
            "--files",
            "a.sh",
            "--ledger",
            str(ledger),
            "--no-branch-files",
        ]
    )
    captured = capsys.readouterr()
    assert rc == peers.EXIT_NOT_OK
    assert "peer-check REFUSED: OVERLAP" in captured.err
    assert "ao-sub-12" in captured.err
    assert "a.sh" in captured.err


def _json(payload) -> str:
    import json

    return json.dumps(payload, sort_keys=True)


def test_the_clock_is_injectable_so_the_report_is_reproducible():
    # A lease exactly at the TTL boundary: alive one second before, gone at it.
    at = (NOW - timedelta(hours=24) + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    live = claims_mod.active_claims([event(12, "ao-sub-12", at=at)], NOW)
    assert 12 in live
    gone = claims_mod.active_claims([event(12, "ao-sub-12", at=at)], NOW + timedelta(seconds=1))
    assert 12 not in gone


def test_the_module_is_importable_by_path_so_a_script_can_call_it():
    # The wrapper resolves the engine by path; this pins that the module's own
    # sys.path bootstrap is what makes the bare `import claims` work.
    assert Path(peers.__file__).name == "peers.py"
    assert peers.EXIT_OK == 0 and peers.EXIT_NOT_OK == 1 and peers.EXIT_CANNOT_ASSESS == 2
