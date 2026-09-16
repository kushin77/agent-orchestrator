"""The authorisation directive's terminal move, provoked in both directions (#821).

The defect these tests exist for was measured: ``.fleet/sent/`` held 118 stranded
directives because ``fleet/channel.py consume`` reads only the inbox, a mailbox a
brain-minted directive never enters, so the remedy ``DIRECTIVE_NOT_CONSUMED`` named
could not consume the artifact it named. The fix is that the close-out owns the
move — and the whole point of owning it is that it is *gated*. So every case below
asserts the mailbox it inspects, the bytes it moves, and the refusal by name.

The mutation-landing assertions are load-bearing in the same spirit as the gate's
own negative controls: a harness that reports "moved" while the source file is
still there, or "refused" while the file changed underneath it, certifies nothing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from governance.lifecycle import directive
from governance.lifecycle.cli import _directive_for
from governance.lifecycle.directive import STATE_DONE, STATE_SENT, DirectiveRefused
from governance.policy import lease

from conftest import HEAD_COMMIT  # noqa: E402


def digest(path: Path) -> str:
    """The sha256 of a file's bytes, so a move can be proven to preserve them."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directive_file(root: Path, id_: str, issue: int | None, mailbox: str = "sent") -> Path:
    """Write a directive record into one of the mailboxes and return its path."""
    directory = root / ".fleet" / mailbox
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "body": f"order for #{issue}" if issue is not None else "order with no subject",
        "id": id_,
        "ts": "2026-09-15T00:00:00+00:00",
        "nonce": "0" * 32,
    }
    payload["task"] = {"kind": "work", "issue": issue} if issue is not None else {"kind": "work"}
    if issue is None:
        payload.pop("task")
    path = directory / f"{id_}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def landed(*items: dict) -> dict[int, bool]:
    return directive.landed_issues({"items": list(items)})


def merged(issue: int) -> dict:
    return {"issue": issue, "state": "open", "pr": {"state": "merged", "head_commit": HEAD_COMMIT}}


def unlanded(issue: int) -> dict:
    return {"issue": issue, "state": "open", "pr": {"state": "open"}}


# --- the move, and the fact it preserves the record -------------------------


def test_a_stranded_directive_for_a_landed_order_reaches_its_terminal_mailbox(tmp_path):
    source = directive_file(tmp_path, "brain-directive-821", 821)
    before = digest(source)

    detail = directive.consume(tmp_path, "brain-directive-821", landed(merged(821)))

    target = tmp_path / ".fleet" / "done" / "brain-directive-821.json"
    assert "consumed 1 directive(s) for #821" in detail
    assert not source.exists(), "the stranded record is still in sent/ — the move did not land"
    assert target.exists(), "the record did not arrive in done/"
    assert digest(target) == before, "the move changed the record's bytes; it must be the same file"
    assert directive.records(tmp_path) == [
        directive.Directive("brain-directive-821", 821, STATE_DONE, target)
    ]


def test_a_closed_issue_counts_as_landed_too(tmp_path):
    """``owes_closure`` is the model's definition, reused rather than restated."""
    directive_file(tmp_path, "brain-directive-900", 900)
    detail = directive.consume(tmp_path, "brain-directive-900", landed({"issue": 900, "state": "closed"}))
    assert "#900" in detail
    assert (tmp_path / ".fleet" / "done" / "brain-directive-900.json").exists()


def test_the_move_is_idempotent(tmp_path):
    directive_file(tmp_path, "brain-directive-821", 821)
    directive.consume(tmp_path, "brain-directive-821", landed(merged(821)))
    again = directive.consume(tmp_path, "brain-directive-821", landed(merged(821)))
    assert "already consumed" in again
    assert not list((tmp_path / ".fleet" / "sent").glob("*.json"))


def test_the_whole_stranded_set_for_an_issue_moves_in_one_pass(tmp_path):
    """One order, several authorisations: retiring one and leaving a sibling pending
    would reproduce the finding while ``sent/`` kept growing."""
    directive_file(tmp_path, "brain-directive-a2a-821", 821)
    directive_file(tmp_path, "brain-directive-sd-821", 821)
    other = directive_file(tmp_path, "brain-directive-999", 999)

    detail = directive.consume(tmp_path, "brain-directive-a2a-821", landed(merged(821), unlanded(999)))

    done = tmp_path / ".fleet" / "done"
    assert "consumed 2 directive(s)" in detail
    assert sorted(path.name for path in done.glob("*.json")) == [
        "brain-directive-a2a-821.json",
        "brain-directive-sd-821.json",
    ]
    assert other.exists(), "a directive for another issue was moved"
    assert directive.stranded(tmp_path, 821) == []


# --- the refusals, each by name, each leaving the mailbox alone -------------


def test_a_live_order_is_refused_by_name_and_stays_in_sent(tmp_path):
    """The negative control that keeps this from becoming a "consume anything" verb."""
    source = directive_file(tmp_path, "brain-directive-live", 900)
    before = digest(source)

    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(tmp_path, "brain-directive-live", landed(unlanded(900)))

    message = str(refused.value)
    assert "brain-directive-live" in message and "#900" in message
    assert "has not landed" in message and "live" in message
    assert source.exists() and digest(source) == before, "a refused order was moved anyway"
    assert not (tmp_path / ".fleet" / "done").exists()


def test_an_unknown_directive_is_refused_by_name(tmp_path):
    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(tmp_path, "brain-directive-nonexistent", landed(merged(821)))
    assert "unknown directive" in str(refused.value)
    assert "brain-directive-nonexistent" in str(refused.value)


def test_a_directive_that_names_no_issue_is_refused(tmp_path):
    """Undecidable is refused, never defaulted to allowed."""
    source = directive_file(tmp_path, "brain-directive-subjectless", None)
    before = digest(source)

    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(tmp_path, "brain-directive-subjectless", landed(merged(821)))

    assert "names no issue" in str(refused.value)
    assert source.exists() and digest(source) == before


def test_an_issue_outside_the_lifecycle_record_is_refused(tmp_path):
    source = directive_file(tmp_path, "brain-directive-unknown-issue", 4242)
    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(tmp_path, "brain-directive-unknown-issue", landed(merged(821)))
    assert "outside the lifecycle record" in str(refused.value)
    assert source.exists()


def test_a_record_that_cannot_be_read_is_refused_rather_than_guessed(tmp_path):
    directory = tmp_path / ".fleet" / "sent"
    directory.mkdir(parents=True)
    (directory / "brain-directive-corrupt.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(tmp_path, "brain-directive-corrupt", landed(merged(821)))
    assert "unreadable" in str(refused.value)
    assert (directory / "brain-directive-corrupt.json").exists()


def test_a_differing_terminal_record_is_refused_and_nothing_moves(tmp_path):
    """A collision is checked before the first move, so a refusal is never partial."""
    directive_file(tmp_path, "brain-directive-first", 821)
    directive_file(tmp_path, "brain-directive-second", 821)
    terminal = tmp_path / ".fleet" / "done"
    terminal.mkdir(parents=True)
    (terminal / "brain-directive-second.json").write_text('{"id": "a different edition"}\n', encoding="utf-8")

    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(tmp_path, "brain-directive-first", landed(merged(821)))

    assert "different content" in str(refused.value)
    assert sorted(path.name for path in (tmp_path / ".fleet" / "sent").glob("*.json")) == [
        "brain-directive-first.json",
        "brain-directive-second.json",
    ]


def test_a_byte_identical_duplicate_of_a_terminal_record_is_removed(tmp_path):
    source = directive_file(tmp_path, "brain-directive-821", 821)
    terminal = tmp_path / ".fleet" / "done"
    terminal.mkdir(parents=True)
    (terminal / "brain-directive-821.json").write_bytes(source.read_bytes())

    detail = directive.consume(tmp_path, "brain-directive-821", landed(merged(821)))

    assert "duplicate" in detail
    assert not source.exists()
    assert (terminal / "brain-directive-821.json").exists()


# --- landed_issues, and the report the invariant is decided on --------------


def test_landed_issues_reads_the_models_own_definition():
    record = {
        "items": [
            merged(821),
            {"issue": 900, "state": "closed"},
            unlanded(901),
        ]
    }
    assert directive.landed_issues(record) == {821: True, 900: True, 901: False}
    assert directive.landed_issues(None) == {}


def test_a_stranded_directive_wins_over_a_terminal_sibling(tmp_path):
    """Why the failure looked intermittent: an older directive already in ``done/``
    used to mask the one that actually dispatched the lane."""
    directive_file(tmp_path, "brain-directive-old", 821, mailbox="done")
    directive_file(tmp_path, "brain-directive-new", 821)

    assert _directive_for(821, root=tmp_path) == {"id": "brain-directive-new", "state": STATE_SENT}
    directive.consume(tmp_path, "brain-directive-new", landed(merged(821)))
    # The invariant is decided on the *state*, and that is what is pinned here.
    # After the stranded record is retired, both remaining records are terminal,
    # so ``records`` reports the one it meets first in ``done/`` (sorted): which
    # of two terminal siblings is named is not part of this reader's contract,
    # and asserting it would pin an incidental tie-break rather than the rule.
    after = _directive_for(821, root=tmp_path)
    assert after["state"] == STATE_DONE, after
    assert after["id"] in {"brain-directive-new", "brain-directive-old"}, after


def test_an_issue_with_no_directive_reports_nothing(tmp_path):
    directive_file(tmp_path, "brain-directive-old", 821, mailbox="done")
    assert _directive_for(900, root=tmp_path) == {}


# --- the supersession mode (#861): the one case the landed gate cannot serve ---
#
# The landed gate presumes the order's issue has a change of its own to have
# landed. A SUPERSEDED issue has none, so it is closed on the board, absent from
# the lifecycle record, and can never satisfy that predicate — measured on the live
# mailbox: #692 closed 2026-09-16, no `.fleet/lifecycle/692.json`, and its
# authorisation stranded in `.fleet/sent/` since 2026-09-15 with no command able to
# retire it.
#
# The mode added for it is deliberately NARROWER than the gate, not wider, and the
# suite below is written to prove both halves: it retires the strand, and it refuses
# everything the old gate refused. The refusal of an OPEN issue is the mandated
# negative control — the #821 invariant, without which this mode would be a bypass.


def board_file(path: Path, *entries: tuple[int, str], generated_at: str | None = None) -> Path:
    """A committed-board-shaped snapshot, with an edition that can be aged."""
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(
        json.dumps(
            {
                "generated_at": stamp,
                "source": "kushin77/agent-orchestrator",
                "issues": [
                    {"number": number, "title": f"issue {number}", "state": state}
                    for number, state in entries
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def fresh_board(tmp_path: Path, *entries: tuple[int, str]) -> Path:
    """A board inside the freshness bar, read the way the CLI reads one."""
    path = board_file(tmp_path / "snapshot.json", *entries)
    return directive.board_closure(path).closed


def test_a_superseded_issue_with_no_change_of_its_own_is_retired_and_stamped(tmp_path):
    """The deliverable: the strand becomes retirable through the code path.

    The record is MOVED and the move is RECORDED — reason, successor, board edition
    and who — because a retirement is a state change to a governed store and an
    unexplained disappearance cannot be audited afterwards.
    """
    source = directive_file(tmp_path, "brain-directive-a2a-692", 692)

    detail = directive.retire_superseded(
        tmp_path,
        "brain-directive-a2a-692",
        723,
        landed={},
        closed={692: True},
        board="2026-09-16T00:00:00Z",
        detail="#692 closed as superseded by #723/#727/#754",
    )

    target = tmp_path / ".fleet" / "done" / "brain-directive-a2a-692.json"
    assert "retired 1 directive(s) for #692" in detail and "#723" in detail, detail
    assert not source.exists(), "the stranded record is still in sent/"
    assert target.exists(), "the record did not arrive in done/"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["retired"] == {
        "reason": "superseded",
        "at": payload["retired"]["at"],
        "by": "governance/lifecycle/directive.py",
        "superseded_by": 723,
        "board": "2026-09-16T00:00:00Z",
        "detail": "#692 closed as superseded by #723/#727/#754",
    }, payload["retired"]
    # The original record survives the move: the stamp is added, nothing replaced.
    assert payload["id"] == "brain-directive-a2a-692" and payload["task"]["issue"] == 692
    assert directive.stranded(tmp_path, 692) == []
    # The reason is a declared one, not free text.
    assert payload["retired"]["reason"] in directive.SUPERSESSION_REASONS


def test_an_open_issue_is_refused_by_the_supersession_mode(tmp_path):
    """THE MANDATED NEGATIVE CONTROL — the #821 invariant, preserved.

    The whole risk of adding a terminal mode is that it becomes a bypass. It cannot:
    an order whose issue is OPEN on the board is refused here exactly as the landed
    gate refuses it, and the file is byte-for-byte where it was.
    """
    source = directive_file(tmp_path, "brain-directive-live", 900)
    before = digest(source)

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(
            tmp_path, "brain-directive-live", 901, landed={}, closed={900: False}
        )

    message = str(refused.value)
    assert "brain-directive-live" in message and "#900" in message
    assert "is OPEN" in message and "not retired by supersession" in message, message
    assert "#821" in message, "the refusal must name the invariant it is protecting"
    assert source.exists() and digest(source) == before, "a refused order was moved anyway"
    assert not (tmp_path / ".fleet" / "done").exists()


def test_a_supersession_of_an_issue_whose_change_has_not_landed_is_refused(tmp_path):
    """The second half of the same control: the close-out says the work is live.

    The board and the record disagree (closed vs unlanded), and a contradiction is
    refused rather than resolved in whichever direction is convenient.
    """
    source = directive_file(tmp_path, "brain-directive-conflict", 900)

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(
            tmp_path, "brain-directive-conflict", 901, landed={900: False}, closed={900: True}
        )

    assert "has NOT landed" in str(refused.value)
    assert source.exists()


def test_a_supersession_of_an_issue_the_closeout_can_see_is_refused(tmp_path):
    """The mode must not become the easier road past the gate it sits beside.

    When the close-out has collected the item, the ordinary landed move applies and
    is better evidenced, so this mode routes the operator to it instead of doing the
    move itself.
    """
    source = directive_file(tmp_path, "brain-directive-seen", 900)
    journal = tmp_path / ".fleet" / "lifecycle"
    journal.mkdir(parents=True)
    (journal / "900.json").write_text(json.dumps({"issue": 900}), encoding="utf-8")

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(
            tmp_path,
            "brain-directive-seen",
            901,
            landed={900: True},
            closed={900: True},
        )

    assert "ordinary landed gate applies" in str(refused.value)
    assert source.exists()


def test_a_supersession_with_no_successor_is_refused(tmp_path):
    """A supersession with no successor is an assertion, not evidence."""
    source = directive_file(tmp_path, "brain-directive-nosuccessor", 900)

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(
            tmp_path, "brain-directive-nosuccessor", 0, landed={}, closed={900: True}
        )

    assert "names no successor" in str(refused.value)
    assert source.exists()


def test_a_supersession_by_itself_is_refused(tmp_path):
    source = directive_file(tmp_path, "brain-directive-self", 900)

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(
            tmp_path, "brain-directive-self", 900, landed={}, closed={900: True}
        )

    assert "cannot have superseded itself" in str(refused.value)
    assert source.exists()


def test_a_supersession_without_a_closure_oracle_is_refused(tmp_path):
    """An undecidable subject is refused, never defaulted to allowed."""
    source = directive_file(tmp_path, "brain-directive-nooracle", 900)

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(tmp_path, "brain-directive-nooracle", 901, landed={})

    assert "closure oracle was not supplied" in str(refused.value)
    assert source.exists()


def test_a_supersession_of_an_issue_the_board_does_not_carry_is_refused(tmp_path):
    source = directive_file(tmp_path, "brain-directive-absent", 900)

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(
            tmp_path, "brain-directive-absent", 901, landed={}, closed={901: True}
        )

    assert "does not carry #900" in str(refused.value)
    assert "snapshot --from-github" in str(refused.value), "the refusal must name its remedy"
    assert source.exists()


def test_an_unknown_retirement_reason_is_refused_by_name(tmp_path):
    """A terminal move whose reason is unrecognised is not a move anybody can audit."""
    source = directive_file(tmp_path, "brain-directive-reason", 900)

    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(
            tmp_path,
            "brain-directive-reason",
            {},
            retirement=directive.Retirement(reason="because-i-say-so", superseded_by=901),
            closed={900: True},
        )

    assert "unknown retirement reason" in str(refused.value)
    assert source.exists()


def test_the_supersession_mode_is_idempotent(tmp_path):
    """A second run reports the terminal state rather than duplicating the move."""
    directive_file(tmp_path, "brain-directive-once", 692)
    directive.retire_superseded(tmp_path, "brain-directive-once", 723, landed={}, closed={692: True})

    again = directive.retire_superseded(
        tmp_path, "brain-directive-once", 723, landed={}, closed={692: True}
    )

    assert "already consumed" in again
    assert len(list((tmp_path / ".fleet" / "done").glob("*.json"))) == 1


def test_a_supersession_never_overwrites_a_differing_terminal_record(tmp_path):
    """Collision checking still happens before the first move, stamped or not."""
    directive_file(tmp_path, "brain-directive-clash", 692)
    terminal = tmp_path / ".fleet" / "done"
    terminal.mkdir(parents=True)
    (terminal / "brain-directive-clash.json").write_text('{"id": "another edition"}\n', encoding="utf-8")

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire_superseded(
            tmp_path, "brain-directive-clash", 723, landed={}, closed={692: True}
        )

    assert "different content" in str(refused.value)
    assert (tmp_path / ".fleet" / "sent" / "brain-directive-clash.json").exists()


def test_the_ordinary_gate_still_refuses_a_live_order(tmp_path):
    """The new mode changed nothing about the old one (the #821 invariant itself)."""
    source = directive_file(tmp_path, "brain-directive-unchanged", 900)

    with pytest.raises(DirectiveRefused) as refused:
        directive.consume(tmp_path, "brain-directive-unchanged", landed(unlanded(900)))

    assert "has not landed" in str(refused.value)
    assert source.exists()


# --- the closure oracle ------------------------------------------------------


def test_the_board_oracle_reads_closed_ness_from_the_snapshot(tmp_path):
    path = board_file(tmp_path / "snapshot.json", (692, "CLOSED"), (861, "OPEN"), (999, "open"))

    oracle = directive.board_closure(path)

    assert oracle.closed == {692: True, 861: False, 999: False}
    assert oracle.age_minutes < 1.0
    assert oracle.generated_at


def test_a_stale_board_is_refused_rather_than_believed(tmp_path):
    """A closure oracle that can be arbitrarily old would fail OPEN, which is worse
    than having none: 'the board said closed' is only evidence while it is a board."""
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = board_file(tmp_path / "snapshot.json", (692, "CLOSED"), generated_at=old)

    with pytest.raises(DirectiveRefused) as refused:
        directive.board_closure(path)

    message = str(refused.value)
    assert "is 2880.0m old" in message or "m old (bar" in message, message
    assert "snapshot --from-github" in message


def test_the_freshness_bar_is_the_dispatch_layers_own(tmp_path):
    """Harvested, never re-invented (GR-10): a second bar here would be free to drift."""
    assert directive.BOARD_MAX_AGE_MINUTES == lease.SNAPSHOT_STALENESS_MINUTES


def test_an_unreadable_board_is_cannot_assess_never_closed(tmp_path):
    with pytest.raises(DirectiveRefused) as refused:
        directive.board_closure(tmp_path / "absent.json")
    assert "cannot be read" in str(refused.value)

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(DirectiveRefused):
        directive.board_closure(broken)


def test_a_board_without_a_readable_edition_is_refused(tmp_path):
    """Without the edition, 'the board said closed' could not be checked afterwards."""
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"issues": []}) + "\n", encoding="utf-8")
    with pytest.raises(DirectiveRefused) as refused:
        directive.board_closure(path)
    assert "no readable generated_at" in str(refused.value)


# --- the operator's entry point ---------------------------------------------


def test_the_cli_retires_the_standing_strand(tmp_path, capsys):
    """The operator path, driven end to end — the record #692 is the live example of.

    The mailbox is NOT hand-edited and the move is NOT performed by hand: the CLI
    reads a board, routes through the same mode, and the artifact carries the stamp.
    """
    directive_file(tmp_path, "brain-directive-a2a-692-6b3951b31bb3db7d", 692)
    oracle = board_file(tmp_path / "snapshot.json", (692, "CLOSED"), (723, "CLOSED"))

    rc = directive.main(
        [
            "--root", str(tmp_path),
            "retire",
            "--directive", "brain-directive-a2a-692-6b3951b31bb3db7d",
            "--superseded-by", "723",
            "--board", str(oracle),
            "--detail", "closed as superseded by #723/#727/#754",
        ]
    )
    out = capsys.readouterr().out

    assert rc == directive.EXIT_OK, out
    assert "retired 1 directive(s) for #692" in out, out
    target = tmp_path / ".fleet" / "done" / "brain-directive-a2a-692-6b3951b31bb3db7d.json"
    assert target.exists() and not (
        tmp_path / ".fleet" / "sent" / "brain-directive-a2a-692-6b3951b31bb3db7d.json"
    ).exists()
    assert json.loads(target.read_text(encoding="utf-8"))["retired"]["superseded_by"] == 723
    assert capsys.readouterr().err == ""


def test_the_cli_refuses_an_open_issue_and_reports_it(tmp_path, capsys):
    """The mandated negative control, at the operator's own surface: rc 1, file untouched."""
    source = directive_file(tmp_path, "brain-directive-open", 861)
    oracle = board_file(tmp_path / "snapshot.json", (861, "OPEN"))

    rc = directive.main(
        [
            "--root", str(tmp_path),
            "retire",
            "--directive", "brain-directive-open",
            "--superseded-by", "900",
            "--board", str(oracle),
        ]
    )
    captured = capsys.readouterr()

    assert rc == directive.EXIT_NOT_OK, captured
    assert "REFUSED" in captured.err and "is OPEN" in captured.err, captured.err
    assert source.exists()


def test_the_cli_reports_a_stale_board_as_cannot_assess(tmp_path, capsys):
    """CANNOT-ASSESS is its own exit code — a stale board is not a refusal of the order."""
    directive_file(tmp_path, "brain-directive-stale", 692)
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    oracle = board_file(tmp_path / "snapshot.json", (692, "CLOSED"), generated_at=old)

    rc = directive.main(
        [
            "--root", str(tmp_path),
            "retire",
            "--directive", "brain-directive-stale",
            "--superseded-by", "723",
            "--board", str(oracle),
        ]
    )
    captured = capsys.readouterr()

    assert rc == directive.EXIT_CANNOT_ASSESS, captured
    assert "CANNOT-ASSESS" in captured.err, captured.err
    assert (tmp_path / ".fleet" / "sent" / "brain-directive-stale.json").exists()


def test_the_cli_status_lists_the_mailboxes_by_verb(tmp_path, capsys):
    """The mailboxes are read back through a verb, not by walking the filesystem."""
    directive_file(tmp_path, "brain-directive-pending", 692)
    directive_file(tmp_path, "brain-directive-terminal", 821, mailbox="done")

    rc = directive.main(["--root", str(tmp_path), "status"])
    out = capsys.readouterr().out

    assert rc == directive.EXIT_OK
    assert "1 pending in .fleet/sent/" in out and "1 terminal in .fleet/done/" in out, out
    assert "brain-directive-pending" in out and "brain-directive-terminal" not in out
