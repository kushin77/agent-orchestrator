"""The steering channel enforces the brain/sister contract (issue #162)."""

from __future__ import annotations

import json

import channel
from channel import EXIT_NOT_OK, EXIT_OK, validate


def valid_directive(**overrides):
    message = {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "model": {"tier": "flash", "thinking": "none"},
        "task": {"issue": 162, "epic": 160, "lane": "fleet"},
        "body": "spawn one subagent for issue #162",
    }
    message.update(overrides)
    return message


def test_valid_directive_passes():
    assert validate(valid_directive()) == []


def test_unknown_message_type_is_refused():
    problems = validate(valid_directive(type="replan"))
    assert any("type must be one of" in problem for problem in problems)


def test_bad_model_tier_is_refused():
    problems = validate(valid_directive(model={"tier": "ultra", "thinking": "none"}))
    assert any("model.tier" in problem for problem in problems)


def test_bad_thinking_level_is_refused():
    problems = validate(valid_directive(model={"tier": "flash", "thinking": "max"}))
    assert any("model.thinking" in problem for problem in problems)


def test_missing_role_is_refused():
    message = valid_directive()
    del message["to"]
    problems = validate(message)
    assert any("to must match" in problem for problem in problems)


def test_a_sister_issued_directive_is_refused():
    problems = validate(valid_directive(**{"from": "sister"}))
    assert any("cannot issue directives" in problem for problem in problems)


def test_a_directive_not_addressed_to_the_sister_is_refused():
    problems = validate(valid_directive(to="subagent-x"))
    assert any("only be addressed to the sister" in problem for problem in problems)


def test_a_halting_message_needs_no_task():
    assert validate({"from": "brain", "to": "sister", "type": "halt"}) == []


def test_task_issue_must_be_a_positive_integer():
    problems = validate(valid_directive(task={"issue": -4}))
    assert any("task.issue" in problem for problem in problems)


def test_send_stamps_id_and_queues_for_the_sister(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(channel, "SENT", tmp_path / "sent")

    source = tmp_path / "directive.json"
    source.write_text(json.dumps(valid_directive()), encoding="utf-8")
    assert channel.cmd_send(type("Args", (), {"message": str(source)})) == EXIT_OK

    sent = sorted((tmp_path / "sent").glob("*.json"))
    inbox = sorted((tmp_path / "inbox").glob("*.json"))
    assert len(sent) == 1 and len(inbox) == 1
    queued = json.loads(inbox[0].read_text(encoding="utf-8"))
    assert queued["id"]
    assert queued["ts"]
    assert queued["model"] == {"tier": "flash", "thinking": "none"}


def test_send_refuses_an_invalid_message(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text(json.dumps(valid_directive(type="replan")), encoding="utf-8")
    assert channel.cmd_send(type("Args", (), {"message": str(source)})) == EXIT_NOT_OK


def test_standing_directive_on_disk_carries_dsv4fnone():
    directive_path = channel.ROOT / "fleet" / "directive.json"
    data = json.loads(directive_path.read_text(encoding="utf-8"))
    assert validate(data) == []
    assert data["from"] == "brain"
    assert data["to"] == "sister"
    assert data["model"] == {"tier": "flash", "thinking": "none", "budget_hint": data["model"]["budget_hint"]}
    assert data["model"]["tier"] == "flash"
    assert data["model"]["thinking"] == "none"
