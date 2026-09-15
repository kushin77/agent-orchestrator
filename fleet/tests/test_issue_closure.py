"""#693 — the brain issues no directive for an issue the board says is CLOSED.

Measured before the guard: 17 directives for already-closed issues had accumulated
in the sister's inbox and wedged the terminal loop — every one of them naming work
the claim layer refuses `issue-closed`, i.e. a run that could never start.

The guard sits in `dispatch()`, the one funnel every directive passes through
(operator orders, wave children, the completion-triggered advance), and it reads
the COMMITTED board snapshot — the same artifact a claim is validated against —
BEFORE the sent-marker is written and BEFORE the channel is invoked.

Two things here are deliberately not assertions about intent:

* the stub channel RECORDS every send, so "nothing entered the inbox" is a file
  that was never written (or a log that stayed empty) rather than a claim;
* the refusal is paired with its own mutation control — the same order for an OPEN
  issue must still dispatch — so a guard that refused everything would fail here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import brain
import channel


def write_board(path: Path, *entries: tuple[int, str]) -> Path:
    """A committed-board-shaped snapshot.

    The states are written the way the REAL `.board/snapshot.json` stores them
    (uppercase `OPEN`/`CLOSED`), so the lower-casing `Issue.closed` relies on is
    exercised against the artifact's own shape rather than a friendlier one.
    """
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-14T00:00:00Z",
                "source": "kushin77/agent-orchestrator",
                "issues": [
                    {"number": number, "title": f"issue {number}", "state": state}
                    for number, state in entries
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _redirect_dispatch_markers(tmp_path, monkeypatch):
    """Keep sent-markers out of the live `.fleet/` tree (the cover test_brain.py uses).

    `conftest.RUNTIME_PATHS["brain"]` does not carry this path, and conftest is a
    shared file — so the redirect lives here rather than a test writing into the
    real fleet's dispatch-marker directory.
    """
    monkeypatch.setattr(brain, "DISPATCH_MARKERS", tmp_path / "brain" / "dispatched")


@pytest.fixture
def recorded_channel(tmp_path, monkeypatch):
    """A `fleet/channel.py` stand-in that records every send and exits 0."""

    def install() -> Path:
        log = tmp_path / "sent.jsonl"
        script = tmp_path / "recording-channel.py"
        script.write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(log)!r}).open('a', encoding='utf-8').write(sys.argv[-1] + '\\n')\n"
            "print('channel send: OK — queued for the sister')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(brain, "CHANNEL", str(script))
        return log

    return install


def workload(number: int, reference: str = "ao-693-closure") -> dict:
    """An operator work order naming one issue."""
    return {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "id": reference,
        "task": {"issue": number, "lane": "fleet"},
        "body": f"dispatch one subagent for issue #{number}",
    }


# --- the refusal -------------------------------------------------------------


def test_a_closed_issue_is_refused_and_nothing_is_sent(tmp_path, monkeypatch, recorded_channel):
    """The guard's own case: the board says closed, so no directive is issued."""
    log = recorded_channel()
    board = write_board(tmp_path / "snapshot.json", (42, "CLOSED"), (43, "OPEN"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)

    ok, report = brain.handle_order(workload(42))

    assert ok is False, "a closed issue must not be dispatched"
    assert "issue-closed" in report, f"the refusal must name the state it read: {report}"
    assert "#42" in report and "is closed" in report
    assert str(board) in report, "the refusal must name WHICH board said so"
    assert not log.exists(), f"the channel was invoked for a closed issue: {log.read_text()}"
    marker = brain.order_marker(workload(42))
    assert marker is not None and not marker.exists(), "a refused order must leave no sent-marker"


def test_the_same_order_dispatches_when_the_board_says_the_issue_is_open(
    tmp_path, monkeypatch, recorded_channel
):
    """The mutation control: the guard is state-driven, not a blanket refusal.

    Without this, a `dispatch()` that refused every order would satisfy the test
    above — a check that cannot fail is a formality (GR-12).
    """
    log = recorded_channel()
    board = write_board(tmp_path / "snapshot.json", (42, "CLOSED"), (43, "OPEN"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)

    ok, report = brain.handle_order(workload(43))

    assert ok is True and "dispatched #43" in report, report
    sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [message["task"]["issue"] for message in sent] == [43], sent
    assert sent[0]["from"] == "brain" and sent[0]["to"] == "sister"


def test_an_issue_absent_from_the_board_is_refused_by_name(tmp_path, monkeypatch, recorded_channel):
    """An absent issue is NOT an open issue, and the refusal names the remedy.

    This is the second dead-directive class the guard closes: the claim layer
    refuses an issue that is absent from the snapshot `unknown-issue` too, so a
    directive for one is dead on arrival — `handle_decompose` refreshes the board
    after filing children for exactly this reason.
    """
    log = recorded_channel()
    board = write_board(tmp_path / "snapshot.json", (43, "OPEN"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)

    ok, report = brain.handle_order(workload(9999))

    assert ok is False
    assert "unknown-issue" in report and "#9999" in report
    assert "snapshot --from-github" in report, "the refusal must name how to resolve it"
    assert not log.exists()


def test_an_unreadable_board_is_a_refusal_never_an_allow(tmp_path, monkeypatch, recorded_channel):
    """No board, no verdict: the brain does not dispatch what it cannot assess."""
    log = recorded_channel()
    absent = tmp_path / "absent.json"
    monkeypatch.setattr(brain, "BOARD_PATH", absent)

    ok, report = brain.handle_order(workload(43))

    assert ok is False and "cannot-assess" in report, report
    assert not log.exists()

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(brain, "BOARD_PATH", broken)
    ok, report = brain.handle_order(workload(43))
    assert ok is False and "cannot-assess" in report, report
    assert not log.exists()


def test_the_states_read_from_the_board_are_the_refusals_reported(tmp_path, monkeypatch):
    """The oracle's table is total, and only `open` is a licence to dispatch."""
    board = write_board(tmp_path / "snapshot.json", (42, "CLOSED"), (43, "OPEN"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)

    assert brain.board_issue_state(43)[0] == "open"
    assert brain.closure_refusal(43) is None
    assert brain.board_issue_state(42)[0] == brain.ISSUE_CLOSED
    assert brain.board_issue_state(9999)[0] == brain.ISSUE_UNKNOWN
    monkeypatch.setattr(brain, "BOARD_PATH", tmp_path / "absent.json")
    assert brain.board_issue_state(43)[0] == brain.BOARD_UNREADABLE
    assert brain.closure_refusal(43) is not None


# --- the internal paths that mint a directive for a specific issue ------------


def test_a_wave_child_that_closed_is_refused_before_it_is_dispatched(
    tmp_path, monkeypatch, recorded_channel
):
    """The wave advance goes through the same funnel, so it is covered too.

    A child filed by a decomposition can be closed by the time a later idle tick
    re-reads the plan (`advance_waves`); the guard refuses it there, and the open
    sibling still goes out — in the same pass.
    """
    log = recorded_channel()
    board = write_board(tmp_path / "snapshot.json", (301, "CLOSED"), (302, "OPEN"))
    monkeypatch.setattr(brain, "BOARD_PATH", board)
    monkeypatch.setattr(brain, "WAVES", tmp_path / "waves")
    brain.WAVES.mkdir(parents=True, exist_ok=True)
    plan = {
        "parent": 219,
        "children": [
            {"index": 0, "issue": 301, "lane": "fleet", "title": "closed child", "verify": "x", "depends_on": []},
            {"index": 1, "issue": 302, "lane": "fleet", "title": "open child", "verify": "y", "depends_on": []},
        ],
        "dispatched": [],
    }
    (brain.WAVES / "219.json").write_text(json.dumps(plan), encoding="utf-8")

    assert brain.advance_waves() == [302], "only the open child may be dispatched"

    sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [message["task"]["issue"] for message in sent] == [302]


def _advance_board(monkeypatch, board):
    """The completion advance's live board, in memory and offline."""
    monkeypatch.setattr(brain.snapshot_mod, "github_records", lambda repo: [])
    monkeypatch.setattr(brain.snapshot_mod, "build_snapshot", lambda records, source: board)
    monkeypatch.setattr(brain.claims, "read_ledger", lambda *a, **k: [])
    monkeypatch.setattr(brain.claims, "active_claims", lambda events: {})


def _board_with_child(child_state: str):
    parent = brain.snapshot_mod.Issue(10, "closed parent", state="closed")
    child = brain.snapshot_mod.Issue(11, "child of the parent", state=child_state, parent=10)
    return brain.snapshot_mod.Snapshot(
        generated_at="2026-09-14T00:00:00Z", source="test", issues={10: parent, 11: child}
    )


def test_the_completion_advance_never_offers_a_closed_issue(tmp_path, monkeypatch, recorded_channel):
    """`advance_candidates` reads `open_issues()`, so a closed child is not ready.

    The guard is the last line, not the only one: the advance's own candidate set
    cannot even contain a closed issue. Both halves are measured here — the closed
    child produces no directive AND no send, while the open child (the control)
    does, which is what stops this test from passing on a dead harness.
    """
    log = recorded_channel()
    _advance_board(monkeypatch, _board_with_child("closed"))
    monkeypatch.setattr(brain, "BOARD_PATH", write_board(tmp_path / "closed.json", (10, "CLOSED"), (11, "CLOSED")))
    assert brain.advance_ready() == []
    assert not log.exists(), "a closed child must not reach the channel"

    _advance_board(monkeypatch, _board_with_child("open"))
    monkeypatch.setattr(brain, "BOARD_PATH", write_board(tmp_path / "open.json", (10, "CLOSED"), (11, "OPEN")))
    assert brain.advance_ready() == [11]
    sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [message["task"]["issue"] for message in sent] == [11]


# --- the real board ----------------------------------------------------------


def test_the_refusal_fires_against_a_real_closed_issue_from_the_committed_board(
    monkeypatch, recorded_channel
):
    """The provocation uses the repository's OWN committed board, not a fixture.

    The numbers are read from `.board/snapshot.json` rather than hard-coded, so the
    test cannot rot when an issue closes, and the OPEN half is asserted in the same
    run: a guard that refused everything would fail it.
    """
    log = recorded_channel()
    board = brain.snapshot_mod.load(brain.BOARD_PATH)
    closed = sorted(issue.number for issue in board.issues.values() if issue.closed)
    open_issues = sorted(issue.number for issue in board.open_issues())
    assert closed and open_issues, "the committed board must carry both states for this proof"

    refused, report = brain.handle_order(workload(closed[0], reference="ao-693-real-closed"))
    assert refused is False and "issue-closed" in report, report

    dispatched, report = brain.handle_order(workload(open_issues[0], reference="ao-693-real-open"))
    assert dispatched is True, report

    sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [message["task"]["issue"] for message in sent] == [open_issues[0]], (
        "the only directive the channel saw must be the open issue's"
    )


# --- the loop: the issue's own acceptance criterion, end to end ---------------


class _Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_the_loop_reports_the_refusal_as_a_result_and_consumes_the_order(tmp_path, monkeypatch):
    """#693 as written: "write a `result` refusal naming `issue-closed` (and consume
    the order) instead of issuing a directive".

    So the operator reads a `result` in the brain's outbox, the order leaves the
    inbox for `done/`, and the channel CLI is invoked EXACTLY once — the watch — never
    for a send. Every one of those three is a file on disk, not a claim about intent.
    """
    monkeypatch.setattr(brain.signal, "signal", lambda *args: None)
    monkeypatch.setattr(brain, "BOARD_PATH", write_board(tmp_path / "snapshot.json", (42, "CLOSED")))
    calls = tmp_path / "channel-calls.jsonl"
    stub = tmp_path / "watch-channel.py"
    stub.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(calls)!r}).open('a', encoding='utf-8').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.write({json.dumps(json.dumps(workload(42, 'ao-693-loop')))!s})\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(brain, "CHANNEL", str(stub))
    channel.BRAIN_INBOX.mkdir(parents=True, exist_ok=True)
    (channel.BRAIN_INBOX / "ao-693-loop.json").write_text(
        json.dumps(workload(42, "ao-693-loop")), encoding="utf-8"
    )

    assert brain.loop(_Args(watch_timeout=0.02, once=True)) == 1, "a refusal is not a success"

    invoked = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert invoked == [["brain-inbox", "--timeout-seconds", "0.02", "--interval", "1"]], (
        f"the channel must be invoked for the watch and nothing else: {invoked}"
    )
    replies = [json.loads(path.read_text(encoding="utf-8")) for path in channel.BRAIN_OUTBOX.glob("*.json")]
    assert len(replies) == 1 and replies[0]["type"] == "result", replies
    assert "issue-closed" in replies[0]["body"], replies[0]
    assert (channel.BRAIN_DONE / "ao-693-loop.json").exists(), "the refused order must be consumed"
    assert not (channel.BRAIN_INBOX / "ao-693-loop.json").exists()
