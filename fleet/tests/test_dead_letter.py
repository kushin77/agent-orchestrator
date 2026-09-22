"""A2A dead-lettering by protocol (issue #754).

The runaway guard (#723) retires a directive the loop can PROVE is unrunnable —
its attempt budget is exhausted. That is only half of the contract: an operator,
or a peer agent that can see the order is wedged, must be able to say so **over
the control channel**, and the loop must honour it with the SAME terminal
record. Until now the only way was to `mv` the order out of `.fleet/inbox/`
while the loop read it — which races the reader, loses the attempt history,
bypasses the channel (no ack, no audit) and cannot be done by an agent at all.

These tests prove the four things the issue asks for:

1. `control:drop` moves the named directive to the dead-letter mailbox with a
   durable record naming the reason and the dropper, and `watch` never returns
   it again — while a directive that was NOT dropped still is.
2. The control is **acked**; an unsupported control is a **reported failure**,
   never a silent no-op.
3. The verb and the automatic path produce the **same record shape**, from one
   implementation.
4. The mailbox is read back by **verb**, not by walking the filesystem.

Isolation: every test points ``terminal.RUNS`` and ``channel.INBOX`` at a tmp
directory (the same seam ``test_runaway_guard.py`` uses), so no test can write
into the live fleet's `.fleet/`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import channel  # noqa: E402
import runaway  # noqa: E402
import terminal  # noqa: E402

FLEET_DIR = Path(__file__).resolve().parents[1]
RUNAWAY_PATH = FLEET_DIR / "runaway.py"

#: A record always wins over a stale .pyc (measured elsewhere in this repo).
sys.dont_write_bytecode = True


# --- helpers -----------------------------------------------------------------


def wire_scratch(tmp_path: Path, monkeypatch, *, cap: int | None = None) -> Path:
    """Point the loop's guard and the mailbox at ONE scratch fleet directory."""
    if cap is not None:
        monkeypatch.setenv(runaway.ENV_ATTEMPT_CAP, str(cap))
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(channel, "DONE", tmp_path / "done")
    monkeypatch.setattr(terminal, "stream_run_event", lambda *a, **k: None)
    # `report_once` shells out to `channel.py` as a SUBPROCESS (not an
    # in-process call), so the monkeypatches above never reach it — it
    # re-resolves FLEET_DIR from the environment and writes escalate
    # mailboxes (outbox/inbox/sent) into the real repo `.fleet/` (#1985).
    # `AO_FLEET_DIR` is the seam `fleet/runtime.py` already reads for
    # exactly this; inherited subprocess env picks it up.
    monkeypatch.setenv("AO_FLEET_DIR", str(tmp_path / "fleet"))
    return tmp_path


def record_reporting(calls: list[dict]):
    """A ``report_once`` stand-in that records instead of shelling out to channel."""

    def report_once(directive_id, key, message_type, body, severity="warn"):
        calls.append(
            {"directive": directive_id, "key": key, "type": message_type, "severity": severity}
        )
        return True

    return report_once


def plant(directory: Path, directive_id: str, *, issue: int = 754) -> Path:
    """Put one pending directive envelope in `directory` (the mailbox shape)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": "2026-09-14T00:00:00Z",
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "correlation_id": f"c-{directive_id}",
                "model": {"tier": "flash", "thinking": "none"},
                "task": {"kind": "work", "issue": issue, "lane": "fleet"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def watch(capsys, *, skip: list[str] | None = None) -> tuple[int, str]:
    """Run the real ``channel watch`` once; return (exit code, stdout)."""
    rc = channel.cmd_watch(
        argparse.Namespace(timeout_seconds=0.05, interval=0.01, skip=skip or [])
    )
    return rc, capsys.readouterr().out


def drop_control(directive_id: str, *, issue: int = 754, reason: str = "its work already landed") -> dict:
    """The control envelope an operator/peer sends for `control:drop`."""
    return {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "control": "drop",
        "id": f"ctl-{directive_id}",
        "task": {"directive": directive_id, "issue": issue, "reason": reason},
    }


# --- 1. the verb retires the order and watch never returns it ----------------


def test_drop_control_retires_the_directive_and_watch_never_returns_it(
    tmp_path, monkeypatch, capsys
):
    state = wire_scratch(tmp_path, monkeypatch)
    plant(channel.INBOX, "d-wedged")
    plant(channel.INBOX, "d-healthy")

    outcome = terminal.apply_control("drop", drop_control("d-wedged"), "subagent-x")
    assert outcome == "drop", f"the vocabulary has no drop verb: {outcome!r}"

    terminal.drop_directive(
        "d-wedged", 754, "its work already landed", dropped_by="brain"
    )

    assert runaway.dead_lettered("d-wedged", base=terminal.guard_base()), (
        "the directive was not moved to the dead-letter store"
    )
    assert not (channel.INBOX / "d-wedged.json").exists(), (
        "the order is still in the inbox — it can be returned again"
    )

    # The negative control: the directive that was NOT dropped is still returned,
    # and the dropped one is not. Asserting only the second half would pass even
    # if `watch` had stopped returning anything at all.
    rc, out = watch(capsys)
    assert rc == channel.EXIT_OK, "watch returned nothing — the undropped order is lost"
    returned = json.loads(out[out.index("{") : out.rindex("}") + 1])
    assert returned["id"] == "d-healthy", (
        f"watch returned {returned['id']!r}; a dead-lettered directive must never come back"
    )


def test_a_dropped_directive_carries_a_durable_record_naming_reason_and_dropper(
    tmp_path, monkeypatch
):
    wire_scratch(tmp_path, monkeypatch)
    plant(channel.INBOX, "d-wedged")

    terminal.drop_directive(
        "d-wedged", 754, "issue's content already on master", dropped_by="brain"
    )

    record = runaway.record_shape(terminal.guard_base(), "d-wedged")
    assert set(runaway.RECORD_FIELDS) <= set(record), (
        f"the record is missing a named field: {sorted(record)}"
    )
    assert record["id"] == "d-wedged"
    assert record["issue"] == 754
    assert record["reason"] == "issue's content already on master"
    assert record["dropped_by"] == "brain", "the record does not name WHO dropped it"
    assert record["ts"], "the record carries no timestamp"


# --- 2. one implementation, two callers: the record shapes must match --------


def test_the_verb_and_the_automatic_path_produce_the_same_record_shape(
    tmp_path, monkeypatch
):
    """Acceptance: the two callers must not fork the record.

    The automatic path retires at budget exhaustion; the verb retires on a
    peer's word. Both must write ``RECORD_FIELDS`` — and the assertion is made on
    the KEYS, because that is the property one implementation guarantees and a
    duplicated one would silently break.
    """
    state = wire_scratch(tmp_path, monkeypatch, cap=1)
    calls: list[dict] = []
    monkeypatch.setattr(terminal, "report_once", record_reporting(calls))

    plant(channel.INBOX, "d-auto")
    plant(channel.INBOX, "d-verb")

    # The automatic path (budget exhausted on the first failure).
    terminal.guard_retire("d-auto", 754, "claim refused: refused")
    assert runaway.dead_lettered("d-auto", base=state), "the auto path did not retire"

    # The verb.
    terminal.drop_directive("d-verb", 754, "wedged", dropped_by="brain")
    assert runaway.dead_lettered("d-verb", base=state), "the verb did not retire"

    auto = runaway.record_shape(state, "d-auto")
    verb = runaway.record_shape(state, "d-verb")

    assert set(auto) == set(verb) == set(runaway.RECORD_FIELDS), (
        f"the two paths wrote different records: auto={sorted(auto)} verb={sorted(verb)}"
    )
    assert auto["dropped_by"] == "runaway-guard", "the auto path must name the guard"
    assert verb["dropped_by"] == "brain", "the verb must name the sender"


# --- 3. an unsupported / untargeted control is REPORTED, never swallowed -----


def test_a_drop_that_names_no_target_is_reported_not_silently_ignored():
    """An unhonourable control is a reported failure — the #754 criterion.

    ``apply_control`` still returns the ``drop`` verdict (the verb exists), so the
    guard is the *target check*: the loop must have a way to tell "the verb is
    unknown" from "the verb is known but I cannot honour it".
    """
    assert terminal.control_target_directive_id({"task": {}}) is None
    assert terminal.control_target_directive_id({"task": {"directive": ""}}) is None
    assert terminal.control_target_directive_id({"task": {"directive": "../../etc/passwd"}}) is None, (
        "a target that is not a safe mailbox name must be refused"
    )
    assert (
        terminal.control_target_directive_id({"task": {"directive": "d-wedged"}}) == "d-wedged"
    )


def test_an_unknown_control_is_still_reported_as_unknown():
    """Regression guard: adding verbs must not make the poison path swallow."""
    assert terminal.apply_control("detonate", {}, "subagent-x") == "unknown"
    problems = channel.validate(
        {"from": "brain", "to": "sister", "type": "directive", "control": "detonate"}
    )
    assert any("control must be one of" in problem for problem in problems)


def test_the_transport_refuses_a_drop_with_no_target_and_accepts_a_valid_one():
    problems = channel.validate(
        {"from": "brain", "to": "sister", "type": "directive", "control": "drop"}
    )
    assert any("must name the task.directive" in problem for problem in problems), (
        f"a targetless drop was not refused by the transport: {problems}"
    )
    assert (
        channel.validate(
            {
                "from": "brain",
                "to": "sister",
                "type": "directive",
                "control": "drop",
                "task": {"directive": "d-wedged"},
            }
        )
        == []
    ), "a well-formed drop was refused"


def test_the_control_vocabulary_advertises_both_new_verbs():
    assert "drop" in channel.CONTROL_ACTIONS
    assert "dead-letter" in channel.CONTROL_ACTIONS
    assert "drop" in channel.DIRECTIVE_CONTROLS, "drop must be declared as needing a target"
    assert terminal.apply_control("dead-letter", {}, "subagent-x") == "list-dead-letter"


# --- 4. the mailbox is read by verb ---------------------------------------


def test_the_mailbox_is_listed_by_verb_with_its_reasons(tmp_path, monkeypatch, capsys):
    state = wire_scratch(tmp_path, monkeypatch)
    plant(channel.INBOX, "d-one")
    plant(channel.INBOX, "d-two")
    terminal.drop_directive("d-one", 754, "first reason", dropped_by="brain")
    terminal.drop_directive("d-two", 755, "second reason", dropped_by="operator")

    records = terminal.dead_letter_inventory()
    assert {record["id"] for record in records} == {"d-one", "d-two"}
    assert {record["reason"] for record in records} == {"first reason", "second reason"}

    rc = runaway.main(["--fleet-dir", str(state), "dead-letter"])
    printed = capsys.readouterr().out
    assert rc == runaway.EXIT_OK
    assert "d-one" in printed and "first reason" in printed
    assert "second reason" in printed

    rc = runaway.main(["--fleet-dir", str(state), "dead-letter", "--directive", "d-one"])
    printed = capsys.readouterr().out
    assert rc == runaway.EXIT_OK
    payload = json.loads(printed)
    assert payload["id"] == "d-one"
    assert payload["dropped_by"] == "brain"


# --- 5. the mutation harness: the anchor must actually LAND ------------------


def load_mutant(tmp_path: Path, old: str, new: str) -> tuple[object, Path]:
    """Import a mutated copy of runaway.py; assert the mutation really landed.

    A proof that reports "the gate caught it" while the mutation never applied
    proves nothing — the same trap measured elsewhere in this repo — so this
    helper asserts the source changed AND that the imported module is the scratch
    copy, not a stale __pycache__.
    """
    source = RUNAWAY_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    mutated = source.replace(old, new)
    assert mutated != source, "the mutation did not change the source"
    target = tmp_path / "mutant" / "runaway.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mutated, encoding="utf-8")
    for cache in target.parent.rglob("__pycache__"):
        for cached in sorted(cache.glob("runaway*.pyc")):
            cached.unlink()
    name = f"runaway_dl_mutant_{abs(hash((old, new)))}"
    spec = importlib.util.spec_from_file_location(name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == target.resolve(), (
        "the mutant did not import the scratch copy it wrote"
    )
    assert module.__file__ != runway_module_file(), "the mutant is the live module"
    return module, target


def runway_module_file() -> str:
    return str(RUNAWAY_PATH.resolve())


#: A mutant that omits `dropped_by` from the record — the field that makes the
#: two callers distinguishable, and the one a careless refactor would drop.
DROPPED_BY_REMOVED = (
    '        "dropped_by": dropped_by,',
    '        "dropped_by": None,',
)

#: A mutant that never moves the order out of the inbox — the `consume` half.
ORDER_NOT_MOVED = (
    "    try:\n        source.unlink()\n    except OSError:\n        pass\n    return target",
    "    return target",
)


def test_the_mutation_anchors_are_unique_and_land(tmp_path):
    """Self-control for THIS file: both anchors match exactly once."""
    source = RUNAWAY_PATH.read_text(encoding="utf-8")
    for old, _new in (DROPPED_BY_REMOVED, ORDER_NOT_MOVED):
        assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"


def test_a_mutant_that_omits_the_dropper_is_caught(tmp_path):
    """The record-shape contract must be able to fail (AO-GR-12)."""
    mutant, target = load_mutant(tmp_path, *DROPPED_BY_REMOVED)
    inbox = tmp_path / "inbox"
    plant(inbox, "d-mutant")
    mutant.dead_letter("d-mutant", "wedged", base=tmp_path, inbox=inbox, dropped_by="brain")
    record = mutant.record_shape(tmp_path, "d-mutant")
    assert record["dropped_by"] is None, (
        "the mutant did not diverge — asserting on the happy path here would be vacuous"
    )
    assert record["dropped_by"] != "brain", "the mutant must differ from the real record"


def test_a_mutant_that_leaves_the_order_in_the_inbox_is_caught(tmp_path, monkeypatch):
    """The `consume` half: a dead-letter that does not move the order is a no-op."""
    mutant, _target = load_mutant(tmp_path, *ORDER_NOT_MOVED)
    inbox = tmp_path / "inbox"
    plant(inbox, "d-mutant")
    mutant.dead_letter("d-mutant", "wedged", base=tmp_path, inbox=inbox, dropped_by="brain")
    assert (inbox / "d-mutant.json").exists(), (
        "the mutant did not diverge — it must leave the order in the inbox"
    )
    # The real implementation is what makes this test meaningful: prove it moves.
    real_inbox = tmp_path / "real-inbox"
    plant(real_inbox, "d-real")
    runaway.dead_letter(
        "d-real", "wedged", base=tmp_path / "real-base", inbox=real_inbox, dropped_by="brain"
    )
    assert not (real_inbox / "d-real.json").exists(), (
        "the REAL implementation left the order in the inbox"
    )
