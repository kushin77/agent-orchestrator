"""The operator → brain rung: orders go to the brain, and only the brain → sister.

Issue #160 named three rungs but the operator's only working trigger was to write
into the sister's inbox — i.e. to do the brain's job. These tests pin the
hierarchy the transport now enforces, and the brain loop that makes it real.
"""

from __future__ import annotations

import json
from pathlib import Path

import brain
import channel
from channel import validate


def order(**overrides):
    message = {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "correlation_id": "o-1",
        "task": {"issue": 5, "lane": "fleet"},
        "body": "dispatch one subagent for issue #5",
    }
    message.update(overrides)
    return message


# --- the hierarchy, enforced by the channel ---------------------------------


def test_an_operator_order_to_the_brain_is_valid():
    assert validate(order()) == []


def test_the_brain_takes_orders_only_from_the_operator():
    problems = validate(order(**{"from": "sister"}))
    assert any("only from the operator" in problem for problem in problems)


def test_an_order_to_the_brain_must_be_a_directive():
    problems = validate(order(type="ack"))
    assert any("must be a directive" in problem for problem in problems)


def test_the_operator_cannot_address_the_sister_directly():
    """The regression this rule exists for: bypassing the brain entirely."""
    problems = validate({"from": "operator", "to": "sister", "type": "result", "correlation_id": "x"})
    assert any("the operator does not address the sister" in problem for problem in problems)


def test_only_the_brain_may_issue_directives_to_the_sister():
    problems = validate(
        {"from": "subagent-x", "to": "sister", "type": "directive", "task": {"issue": 5}}
    )
    assert any("only the brain may issue directives to the sister" in problem for problem in problems)


def test_the_brain_reports_back_to_the_operator():
    assert validate({"from": "brain", "to": "operator", "type": "ack", "correlation_id": "o-1"}) == []


def test_the_brain_does_not_ack_the_sister():
    problems = validate({"from": "brain", "to": "sister", "type": "result", "correlation_id": "d-1"})
    assert any("does not ack or report on its own directives" in problem for problem in problems)


def test_a_brain_escalation_goes_to_the_operator():
    assert validate(
        {"from": "brain", "to": "operator", "type": "escalate", "severity": "warn", "correlation_id": "o-1"}
    ) == []
    problems = validate(
        {"from": "brain", "to": "sister", "type": "escalate", "severity": "warn", "correlation_id": "o-1"}
    )
    assert any("addressed to the operator" in problem for problem in problems)


def test_the_sister_still_escalates_to_the_brain():
    assert validate(
        {"from": "sister", "to": "brain", "type": "escalate", "severity": "critical", "correlation_id": "d-1"}
    ) == []


# --- the operator trigger ----------------------------------------------------


def test_a_non_work_order_needs_no_issue_and_a_work_order_still_does():
    """The live bug: `task.kind: status` was refused for lacking an issue."""
    assert validate({"from": "operator", "to": "brain", "type": "directive", "task": {"kind": "status"}}) == []
    problems = validate({"from": "operator", "to": "brain", "type": "directive", "task": {}})
    assert any("task.issue must be a positive integer" in problem for problem in problems)
    problems = validate(
        {"from": "operator", "to": "brain", "type": "directive", "task": {"kind": "whenever"}}
    )
    assert any("task.kind must be one of" in problem for problem in problems)


def test_a_decompose_order_needs_no_issue_of_its_own():
    """A decompose order carries task.decompose, not task.issue — the validator must
    let it through (the paper-feature class: schema extended, validator not)."""
    order = {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "task": {
            "decompose": {
                "parent_issue": 219,
                "children": [{"title": "a", "verify": "pytest a", "depends_on": []}],
            }
        },
    }
    assert validate(order) == []


def test_order_queues_for_the_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "BRAIN_INBOX", tmp_path / "brain" / "inbox")
    monkeypatch.setattr(channel, "BRAIN_SENT", tmp_path / "brain" / "sent")
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    result = channel.cmd_order(_Args(message=json.dumps(order())))
    assert result == channel.EXIT_OK
    assert len(list(channel.BRAIN_INBOX.glob("*.json"))) == 1


def test_order_refuses_a_message_the_operator_may_not_send(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "BRAIN_INBOX", tmp_path / "brain" / "inbox")
    monkeypatch.setattr(channel, "BRAIN_SENT", tmp_path / "brain" / "sent")
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    bypass = json.dumps({"from": "operator", "to": "sister", "type": "directive", "task": {"issue": 5}})
    assert channel.cmd_order(_Args(message=bypass)) == channel.EXIT_NOT_OK


class _Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# --- the brain rung ----------------------------------------------------------


def test_build_directive_signs_it_as_the_brain():
    directive = brain.build_directive(order())
    assert directive["from"] == "brain" and directive["to"] == "sister"
    assert directive["correlation_id"] == "o-1"
    assert validate(directive) == []


def test_the_brain_raises_the_floor_for_security_lanes():
    """Security/auth/IaC never dispatch at flash/none (fleet doctrine)."""
    directive = brain.build_directive(order(task={"issue": 9, "lane": "secrets-handling"}))
    assert directive["model"] == {"tier": "pro", "thinking": "low"}


def test_the_brain_does_not_lower_a_tier_the_operator_raised():
    directive = brain.build_directive(order(model={"tier": "pro", "thinking": "high"}))
    assert directive["model"] == {"tier": "pro", "thinking": "high"}


def test_handle_order_refuses_one_that_names_no_issue():
    ok, report = brain.handle_order(order(task={"lane": "fleet"}))
    assert ok is False
    assert "names no issue" in report


def test_handle_order_dispatches_through_the_channel(monkeypatch):
    seen = {}

    def fake_dispatch(order_):
        seen["directive"] = brain.build_directive(order_)
        return True, "channel send: OK — abc queued for the sister"

    monkeypatch.setattr(brain, "dispatch", fake_dispatch)
    ok, report = brain.handle_order(order())
    assert ok is True
    assert seen["directive"]["to"] == "sister"
    assert "#5" in report


def test_handle_order_reports_a_refused_dispatch_verbatim(monkeypatch):
    monkeypatch.setattr(brain, "dispatch", lambda order_: (False, "REFUSED — no-chain-edge: #5 is not next"))
    ok, report = brain.handle_order(order())
    assert ok is False
    assert "no-chain-edge" in report


def test_non_work_orders_do_not_dispatch(monkeypatch):
    called = {"n": 0}

    def fake_dispatch(order_):
        called["n"] += 1
        return True, "x"

    monkeypatch.setattr(brain, "dispatch", fake_dispatch)
    monkeypatch.setattr(brain, "_health", lambda: (0, ["healthy"]))
    ok, report = brain.handle_order(order(task={"kind": "status"}))
    assert ok is True
    assert called["n"] == 0
    assert "no dispatch" in report


def test_brain_outbox_reads_newest_last(tmp_path, monkeypatch, capsys):
    """Ids are uuid4, so a filename sort shows the operator a stale reply."""
    monkeypatch.setattr(channel, "BRAIN_OUTBOX", tmp_path / "brain" / "outbox")
    channel.BRAIN_OUTBOX.mkdir(parents=True, exist_ok=True)
    for name, stamp, body in (
        ("zzz-first-id", "2026-09-13T19:00:00Z", "the older reply"),
        ("aaa-second-id", "2026-09-13T20:00:00Z", "the newer reply"),
    ):
        (channel.BRAIN_OUTBOX / f"{name}.json").write_text(
            json.dumps(
                {
                    "from": "brain",
                    "to": "operator",
                    "type": "ack",
                    "id": name,
                    "ts": stamp,
                    "correlation_id": "o-1",
                    "body": body,
                }
            ),
            encoding="utf-8",
        )
    assert channel.cmd_brain_outbox(type("Args", (), {"limit": 0})()) == channel.EXIT_OK
    out = capsys.readouterr().out
    assert out.index("the older reply") < out.index("the newer reply")


def test_brain_reply_lands_in_the_operator_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(channel, "BRAIN_OUTBOX", tmp_path / "brain" / "outbox")
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    channel.brain_reply(order(), "ack", "dispatched #5 at flash/none")
    files = list(channel.BRAIN_OUTBOX.glob("*.json"))
    assert len(files) == 1
    reply = json.loads(files[0].read_text(encoding="utf-8"))
    assert reply["to"] == "operator" and reply["correlation_id"] == "o-1"
    assert validate(reply) == []


def test_the_brain_writes_a_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "HEARTBEAT", tmp_path / "brain.heartbeat.json")
    brain.write_heartbeat("idle", started_at="2026-09-13T00:00:00Z", commit="abc1234")
    entry = json.loads(Path(brain.HEARTBEAT).read_text(encoding="utf-8"))
    assert entry["state"] == "idle" and entry["commit"] == "abc1234" and entry["pid"] > 0
