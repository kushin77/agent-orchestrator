"""The steering channel enforces the brain/sister contract (issue #162)."""

from __future__ import annotations

import json
import threading

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


def test_ack_requires_a_correlation_id():
    problems = validate({"from": "sister", "to": "brain", "type": "result"})
    assert any("correlation_id" in problem for problem in problems)


def test_the_brain_cannot_ack_its_own_directives():
    problems = validate(
        {"from": "brain", "to": "sister", "type": "ack", "correlation_id": "directive-0001"}
    )
    assert any("does not ack" in problem for problem in problems)


def test_halt_may_only_come_from_the_brain():
    assert validate({"from": "brain", "to": "sister", "type": "halt"}) == []
    problems = validate({"from": "sister", "to": "brain", "type": "halt"})
    assert any("only the brain" in problem for problem in problems)


def test_wait_triggers_when_the_result_lands(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "OUTBOX", tmp_path / "outbox")
    result = {"from": "sister", "to": "brain", "type": "result", "correlation_id": "d-1", "id": "r-1"}

    def later():
        import time as clock

        clock.sleep(0.1)
        channel.OUTBOX.mkdir(parents=True, exist_ok=True)
        (channel.OUTBOX / "r-1.json").write_text(json.dumps(result), encoding="utf-8")

    thread = threading.Thread(target=later)
    thread.start()
    args = type("Args", (), {"id": "d-1", "timeout_seconds": 5.0, "interval": 0.02})()
    assert channel.cmd_wait(args) == EXIT_OK
    thread.join()


def test_wait_times_out_with_a_failure_not_a_silent_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "OUTBOX", tmp_path / "outbox")
    args = type("Args", (), {"id": "nope", "timeout_seconds": 0.05, "interval": 0.01})()
    assert channel.cmd_wait(args) == EXIT_NOT_OK


def test_wait_matches_by_correlation_id(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "OUTBOX", tmp_path / "outbox")
    channel.OUTBOX.mkdir(parents=True, exist_ok=True)
    result = {"from": "subagent-x", "to": "brain", "type": "result", "correlation_id": "d-1", "id": "r-9"}
    (channel.OUTBOX / "r-9.json").write_text(json.dumps(result), encoding="utf-8")
    args = type("Args", (), {"id": "d-1", "timeout_seconds": 1.0, "interval": 0.01})()
    assert channel.cmd_wait(args) == EXIT_OK


def test_report_writes_a_valid_outbox_message(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "OUTBOX", tmp_path / "outbox")
    args = type(
        "Args",
        (),
        {"from_role": "sister", "type": "result", "correlation": "d-1", "body": "merged #171"},
    )()
    assert channel.cmd_report(args) == EXIT_OK
    files = list((tmp_path / "outbox").glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert validate(data) == []
    assert data["correlation_id"] == "d-1"


def test_watch_returns_the_oldest_pending_directive(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    channel.INBOX.mkdir(parents=True, exist_ok=True)
    directive = {"from": "brain", "to": "sister", "type": "directive", "id": "d-1"}
    (channel.INBOX / "d-1.json").write_text(json.dumps(directive), encoding="utf-8")
    args = type("Args", (), {"timeout_seconds": 1.0, "interval": 0.01})()
    assert channel.cmd_watch(args) == EXIT_OK


def test_watch_reports_idle_rather_than_blocking_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    args = type("Args", (), {"timeout_seconds": 0.05, "interval": 0.01})()
    assert channel.cmd_watch(args) == EXIT_NOT_OK


def test_report_consumes_the_directive_it_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(channel, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(channel, "DONE", tmp_path / "done")
    channel.INBOX.mkdir(parents=True, exist_ok=True)
    directive = {"from": "brain", "to": "sister", "type": "directive", "id": "d-1"}
    (channel.INBOX / "d-1.json").write_text(json.dumps(directive), encoding="utf-8")

    args = type(
        "Args",
        (),
        {"from_role": "sister", "type": "result", "correlation": "d-1", "body": "done"},
    )()
    assert channel.cmd_report(args) == EXIT_OK

    assert not (channel.INBOX / "d-1.json").exists()
    assert (tmp_path / "done" / "d-1.json").exists()


def test_consuming_an_absent_directive_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(channel, "DONE", tmp_path / "done")
    assert channel.consume_directive("never-queued") is False
