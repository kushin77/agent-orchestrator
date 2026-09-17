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
from pathlib import Path

import pytest

from governance.lifecycle import directive
from governance.lifecycle.cli import _directive_for
from governance.lifecycle.directive import STATE_DONE, STATE_SENT, DirectiveRefused

import importlib.util as _importlib_util  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_lifecycle_tests_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
HEAD_COMMIT = _conftest.HEAD_COMMIT


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


# --- retire: the terminal mode for a closed issue with no change of its own (#861) --
#
# ``consume()`` gates on "landed" (owes_closure): closed, or PR merged. That gate
# is exactly wrong for a SUPERSEDED issue — one closed by another issue's change,
# with no PR of its own — because such an issue is closed but its own change never
# lands, so it can never satisfy `landed`. ``retire()`` is the second terminal mode
# this filed: it gates on CLOSED (a fact the caller supplies, e.g. from a live
# `gh issue view`), requires a reason and at least one superseding issue, and
# refuses (the #821 invariant) an OPEN issue whose change has not landed — retire
# must never become a bypass for consume's gate.


def test_a_closed_superseded_issue_is_retireable(tmp_path):
    source = directive_file(tmp_path, "brain-directive-a2a-692", 692)
    before = digest(source)

    detail = directive.retire(
        tmp_path,
        "brain-directive-a2a-692",
        closed=True,
        reason="superseded",
        superseded_by=(723, 727, 754),
    )

    target = tmp_path / ".fleet" / "done" / "brain-directive-a2a-692.json"
    assert "retired" in detail and "#692" in detail
    assert not source.exists(), "the stranded record is still in sent/ — the retire did not move it"
    assert target.exists(), "the record did not arrive in done/"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["retired"] is True
    assert payload["retirement_reason"] == "superseded"
    assert payload["superseded_by"] == [723, 727, 754]
    # the original bytes are extended, not discarded — the id/body survive.
    assert payload["id"] == "brain-directive-a2a-692"


def test_the_692_negative_control_an_open_issue_is_not_retireable(tmp_path):
    """The #821 invariant, preserved: retire must not become a bypass for consume's
    'has the change landed' gate. An OPEN issue whose change has not landed must be
    refused, exactly like ``consume()`` refuses it."""
    source = directive_file(tmp_path, "brain-directive-open", 900)
    before = digest(source)

    with pytest.raises(DirectiveRefused) as refused:
        directive.retire(
            tmp_path,
            "brain-directive-open",
            closed=False,
            reason="superseded",
            superseded_by=(901,),
        )

    message = str(refused.value)
    assert "brain-directive-open" in message and "#900" in message
    assert "not closed" in message or "open" in message
    assert source.exists() and digest(source) == before, "a refused order was moved anyway"
    assert not (tmp_path / ".fleet" / "done").exists()


def test_retire_requires_a_superseding_issue(tmp_path):
    directive_file(tmp_path, "brain-directive-692", 692)
    with pytest.raises(DirectiveRefused) as refused:
        directive.retire(tmp_path, "brain-directive-692", closed=True, reason="superseded", superseded_by=())
    assert "superseded_by" in str(refused.value) or "superseding" in str(refused.value)


def test_retire_is_idempotent(tmp_path):
    directive_file(tmp_path, "brain-directive-692", 692)
    directive.retire(tmp_path, "brain-directive-692", closed=True, reason="superseded", superseded_by=(723,))
    again = directive.retire(tmp_path, "brain-directive-692", closed=True, reason="superseded", superseded_by=(723,))
    assert "already" in again


def test_retire_refuses_a_directive_naming_no_issue(tmp_path):
    source = directive_file(tmp_path, "brain-directive-subjectless2", None)
    with pytest.raises(DirectiveRefused) as refused:
        directive.retire(
            tmp_path, "brain-directive-subjectless2", closed=True, reason="superseded", superseded_by=(1,)
        )
    assert "names no issue" in str(refused.value)
    assert source.exists()
