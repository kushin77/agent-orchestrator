"""The operator's `drop` lever, proved effective from OUTSIDE the loop (#799).

THE DEFECT THIS SUITE IS WRITTEN AGAINST
`fleet/control.py drop` used to do exactly one thing: order the sister to
dead-letter a named directive. The order travelled by mailbox, so the remedy for a
loop that cannot drain its mailbox had to travel through the mailbox that loop was
not draining. Measured on 2026-09-15 (#799): the operator issued the drop, the
directive stayed drainable, the cycle re-executed it, and the order was eventually
retired by the runaway guard — 13 cycles later — while the operator's own lever
failed with `FileNotFoundError`. A control that only takes effect once the loop is
healthy is not a control.

WHAT IS ASSERTED, and the negative control beside each half
1. a drop acts on the FILE: with NO loop running, the order leaves the inbox and
   the dead-letter artifact is terminal, dispatchable() is False, and `channel
   watch` can never return it again. The negative control is the assertion made
   BEFORE the drop — the order must be present and dispatchable first, or "it is
   terminal afterwards" would pass on a loop that retires everything;
2. the retirement does NOT depend on the channel: `_send_control` is armed to
   fail, and the drop still retires. The relay is exercised on its own, for the
   one record this process may not move (a brain-minted authorisation, #821);
3. the #821 invariant is PRESERVED — a drop cannot retire an order whose change
   has already landed. Landed is True from either hermetic source (the directive
   already in `done/`, the issue CLOSED in the board snapshot), and it is refused
   BY NAME with the record left where it was. An UNDECIDABLE landed state is
   refused too (CANNOT-ASSESS, exit 2): the rule never defaults to allowed,
   because the defect it exists for is a lever that retires the wrong thing;
4. the record the verb writes is the SAME shape the automatic path writes, from
   the one implementation (`runaway.RECORD_FIELDS`), so the two callers cannot
   drift; only `dropped_by` differs, which is what tells them apart (issue #754);
5. a drop is idempotent — a second drop does not rewrite the first's artifact, so
   it cannot degrade the evidence it just produced;
6. a drop that names an order existing nowhere, or an id that is not a safe
   mailbox name, is refused rather than silently succeeding: a lever whose
   "success" is indistinguishable from a dead loop is not a lever.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

import control  # noqa: E402
import runaway  # noqa: E402


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A private fleet runtime dir + repo root, so no test can touch the live one.

    `control.ROOT` is the documented redirect (see `control.FLEET_SUBDIR`), and
    `FLEET_SUBDIR` is pinned relative so an `AO_FLEET_DIR` in the ambient
    environment cannot point this suite at somebody else's mailbox.
    """
    monkeypatch.setattr(control, "ROOT", tmp_path)
    monkeypatch.setattr(control, "FLEET_SUBDIR", Path(".fleet"))
    return tmp_path / ".fleet"


@pytest.fixture
def sent_controls(monkeypatch):
    """Record every relay instead of running the channel, and name them all."""
    sent: list[tuple[str, dict | None]] = []

    def fake_send(action, task=None, body=None):
        sent.append((action, task))

    monkeypatch.setattr(control, "_send_control", fake_send)
    return sent


def plant_order(fleet: Path, directive_id: str, issue: int, *, mailbox: str = "inbox") -> Path:
    """Write a work order into a mailbox, exactly as the channel would."""
    directory = fleet / mailbox
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": "2026-09-15T00:00:00Z",
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "model": {"tier": "flash", "thinking": "none"},
                "task": {"kind": "work", "issue": issue, "lane": "autonomous-ops"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def plant_snapshot(root: Path, issue: int, state: str | None) -> None:
    """The committed board snapshot, reduced to the one fact the landed test reads."""
    board = root / ".board"
    board.mkdir(parents=True, exist_ok=True)
    issues = [] if state is None else [{"number": issue, "state": state, "title": "t"}]
    (board / "snapshot.json").write_text(json.dumps({"issues": issues}) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_drop(*argv: str) -> int:
    return control.main(["drop", *argv])


# --- 1. effective with no loop running ---------------------------------------


def test_a_drop_with_no_loop_running_leaves_the_order_terminal(fleet, sent_controls):
    order = plant_order(fleet, "d-wedged", 799)
    plant_snapshot(control.ROOT, 799, "OPEN")

    # Negative control FIRST: the order is live and the drain path can return it,
    # so "terminal afterwards" cannot be satisfied by a verb that retires nothing.
    assert order.exists(), "the order must start in the inbox"
    assert runaway.dispatchable("d-wedged", base=fleet) is True, "the order must start dispatchable"

    rc = run_drop("--directive", "d-wedged", "--reason", "the lane is wedged on a stale snapshot")

    assert rc == 0, "a drop of a live inbox order must succeed"
    assert not order.exists(), "the order must LEAVE the inbox"
    artifact = fleet / "dead-letter" / "d-wedged.json"
    assert artifact.exists(), "the terminal artifact must exist"
    assert runaway.dead_lettered("d-wedged", base=fleet) is True
    assert runaway.dispatchable("d-wedged", base=fleet) is False, "watch must never return it again"
    record = runaway.record_shape(fleet, "d-wedged")
    assert record["reason"] == "the lane is wedged on a stale snapshot"
    assert str(record["dropped_by"]).startswith("operator:"), (
        "the record must name the operator as the dropper, not the guard"
    )
    assert record["issue"] == 799, "the artifact keeps the envelope, so the issue survives the drop"
    assert sent_controls == [], "the retirement must not depend on a message the loop has to process"


def test_the_retirement_does_not_need_the_channel(tmp_path, monkeypatch, fleet):
    """The provocation for the whole issue: arm the relay to fail, drop anyway."""
    plant_order(fleet, "d-no-loop", 799)
    plant_snapshot(control.ROOT, 799, "OPEN")

    def refuse(action, task=None, body=None):  # pragma: no cover - must never run
        raise AssertionError("the retirement must be effective WITHOUT the channel (#799)")

    monkeypatch.setattr(control, "_send_control", refuse)

    assert run_drop("--directive", "d-no-loop", "--reason", "no loop is running") == 0
    assert runaway.dead_lettered("d-no-loop", base=fleet) is True


# --- 2. the #821 invariant, preserved ---------------------------------------


def test_a_drop_cannot_retire_an_order_whose_change_landed(fleet, sent_controls, capsys):
    order = plant_order(fleet, "d-landed", 799)
    plant_snapshot(control.ROOT, 799, "CLOSED")

    rc = run_drop("--directive", "d-landed", "--reason", "operator thinks it is wedged")

    assert rc == 1, "retiring landed work must be refused"
    assert "#821" in capsys.readouterr().err, "the refusal must name the invariant it preserves"
    assert order.exists(), "a refused drop moves NOTHING"
    assert not (fleet / "dead-letter" / "d-landed.json").exists()
    assert sent_controls == [], "a refused drop relays nothing either"


def test_a_consumed_authorisation_counts_as_landed_without_a_snapshot(fleet, sent_controls):
    """The lifecycle may only consume a landed change (#821), so `done/` IS the fact."""
    order = plant_order(fleet, "d-consumed", 799)
    done = fleet / "done"
    done.mkdir(parents=True, exist_ok=True)
    (done / "d-consumed.json").write_text(json.dumps({"id": "d-consumed", "task": {"issue": 799}}))

    rc = run_drop("--directive", "d-consumed", "--reason", "looks wedged")

    assert rc == 1, "a consumed authorisation must not be dead-lettered"
    assert order.exists(), "the inbox order stays where it was"


def test_an_undecidable_landed_state_is_refused_never_defaulted(fleet, sent_controls, capsys):
    order = plant_order(fleet, "d-unknown", 799)
    # No snapshot at all: the landed question cannot be answered.

    rc = run_drop("--directive", "d-unknown", "--reason", "wedged")

    assert rc == 2, "CANNOT-ASSESS is not a pass"
    assert "CANNOT-ASSESS" in capsys.readouterr().err
    assert order.exists(), "an undecidable subject moves nothing"


def test_an_order_naming_no_issue_is_undecidable(fleet, sent_controls):
    directory = fleet / "inbox"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "d-subjectless.json").write_text(json.dumps({"id": "d-subjectless", "task": {}}))
    plant_snapshot(control.ROOT, 799, "CLOSED")

    assert run_drop("--directive", "d-subjectless", "--reason", "wedged") == 2


# --- 3. the record, the mailbox, and the two callers ------------------------


def test_a_second_drop_does_not_rewrite_the_first_record(fleet, sent_controls):
    plant_order(fleet, "d-twice", 799)
    plant_snapshot(control.ROOT, 799, "OPEN")
    assert run_drop("--directive", "d-twice", "--reason", "first") == 0
    artifact = fleet / "dead-letter" / "d-twice.json"
    before = digest(artifact)

    assert run_drop("--directive", "d-twice", "--reason", "second") == 0, "a repeat drop is idempotent"

    assert digest(artifact) == before, "a second drop must not degrade the evidence it produced"
    assert runaway.record_shape(fleet, "d-twice")["reason"] == "first"


def test_the_verb_and_the_guard_write_the_same_record_shape(fleet, sent_controls):
    plant_order(fleet, "d-verb", 799)
    plant_snapshot(control.ROOT, 799, "OPEN")
    assert run_drop("--directive", "d-verb", "--reason", "wedged") == 0

    plant_order(fleet, "d-auto", 799)
    runaway.dead_letter("d-auto", "claim refused", base=fleet, dropped_by="runaway-guard")

    verb = runaway.record_shape(fleet, "d-verb")
    auto = runaway.record_shape(fleet, "d-auto")
    assert set(verb) == set(auto) == set(runaway.RECORD_FIELDS), (
        "the two callers must produce one shape, from one implementation"
    )
    assert auto["dropped_by"] == "runaway-guard"
    assert verb["dropped_by"] != auto["dropped_by"], "the dropper is what tells the callers apart"


def test_a_brain_minted_authorisation_is_relayed_not_moved(fleet, sent_controls):
    """The one record this verb may not move (#821): relay it, and say so."""
    authorisation = plant_order(fleet, "d-authorised", 799, mailbox="sent")
    plant_snapshot(control.ROOT, 799, "OPEN")

    rc = run_drop("--directive", "d-authorised", "--reason", "the order is dead")

    assert rc == 0
    assert authorisation.exists(), "nothing but the lifecycle may move a sent record"
    assert [action for action, _task in sent_controls] == ["drop"], "the relay is the route here"
    task = sent_controls[0][1] or {}
    assert task.get("directive") == "d-authorised", "the relay must name the order, never itself (#754)"


def test_a_drop_for_an_order_that_exists_nowhere_is_refused(fleet, sent_controls, capsys):
    plant_snapshot(control.ROOT, 799, "OPEN")

    rc = run_drop("--directive", "d-phantom", "--reason", "wedged")

    assert rc == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err and "d-phantom" in err
    assert sent_controls == [], "a phantom order must not be relayed either"
    assert not (fleet / "dead-letter" / "d-phantom.json").exists()


def test_an_id_that_is_not_a_safe_mailbox_name_is_refused(fleet, sent_controls, capsys):
    plant_snapshot(control.ROOT, 799, "OPEN")

    rc = run_drop("--directive", "../../etc/passwd", "--reason", "wedged")

    assert rc == 1, "the id becomes a filename; a traversal is refused, not sanitised"
    assert "REFUSED" in capsys.readouterr().err
    assert sent_controls == []
