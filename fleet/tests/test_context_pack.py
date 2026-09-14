"""The dispatch context pack (#220) — what a subagent is TOLD, not just ordered.

A directive used to carry the operator's prose and nothing else, so a subagent
rediscovered the issue it was ordered to do: its acceptance criteria, its lane,
and, most expensively, the lessons a previous lane already paid for. These tests
assert the pack by effect:

* the prompt really carries the issue title, body (acceptance text), lane,
  ``Verify:`` clause and the relevant lessons — and drops the irrelevant ones;
* a missing body is a NAMED WARNING, never a silent empty pack;
* the default pack is OFFLINE (it reads the committed snapshot and never calls
  the board) while a live read is only ever a labelled fallback;
* the pack is RECORDED WITH THE RUN, so a reviewer can read what was dispatched —
  and the heartbeat refresh keeps that record instead of rewriting it away.
"""

from __future__ import annotations

import json
from pathlib import Path

import terminal

VERIFY = "python3 -m pytest fleet/tests -q"
ACCEPTANCE = "the pack carries the issue title, body, lane, Verify clause and prior lessons"
BODY = (
    "**Finding.** a subagent receives the operator's prose and nothing else.\n\n"
    f"**Acceptance.** {ACCEPTANCE}\n\n"
    f"**Verify:** {VERIFY}"
)


def write_snapshot(path: Path, issue: int, **entry) -> Path:
    """A board snapshot holding exactly one issue (the shape ``snapshot_issue`` reads)."""
    payload = {
        "generated_at": "2026-09-13T00:00:00Z",
        "source": "kushin77/agent-orchestrator",
        "issues": [{"number": issue, "title": "", "state": "OPEN", **entry}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_ledger(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# acceptance: the prompt carries issue + lane + Verify + up to N lessons
# --------------------------------------------------------------------------


def test_pack_carries_the_title_body_lane_verify_clause_and_relevant_lessons(tmp_path):
    snapshot = write_snapshot(
        tmp_path / "snapshot.json", 220, title="fleet terminal: dispatch a context pack", body=BODY
    )
    ledger = write_ledger(
        tmp_path / "ledger.jsonl",
        [
            {
                "id": "LESSON-0001",
                "kind": "lesson",
                "class": "faang",
                "title": "dispatch a context pack so a subagent is told the acceptance criteria",
            },
            {
                "id": "LESSON-0002",
                "kind": "lesson",
                "class": "class",
                "title": "rotate the database password quarterly",
            },
        ],
    )

    pack = terminal.issue_context(220, "fleet", snapshot_path=snapshot, ledger_path=ledger)

    assert pack["issue"] == 220
    assert pack["lane"] == "fleet"
    assert "context pack" in pack["title"]
    assert ACCEPTANCE in pack["body"]
    assert pack["verify"] == VERIFY
    assert [lesson["id"] for lesson in pack["lessons"]] == ["LESSON-0001"]
    assert pack["warnings"] == []

    prompt = terminal.build_prompt({"id": "d-1", "task": {"issue": 220, "lane": "fleet"}}, context=pack)

    assert ACCEPTANCE in prompt, "the acceptance text is not in the dispatched prompt"
    assert "Lane: fleet" in prompt
    assert f"`{VERIFY}`" in prompt
    assert "LESSON-0001" in prompt
    assert "LESSON-0002" not in prompt, "an irrelevant lesson was padded into the pack"


def test_build_command_carries_the_pack_in_the_final_prompt_argument(tmp_path):
    pack = terminal.issue_context(
        220,
        "fleet",
        snapshot_path=write_snapshot(tmp_path / "s.json", 220, title="t", body=BODY),
        ledger_path=tmp_path / "none.jsonl",
    )
    command = terminal.build_command(
        {"id": "d-1", "task": {"issue": 220}}, "claude -p", "subagent-d1", context=pack
    )
    assert ACCEPTANCE in command[-1]


def test_build_prompt_builds_an_offline_pack_when_none_is_supplied(tmp_path, monkeypatch):
    monkeypatch.setattr(
        terminal, "BOARD_SNAPSHOT", write_snapshot(tmp_path / "s.json", 777, title="offline title", body=BODY)
    )
    monkeypatch.setattr(terminal, "LESSONS_LEDGER", tmp_path / "none.jsonl")
    monkeypatch.setattr(terminal, "gh_issue_field", lambda *a, **k: None)

    prompt = terminal.build_prompt({"id": "d-9", "task": {"issue": 777}})

    assert "offline title" in prompt
    assert ACCEPTANCE in prompt


# --------------------------------------------------------------------------
# acceptance: a missing body degrades to a NAMED WARNING, never an empty pack
# --------------------------------------------------------------------------


def test_a_missing_body_is_a_named_warning_not_a_silent_empty_pack(tmp_path):
    snapshot = write_snapshot(tmp_path / "snapshot.json", 220, title="a title with no body")

    pack = terminal.issue_context(220, "fleet", snapshot_path=snapshot, ledger_path=tmp_path / "none.jsonl")

    assert pack["body"] == ""
    assert pack["verify"] is None
    assert pack["warnings"], "a missing body must be reported, not silently empty"
    warning = pack["warnings"][0]
    assert "#220" in warning
    assert "body" in warning

    prompt = terminal.build_prompt({"id": "d-1", "task": {"issue": 220}}, context=pack)
    assert "WARNING:" in prompt
    assert "#220" in prompt


def test_an_issue_absent_from_the_snapshot_is_named_in_a_warning(tmp_path):
    snapshot = write_snapshot(tmp_path / "snapshot.json", 1, title="some other issue")

    pack = terminal.issue_context(220, "fleet", snapshot_path=snapshot, ledger_path=tmp_path / "none.jsonl")

    assert pack["title"] == ""
    assert any("#220" in warning and "snapshot" in warning for warning in pack["warnings"])


def test_a_torn_snapshot_is_cannot_assess_not_a_crash(tmp_path):
    broken = tmp_path / "snapshot.json"
    broken.write_text("{ not json", encoding="utf-8")

    pack = terminal.issue_context(220, "fleet", snapshot_path=broken, ledger_path=tmp_path / "none.jsonl")

    assert pack["title"] == ""
    assert pack["body"] == ""
    assert any("#220" in warning for warning in pack["warnings"])


# --------------------------------------------------------------------------
# offline-safe: the snapshot is the source; the live board is a labelled fallback
# --------------------------------------------------------------------------


def test_the_default_pack_never_calls_the_board(tmp_path, monkeypatch):
    """Offline safety, proved by effect: the live read is armed to explode."""
    snapshot = write_snapshot(tmp_path / "snapshot.json", 220, title="offline only")
    monkeypatch.setattr(terminal, "BOARD_SNAPSHOT", snapshot)
    monkeypatch.setattr(terminal, "LESSONS_LEDGER", tmp_path / "none.jsonl")

    def explode(*args, **kwargs):  # noqa: ARG001 - signature must match the live read
        raise AssertionError("the default context pack must not read the board over the network")

    monkeypatch.setattr(terminal, "gh_issue_field", explode)

    pack = terminal.issue_context(220, "fleet")

    assert pack["body"] == ""
    assert pack["sources"]["body"] == "unavailable"
    assert pack["sources"]["snapshot"] == str(snapshot)


def test_the_snapshot_body_wins_over_the_live_read(tmp_path):
    snapshot = write_snapshot(tmp_path / "snapshot.json", 220, title="t", body=BODY)

    pack = terminal.issue_context(
        220,
        "fleet",
        snapshot_path=snapshot,
        ledger_path=tmp_path / "none.jsonl",
        body_reader=lambda issue: "SHOULD NOT BE USED",
    )

    assert ACCEPTANCE in pack["body"]
    assert "SHOULD NOT BE USED" not in pack["body"]
    assert pack["sources"]["body"] == "board-snapshot"


def test_the_live_read_is_the_labelled_fallback_when_the_snapshot_has_no_body(tmp_path):
    snapshot = write_snapshot(tmp_path / "snapshot.json", 220, title="t")

    pack = terminal.issue_context(
        220,
        "fleet",
        snapshot_path=snapshot,
        ledger_path=tmp_path / "none.jsonl",
        body_reader=lambda issue: BODY,
    )

    assert ACCEPTANCE in pack["body"]
    assert pack["sources"]["body"] == "live-board"
    assert pack["warnings"] == []


# --------------------------------------------------------------------------
# lessons: relevance is mechanical, and a quota is not a reason to pad
# --------------------------------------------------------------------------


def test_a_record_naming_the_issue_outranks_keyword_overlap(tmp_path):
    records = [
        {"id": "LESSON-0100", "kind": "lesson", "title": "dispatch ordering for an unknown issue"},
        {
            "id": "INC-0009",
            "kind": "incident",
            "summary": "an earlier lane already paid for this",
            "origin": {"kind": "issue", "ref": "#220"},
        },
    ]

    picked = terminal.relevant_lessons(220, "dispatch a context pack", records=records, limit=5)

    assert [record["id"] for record in picked][0] == "INC-0009"


def test_the_lesson_limit_is_respected():
    records = [
        {"id": f"LESSON-{index:04d}", "kind": "lesson", "title": "dispatch a context pack"}
        for index in range(9)
    ]

    assert len(terminal.relevant_lessons(220, "dispatch a context pack", records=records, limit=3)) == 3


def test_an_irrelevant_ledger_yields_no_lessons_not_noise():
    records = [{"id": "LESSON-0002", "kind": "lesson", "title": "rotate the database password"}]

    assert terminal.relevant_lessons(220, "dispatch a context pack", records=records) == []


# --------------------------------------------------------------------------
# acceptance: the pack is RECORDED WITH THE RUN
# --------------------------------------------------------------------------


def test_the_context_pack_is_recorded_with_the_run(tmp_path):
    pack = terminal.issue_context(
        220,
        "fleet",
        snapshot_path=write_snapshot(tmp_path / "s.json", 220, title="t", body=BODY),
        ledger_path=tmp_path / "none.jsonl",
    )

    terminal.mark_run("d-ctx", 220, "subagent-dctx", context=pack)

    marker = json.loads((terminal.RUNS / "d-ctx.json").read_text(encoding="utf-8"))
    assert ACCEPTANCE in marker["context_pack"]["body"]
    assert marker["context_pack"]["lane"] == "fleet"
    assert marker["context_pack"]["sources"]["snapshot"] == str(tmp_path / "s.json")
    assert "issue=#220" in terminal.context_summary(pack)
    assert "body=yes" in terminal.context_summary(pack)


def test_the_heartbeat_refresh_keeps_the_recorded_pack():
    pack = {
        "issue": 220,
        "lane": "fleet",
        "title": "t",
        "body": BODY,
        "verify": VERIFY,
        "lessons": [],
        "warnings": [],
        "sources": {},
    }
    terminal.mark_run("d-ctx2", 220, "a", context=pack)

    terminal.refresh_run("d-ctx2", child_pid=4242)

    marker = json.loads((terminal.RUNS / "d-ctx2.json").read_text(encoding="utf-8"))
    assert marker["child_pid"] == 4242
    assert marker["context_pack"]["body"] == BODY


def test_a_run_without_a_pack_records_no_pack_field():
    """The pack is additive: an older caller that passes none is unchanged."""
    terminal.mark_run("d-nopack", 220, "a")

    marker = json.loads((terminal.RUNS / "d-nopack.json").read_text(encoding="utf-8"))
    assert "context_pack" not in marker
    assert marker["issue"] == 220
