"""E2E pilot: one brain command flows operator -> brain -> sister -> subagent
-> PR -> merge (issue #167).

This proves the whole chain with the REAL transport code (`channel.py`,
`brain.py`) rather than a mock of the contract — every hop below calls the
same functions `fleet/brain.py` and the sister loop call in production, just
against a temp mailbox (monkeypatched module-level paths, the same isolation
`test_channel.py` already uses). Alongside the golden path this module carries
the two negative controls issue #167 asks for: an operator -> sister bypass is
refused, and a sister-issued directive is refused — both by the real
`channel.validate`/`cmd_send`, not by a re-implementation of the rule.
"""

from __future__ import annotations

import json

import brain
import channel
from channel import EXIT_NOT_OK, EXIT_OK


def _mailbox(monkeypatch, tmp_path):
    for name in ("INBOX", "SENT", "OUTBOX", "DONE", "BRAIN_INBOX", "BRAIN_SENT", "BRAIN_OUTBOX", "BRAIN_DONE"):
        monkeypatch.setattr(channel, name, tmp_path / name.lower())
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")


def _args(**fields):
    return type("Args", (), fields)


def test_one_brain_command_flows_operator_to_pr_merge(tmp_path, monkeypatch):
    _mailbox(monkeypatch, tmp_path)

    # 1. operator orders the brain (the only way in).
    order = {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "task": {"issue": 167, "epic": 160, "lane": "fleet-e2e"},
        "body": "dispatch one subagent for issue #167",
    }
    assert channel.cmd_order(_args(message=json.dumps(order))) == EXIT_OK
    queued = list((tmp_path / "brain_inbox").glob("*.json"))
    assert len(queued) == 1
    landed_order = json.loads(queued[0].read_text(encoding="utf-8"))

    # 2. the brain drains its inbox and composes the sister-bound directive —
    # the real `fleet/brain.py` code path, not a re-derivation of its rules.
    directive = brain.build_directive(landed_order)
    assert channel.validate(directive) == []
    assert channel.cmd_send(_args(message=json.dumps(directive))) == EXIT_OK
    sister_inbox = list((tmp_path / "inbox").glob("*.json"))
    assert len(sister_inbox) == 1
    landed_directive = json.loads(sister_inbox[0].read_text(encoding="utf-8"))
    # The emitted dialect is the RECIPIENT's (issue #777), and here the dispatcher
    # has declared nothing: its beat is the redirected, non-existent tmp path, so
    # the emitter may not assume schema 2 and writes the spelling a build from
    # before this migration can read. That is the whole reason the running fleet
    # survives the rename — asserted, not assumed.
    assert channel.declared_envelope_schema("dispatcher") is None
    assert landed_directive["schema"] == channel.SCHEMA_VERSION_LEGACY
    assert landed_directive["from"] == "brain"
    assert landed_directive["to"] == "sister"
    assert landed_directive["task"]["issue"] == 167

    # 3. the sister drains it (watch returns the oldest pending directive) and
    # spawns the epic-focused subagent named in the directive.
    assert channel.cmd_watch(_args(timeout_seconds=0, interval=0.01)) == EXIT_OK

    # 4. the subagent does the work, opens the PR, merges it, and reports —
    # the same `report` verb the contract defines for "answers the directive".
    evidence = "PR #501 opened (Closes #167), make verify PASS, merged to master"
    assert (
        channel.cmd_report(
            _args(from_role="sister", correlation=landed_directive["id"], type="result", body=evidence)
        )
        == EXIT_OK
    )

    # the directive is consumed (moved inbox -> done) and the brain's `wait`
    # sees the merge evidence land in the outbox — the completion trigger.
    assert not (tmp_path / "inbox" / f"{landed_directive['id']}.json").exists()
    assert (tmp_path / "done" / f"{landed_directive['id']}.json").exists()
    outbox = list((tmp_path / "outbox").glob("*.json"))
    assert len(outbox) == 1
    result = json.loads(outbox[0].read_text(encoding="utf-8"))
    assert result["correlation_id"] == landed_directive["id"]
    assert "merged" in result["body"]
    assert "#167" in result["body"]


def test_principal_to_dispatcher_bypass_is_refused(tmp_path, monkeypatch):
    """The principal orders the director; it may never address the dispatcher directly."""
    _mailbox(monkeypatch, tmp_path)
    bypass = {
        "from": "operator",
        "to": "sister",
        "type": "directive",
        "task": {"issue": 167, "lane": "fleet-e2e"},
        "body": "spawn one subagent for issue #167",
    }
    problems = channel.validate(bypass)
    assert any("principal does not address the dispatcher" in problem for problem in problems)
    assert channel.cmd_send(_args(message=json.dumps(bypass))) == EXIT_NOT_OK
    assert list((tmp_path / "inbox").glob("*.json")) == []


def test_dispatcher_issued_directive_is_refused(tmp_path, monkeypatch):
    """The dispatcher never picks work: it executes directives, it never issues them."""
    _mailbox(monkeypatch, tmp_path)
    rogue = {
        "from": "sister",
        "to": "subagent-x",
        "type": "directive",
        "task": {"issue": 167, "lane": "fleet-e2e"},
        "body": "dispatch yourself",
    }
    problems = channel.validate(rogue)
    assert any("dispatcher" in problem and "cannot issue directives" in problem for problem in problems)
    assert channel.cmd_send(_args(message=json.dumps(rogue))) == EXIT_NOT_OK
    assert list((tmp_path / "inbox").glob("*.json")) == []
