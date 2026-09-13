"""The fleet status report: the planning-first surface paperclip owns (issue #302).

Why this module exists
----------------------
ADR-0012 hands the *reporting* half of the fleet a declared pattern owner — the
vendored `paperclip` module — and `fleet/report.py` is the surface that honours
it. These tests pin the three things that make the report worth reading:

* the four sections (now / next / blocked / delivered) are derived from the
  fleet's own state, and each item carries its issue, its lane and an evidence
  pointer rather than a summary nobody can check;
* `--json` is a first-class mode: it round-trips and validates structurally, so
  a consumer gets a shape guarantee instead of a hope;
* the report REFUSES to exist when there is no state to derive it from — exit 2,
  no stdout payload — because an invented report is worse than no report.

Everything here runs against a synthetic fleet in a tmp directory: no network, no
tmux, no live `.fleet/`. `report.read_state(root)` redirects the whole tree, and
every test passes that root explicitly, so a test can never read the operator's
running fleet by accident.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import report  # noqa: E402

MILESTONE = "M26 - Session Fleet Operating Model"
# The fixture's claims must not expire while the suite runs, and `report.main`
# reads the wall clock: a finite TTL would make the JSON round-trip assertion
# depend on the day the suite happened to run.
CLAIM_TTL_HOURS = 876000
CLOCK = "2026-09-13T23:00:00Z"
REPORT_HEADER_LINE = "fleet status report"


# ---------------------------------------------------------------------------
# the synthetic fleet
# ---------------------------------------------------------------------------


def board_issue(number, state, title, blocked_by=(), labels=(), milestone=MILESTONE):
    return {
        "number": number,
        "title": title,
        "state": state,
        "milestone": milestone,
        "labels": list(labels),
        "parent": None,
        "blocked_by": list(blocked_by),
        "closed_at": "",
    }


# #299 is the epic (closed, never a unit of work); #300 is the active milestone's
# frontier (lowest open, unblocked, non-epic); #301..#307 are wave children;
# #308 is a board-only blocker; #309 is claimed but outside the wave.
BOARD_ISSUES = [
    board_issue(299, "CLOSED", "EPIC: reporting alignment", labels=("type:epic",)),
    board_issue(300, "open", "Dispatch by capability"),
    board_issue(301, "CLOSED", "Cannibalize hermes routing"),
    board_issue(302, "open", "Cannibalize paperclip reporting"),
    board_issue(303, "open", "Reporting follow-up"),
    board_issue(304, "open", "Knowledge pack"),
    board_issue(305, "open", "Lessons ledger"),
    board_issue(306, "open", "FinOps chooser"),
    board_issue(307, "open", "Sandbox policy"),
    board_issue(308, "open", "Board reconciliation", blocked_by=(300,)),
    board_issue(309, "open", "Registry packs"),
]

WAVE_PLAN = {
    "parent": 299,
    "children": [
        {"index": 0, "issue": 301, "lane": "autonomous-ops", "verify": "make verify", "depends_on": []},
        {"index": 1, "issue": 302, "lane": "autonomous-ops", "verify": "python3 -m pytest fleet/tests -q", "depends_on": []},
        {"index": 2, "issue": 303, "lane": "autonomous-ops", "verify": "make verify", "depends_on": []},
        {"index": 3, "issue": 304, "lane": "knowledge", "verify": "make verify", "depends_on": [1]},
        {"index": 4, "issue": 305, "lane": "knowledge", "verify": "make verify", "depends_on": []},
        {"index": 5, "issue": 306, "lane": "foundation", "verify": "make verify", "depends_on": [4]},
        {"index": 6, "issue": 307, "lane": "foundation", "verify": "make verify", "depends_on": [2]},
    ],
    "dispatched": [],
}

# #306 is claimed and then released: the release must be honoured (it is not live).
CLAIM_EVENTS = [
    {"event": "claim", "issue": 304, "agent": "subagent-304", "at": "2026-09-13T22:00:00Z",
     "lane": "knowledge", "reason": "next-in-milestone", "ttl_hours": CLAIM_TTL_HOURS},
    {"event": "claim", "issue": 306, "agent": "subagent-306", "at": "2026-09-13T21:00:00Z",
     "lane": "foundation", "reason": "next-in-milestone", "ttl_hours": CLAIM_TTL_HOURS},
    {"event": "release", "issue": 306, "agent": "subagent-306", "at": "2026-09-13T21:05:00Z"},
    {"event": "claim", "issue": 309, "agent": "subagent-309", "at": "2026-09-13T22:10:00Z",
     "lane": "governance", "reason": "brain-directed", "ttl_hours": CLAIM_TTL_HOURS},
]

RUN_MARKERS = {
    "d-304": {"issue": 304, "agent": "subagent-304", "pid": 4242, "started_at": "2026-09-13T22:00:00Z"},
}

SLOG_RECORDS = [
    {"ts": "2026-09-13T21:00:00Z", "id": "s-1", "from": "brain", "to": "sister",
     "type": "directive", "correlation_id": "c-1", "issue": 305, "body": "spawn one subagent"},
    {"ts": "2026-09-13T22:20:00Z", "id": "s-2", "from": "sister", "to": "brain",
     "type": "result", "correlation_id": "c-2", "issue": 303, "body": "merged #303"},
    {"ts": "2026-09-13T22:30:00Z", "id": "s-3", "from": "sister", "to": "brain",
     "type": "escalate", "correlation_id": "c-3", "issue": 307, "severity": "warn",
     "body": "no isolated lane for #307"},
    {"ts": "2026-09-13T22:40:00Z", "id": "s-4", "from": "sister", "to": "brain",
     "type": "escalate", "correlation_id": "c-4", "issue": 307, "severity": "critical",
     "body": "verify failed twice: fleet/tests"},
]

TELEMETRY_RECORDS = [
    {"run_id": "r-301", "issue": "301", "agent": "subagent-301", "status": "done",
     "started_at": "2026-09-13T20:00:00Z", "finished_at": "2026-09-13T20:30:00Z", "detail": ""},
    {"run_id": "r-302", "issue": "302", "agent": "subagent-302", "status": "done",
     "started_at": "2026-09-13T20:40:00Z", "finished_at": "2026-09-13T21:10:00Z", "detail": "PR #310"},
    {"run_id": "r-306", "issue": "306", "agent": "subagent-306", "status": "failed",
     "started_at": "2026-09-13T21:20:00Z", "finished_at": "2026-09-13T21:35:00Z",
     "detail": "pytest rc=1"},
]


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def build_fleet(root: Path) -> Path:
    """A synthetic fleet covering every source the report reads."""
    write_json(root / ".board" / "snapshot.json", {
        "generated_at": "2026-09-13T21:24:51Z",
        "source": "kushin77/agent-orchestrator",
        "issues": BOARD_ISSUES,
    })
    write_jsonl(root / ".board" / "claims.jsonl", CLAIM_EVENTS)
    write_json(root / ".fleet" / "waves" / "299.json", WAVE_PLAN)
    for name, payload in RUN_MARKERS.items():
        write_json(root / ".fleet" / "runs" / f"{name}.json", payload)
    write_jsonl(root / ".fleet" / "slog.jsonl", SLOG_RECORDS)
    write_jsonl(root / ".fleet" / "runs.jsonl", TELEMETRY_RECORDS)
    return root


def state_for(tmp_path: Path) -> dict:
    """State read with a FIXED clock, so claim expiry cannot depend on the wall clock."""
    moment = datetime.fromisoformat(CLOCK.replace("Z", "+00:00")).astimezone(timezone.utc)
    return report.read_state(tmp_path, now=moment, generated_at=CLOCK)


def report_for(tmp_path: Path) -> report.Report:
    built = report.report_from(state_for(tmp_path))
    assert built is not None, "the synthetic fleet must produce a report"
    return built


def issues(section) -> list[int]:
    return [item.issue for item in section]


def find(section, issue: int):
    matches = [item for item in section if item.issue == issue]
    assert matches, f"#{issue} is missing from the section"
    return matches[0]


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------


def test_tail_records_bounds_the_read_and_drops_the_partial_first_line(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("".join('{"n": %d}\n' % i for i in range(50)), encoding="utf-8")
    assert [record["n"] for record in report.tail_records(path, 50)] == list(range(50))
    # A bounded read from the end starts mid-record; that fragment is not an outcome.
    assert [record["n"] for record in report.tail_records(path, 3, max_bytes=25)] == [48, 49]


def test_tail_records_skips_torn_lines_and_absent_files(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n{not json\n\n{"n": 2}\n', encoding="utf-8")
    assert [record["n"] for record in report.tail_records(path, 10)] == [1, 2]
    assert report.tail_records(tmp_path / "absent.jsonl", 5) == []


def test_read_wave_plans_orders_by_parent_and_ignores_non_plans(tmp_path):
    directory = tmp_path / "waves"
    write_json(directory / "240.json", {"parent": 240, "children": []})
    write_json(directory / "219.json", {"parent": 219, "children": []})
    (directory / "junk.json").write_text("[]", encoding="utf-8")
    assert [plan["parent"] for plan in report.read_wave_plans(directory)] == [219, 240]


def test_read_runs_skips_markers_without_an_issue(tmp_path):
    directory = tmp_path / "runs"
    write_json(directory / "d-1.json", {"issue": 7, "agent": "a", "pid": 1, "started_at": "t"})
    write_json(directory / "d-2.json", {"agent": "a", "pid": 1})
    (directory / "d-3.json").write_text("{torn", encoding="utf-8")
    runs = report.read_runs(directory)
    assert [run["issue"] for run in runs] == [7]
    assert runs[0]["directive_id"] == "d-1"


def test_read_board_reports_an_unusable_snapshot_rather_than_guessing(tmp_path):
    board = report.read_board(tmp_path / "snapshot.json")
    assert board["readable"] is False and board["issues"] == {} and board["frontier"] is None


def test_read_board_finds_the_milestone_and_its_frontier(tmp_path):
    build_fleet(tmp_path)
    board = report.read_board(tmp_path / ".board" / "snapshot.json")
    assert board["readable"] is True
    assert board["milestone"] == MILESTONE
    assert board["frontier"]["number"] == 300, "the frontier is the lowest open, unblocked non-epic issue"
    assert [entry["issue"] for entry in board["blocked"]] == [308]


def test_every_source_is_recorded_with_what_it_yielded(tmp_path):
    build_fleet(tmp_path)
    sources = state_for(tmp_path)["sources"]
    assert {name: entry["records"] for name, entry in sources.items()} == {
        "waves": 1, "claims": 2, "runs": 1, "outcomes": 4, "telemetry": 3, "board": 11, "verify": 0,
    }
    assert sources["claims"]["present"] is True, "a released claim is not live: 3 events fold to 2 holders"
    assert sources["waves"]["present"] is True
    assert sources["verify"]["present"] is False, ".verify/ is generated: absent here, and said so"


# ---------------------------------------------------------------------------
# the four sections
# ---------------------------------------------------------------------------


def test_now_lists_work_in_flight_and_merges_a_run_with_its_claim(tmp_path):
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    assert issues(built.now) == [304, 309], "one run marker and one live claim, in issue order"
    running = find(built.now, 304)
    assert running.state == "running" and running.agent == "subagent-304"
    assert running.lane == "knowledge"
    assert "run d-304" in running.evidence and "claim since" in running.evidence, "the claim corroborates the run"
    assert ".fleet/runs" == running.source
    claimed = find(built.now, 309)
    assert claimed.state == "claimed" and claimed.lane == "governance"
    assert "#306" not in [f"#{item.issue}" for item in built.now], "a released claim is not in flight"
    for item in built.now:
        assert item.evidence and item.source


def test_next_lists_unblocked_children_behind_the_milestone_frontier(tmp_path):
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    assert issues(built.next) == [300, 302, 303, 305]
    assert built.next[0].state == "frontier", "the plan starts at the active milestone's frontier"
    assert find(built.next, 302).evidence == "python3 -m pytest fleet/tests -q", (
        "a planned item carries the child's own Verify: command as its evidence pointer"
    )
    assert find(built.next, 305).lane == "knowledge"
    assert 304 not in issues(built.next), "a child whose dependency is unmet is not next"
    assert 301 not in issues(built.next), "a closed child is not next"


def test_blocked_names_what_each_item_waits_on(tmp_path):
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    waiting = {item.issue: item for item in built.blocked if item.state == "waiting"}
    assert sorted(waiting) == [304, 306, 307]
    assert waiting[304].evidence == "depends on #302"
    assert waiting[306].evidence == "depends on #305"
    assert waiting[307].evidence == "depends on #303"
    assert waiting[306].lane == "foundation"


def test_blocked_carries_board_blockers_failed_runs_and_critical_escalations(tmp_path):
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    states = {item.state for item in built.blocked}
    assert {"waiting", "blocked", "failed", "escalated"} <= states
    assert find([item for item in built.blocked if item.state == "blocked"], 308).evidence == "blocked by #300"
    failed = find([item for item in built.blocked if item.state == "failed"], 306)
    assert failed.evidence == "run r-306 failed: pytest rc=1" and failed.source == ".fleet/runs.jsonl"
    escalated = find([item for item in built.blocked if item.state == "escalated"], 307)
    assert "verify failed twice" in escalated.evidence and escalated.source == ".fleet/slog.jsonl"
    assert all(item.lane for item in built.blocked if item.issue != 308), "every attributable item names its lane"


def test_a_warning_escalation_is_not_a_blocker(tmp_path):
    """Only a critical escalation blocks; a warn is a note, not a stop."""
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    escalations = [item for item in built.blocked if item.state == "escalated"]
    assert [item.issue for item in escalations] == [307]
    assert "no isolated lane" not in " ".join(item.evidence for item in escalations)


def test_delivered_lists_closed_children_done_runs_and_reported_results(tmp_path):
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    assert issues(built.delivered) == [301, 302, 303]
    closed = find(built.delivered, 301)
    assert closed.state == "closed" and closed.source == ".fleet/waves/299.json"
    assert closed.evidence == "wave child of #299 closed"
    done = find(built.delivered, 302)
    assert done.state == "done" and done.evidence == "run r-302 finished 2026-09-13T21:10:00Z"
    reported = find(built.delivered, 303)
    assert reported.state == "reported" and reported.evidence == "result: merged #303"
    assert len([item for item in built.delivered if item.issue == 301]) == 1, (
        "one delivery per issue: the wave closure wins over the duplicate run record"
    )


def test_sections_are_independent_and_nothing_is_fabricated(tmp_path):
    """With no execution state at all, the plan still reports and the rest is empty."""
    write_json(tmp_path / ".fleet" / "waves" / "299.json", {
        "parent": 299,
        "children": [{"index": 0, "issue": 305, "lane": "knowledge", "verify": "make verify", "depends_on": []}],
        "dispatched": [],
    })
    built = report_for(tmp_path)
    assert issues(built.now) == [] and issues(built.blocked) == [] and issues(built.delivered) == []
    assert issues(built.next) == [305], "a wave plan alone is enough to know what is next"
    assert built.basis.milestone == "" and built.basis.frontier is None


# ---------------------------------------------------------------------------
# the verdict: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
# ---------------------------------------------------------------------------


def test_verdict_is_not_ok_when_anything_is_blocked(tmp_path):
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    assert built.blocked, "the fixture is blocked"
    assert report.verdict(built) == report.NOT_OK == 1


def test_verdict_is_ok_when_nothing_is_blocked(tmp_path):
    write_json(tmp_path / ".fleet" / "waves" / "299.json", {
        "parent": 299,
        "children": [{"index": 0, "issue": 305, "lane": "knowledge", "verify": "make verify", "depends_on": []}],
        "dispatched": [],
    })
    built = report_for(tmp_path)
    assert report.verdict(built) == report.OK == 0


def test_verdict_is_cannot_assess_when_there_is_no_plan(tmp_path):
    assert report.report_from(state_for(tmp_path)) is None
    assert report.verdict(None) == report.CANNOT_ASSESS == 2


def test_execution_logs_alone_are_not_a_plan(tmp_path):
    """Runs and events without a wave plan or a board are not a reportable plan."""
    write_jsonl(tmp_path / ".fleet" / "slog.jsonl", SLOG_RECORDS)
    write_jsonl(tmp_path / ".fleet" / "runs.jsonl", TELEMETRY_RECORDS)
    write_json(tmp_path / ".fleet" / "runs" / "d-304.json", RUN_MARKERS["d-304"])
    assert report.report_from(state_for(tmp_path)) is None


def test_main_exits_2_without_fabricating_a_report(tmp_path, capsys):
    code = report.main(["--json", "--root", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == report.CANNOT_ASSESS == 2
    assert captured.out == "", "no report is printed when there is nothing to report on"
    assert "CANNOT-ASSESS" in captured.err and "no wave plan and no board snapshot" in captured.err

    assert report.main(["--root", str(tmp_path)]) == 2
    assert capsys.readouterr().out == "", "the human mode refuses the same way"


# ---------------------------------------------------------------------------
# --json is a first-class mode
# ---------------------------------------------------------------------------


def test_json_round_trips_and_validates(tmp_path, capsys):
    build_fleet(tmp_path)
    code = report.main(["--json", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert code == report.NOT_OK
    assert report.validate_report(payload) == [], "the emitted payload must satisfy its own schema"
    assert payload["schema"] == report.SCHEMA
    assert payload["verdict"] == code, "the payload and the exit code cannot disagree"
    assert payload["counts"] == {"now": 2, "next": 4, "blocked": 6, "delivered": 3}
    for name in report.SECTIONS:
        assert payload["sections"][name] == [item.to_json() for item in getattr(report_for(tmp_path), name)]


def test_json_carries_the_evidence_basis_the_reader_needs_to_check_it(tmp_path, capsys):
    build_fleet(tmp_path)
    report.main(["--json", "--root", str(tmp_path)])
    basis = json.loads(capsys.readouterr().out)["basis"]
    assert basis["commit"], "the report names the commit it was derived at"
    assert basis["milestone"] == MILESTONE
    assert basis["frontier"]["number"] == 300
    assert [source["name"] for source in basis["sources"]] == [
        "waves", "claims", "runs", "outcomes", "telemetry", "board", "verify",
    ]
    assert basis["verify"] is None, "no attestation in this tree, and the report says so rather than implying one"


def test_json_reports_a_gate_verdict_when_the_tree_carries_one(tmp_path, capsys):
    build_fleet(tmp_path)
    write_json(tmp_path / ".verify" / "attestation.json", {
        "gate": "verify", "result": "PASS", "exit_code": 0,
        "git_sha": "0ff8adf59e32f39292d8188c45249a55ddb53a0e",
        "check_count": 25, "timestamp": "2026-09-13T23:25:51Z",
    })
    report.main(["--json", "--root", str(tmp_path)])
    basis = json.loads(capsys.readouterr().out)["basis"]
    assert basis["verify"]["result"] == "PASS" and basis["verify"]["check_count"] == 25


def test_validate_report_catches_a_malformed_payload():
    """Negative control: the validator the round-trip test relies on can fail."""
    good = {
        "schema": report.SCHEMA,
        "provenance": {"pattern_source": "x"},
        "basis": {"commit": "abc123"},
        "sections": {name: [] for name in report.SECTIONS},
    }
    assert report.validate_report(good) == []

    without_schema = dict(good, schema="something/else")
    assert any("schema" in problem for problem in report.validate_report(without_schema))

    no_commit = dict(good, basis={"commit": "  "})
    assert any("commit" in problem for problem in report.validate_report(no_commit))

    missing_field = dict(good, sections={**good["sections"], "now": [{"issue": 1}]})
    assert any("missing" in problem for problem in report.validate_report(missing_field))

    bad_section = dict(good, sections={**good["sections"], "blocked": "not a list"})
    assert any("must be a list" in problem for problem in report.validate_report(bad_section))

    assert report.validate_report("not an object") == ["report: payload must be a JSON object"]


# ---------------------------------------------------------------------------
# rendering + provenance
# ---------------------------------------------------------------------------


def test_render_carries_the_four_sections_and_the_provenance_header(tmp_path):
    build_fleet(tmp_path)
    frame = report.render(report_for(tmp_path))
    for title in ("NOW (2)", "NEXT (4)", "BLOCKED (6)", "DELIVERED (3)"):
        assert title in frame, f"the {title} section is missing"
    # Literal expectations, never `report.PROVENANCE[...] in frame`: a constant
    # compared against itself passes however the header is broken (#302 mutation
    # proof M5 caught exactly that).
    assert "pattern:  vendor/CMR/catalog/modules/paperclip (roadmaps, status-reports, skills-and-governance)" in frame
    assert "persona:  paperclip tier LOW \u00b7 registry/personas/cards/paperclip.yaml" in frame
    assert REPORT_HEADER_LINE in frame
    assert "verdict: NOT-OK (exit 1)" in frame


def test_render_states_the_evidence_basis_and_the_gate_verdict(tmp_path):
    """Issue #302: the status output states what was verified, at which commit."""
    build_fleet(tmp_path)
    write_json(tmp_path / ".verify" / "attestation.json", {
        "result": "PASS", "exit_code": 0, "git_sha": "0ff8adf59e32f39292d8188c45249a55ddb53a0e",
        "check_count": 25, "timestamp": "2026-09-13T23:25:51Z",
    })
    frame = report.render(report_for(tmp_path))
    assert "basis:" in frame and f"milestone {MILESTONE}" in frame and "frontier #300" in frame
    assert "verified: PASS (exit 0, 25 checks)" in frame
    assert "HEAD " in frame


def test_render_names_the_commit_it_was_derived_at(tmp_path):
    """A blank commit must fail: `"HEAD " in frame` alone passes for an empty sha."""
    build_fleet(tmp_path)
    built = report_for(tmp_path)
    assert built.basis.commit, "the report must name the commit it was derived at"
    assert f"HEAD {built.basis.commit}" in report.render(built)


def test_render_says_so_when_there_is_no_gate_verdict_to_cite(tmp_path):
    build_fleet(tmp_path)
    frame = report.render(report_for(tmp_path))
    assert "no .verify/attestation.json in this tree" in frame, (
        "an unverifiable tree must not be rendered as a verified one"
    )


def test_every_section_has_a_named_empty_fallback(tmp_path):
    write_json(tmp_path / ".fleet" / "waves" / "299.json", {
        "parent": 299,
        "children": [{"index": 0, "issue": 305, "lane": "knowledge", "verify": "make verify", "depends_on": []}],
        "dispatched": [],
    })
    frame = report.render(report_for(tmp_path))
    assert frame.count("(none)") == 3


def test_provenance_names_the_paperclip_module_and_the_registry_persona():
    """GR-10: the pattern this surface consumes, and the persona that owns it."""
    assert report.PROVENANCE["pattern_source"] == "vendor/CMR/catalog/modules/paperclip"
    assert report.PROVENANCE["persona"] == "registry/personas/cards/paperclip.yaml"
    assert report.PROVENANCE["persona_id"] == "paperclip"
    assert report.PROVENANCE["persona_tier"] == "LOW"
    assert "roadmaps" in report.PROVENANCE["pattern_features"]
    assert "status-reports" in report.PROVENANCE["pattern_features"]
    assert report.PROVENANCE["decision"] == "docs/decision-records/ADR-0012-hermes-paperclip-boundary.md"


# ---------------------------------------------------------------------------
# read-only, by construction and by measurement
# ---------------------------------------------------------------------------


def tree_digest(root: Path) -> dict[str, str]:
    """sha256 per file under `root`, so a write shows up as a changed digest."""
    digest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def test_the_report_never_writes_to_the_state_it_reads(tmp_path):
    """The report is read-only: `.fleet/`, the claims ledger and the board are untouched."""
    build_fleet(tmp_path)
    before = tree_digest(tmp_path)
    assert before, "the fixture must have written something to compare against"

    report.main(["--root", str(tmp_path)])
    report.main(["--json", "--root", str(tmp_path)])
    report.render(report_for(tmp_path))

    assert tree_digest(tmp_path) == before, "the report mutated the state it read"
