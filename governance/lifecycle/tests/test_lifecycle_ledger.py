"""governance/lifecycle/ledger.py — the append-only decision trail (issue #885).

Pins: a refusal produces exactly one record; every record validates against
the frozen shape; the trail append is append-only (mirrors
governance/modules/audit.py's own guarantee); and directive.consume/retire each
write exactly one record per call, success or refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.lifecycle import ledger


def test_a_valid_record_round_trips():
    record = ledger.render(kind="decision", action="close", subject="#42", outcome="ok", detail="fine")
    assert record["schema"] == ledger.SCHEMA


def test_an_invalid_kind_is_refused_before_writing():
    with pytest.raises(ledger.LedgerUnavailable):
        ledger.render(kind="bogus", action="close", subject="#42", outcome="ok", detail="x")


def test_append_writes_one_line_and_proves_growth(tmp_path):
    path = tmp_path / "ledger.jsonl"
    record = ledger.render(kind="decision", action="close", subject="#42", outcome="ok", detail="fine")
    ledger.append(path, record)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["subject"] == "#42"


def test_append_refuses_a_trail_rewritten_underneath_it(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    record = ledger.render(kind="decision", action="close", subject="#1", outcome="ok", detail="a")
    ledger.append(path, record)

    # Simulate a second writer truncating the file between append()'s "before"
    # read and its write, by wrapping Path.read_bytes for this one call only.
    original = Path.read_bytes
    calls = {"n": 0}

    def flaky_read_bytes(self):
        data = original(self)
        calls["n"] += 1
        if calls["n"] == 1 and self == path:
            path.write_bytes(b"")  # the trail is rewritten out from under us
        return data

    monkeypatch.setattr(Path, "read_bytes", flaky_read_bytes)
    with pytest.raises(ledger.LedgerUnavailable):
        ledger.append(path, ledger.render(kind="decision", action="close", subject="#2", outcome="ok", detail="b"))


def test_record_decision_writes_exactly_one_record_for_a_refusal(tmp_path):
    ledger.record_decision(
        tmp_path, action="retire", subject="d-1", outcome=ledger.OUTCOME_REFUSED,
        detail="orders #1, which is not closed", code="DIRECTIVE_NOT_CONSUMED",
    )
    records = ledger.read_all(tmp_path / ledger.DEFAULT_LEDGER)
    assert len(records) == 1
    assert records[0]["kind"] == "refusal"
    assert records[0]["outcome"] == "refused"


def test_consume_writes_exactly_one_refusal_record(tmp_path):
    from governance.lifecycle import directive

    sent = tmp_path / ".fleet" / "sent"
    sent.mkdir(parents=True)
    (sent / "d-1.json").write_text(json.dumps({"id": "d-1", "task": {"issue": 99}}), encoding="utf-8")

    with pytest.raises(directive.DirectiveRefused):
        directive.consume(tmp_path, "d-1", landed={})  # issue outside the record: refused

    records = ledger.read_all(tmp_path / ledger.DEFAULT_LEDGER)
    assert len(records) == 1
    assert records[0]["action"] == "consume"
    assert records[0]["outcome"] == "refused"


def test_consume_writes_exactly_one_ok_record_on_success(tmp_path):
    from governance.lifecycle import directive

    sent = tmp_path / ".fleet" / "sent"
    sent.mkdir(parents=True)
    (sent / "d-1.json").write_text(json.dumps({"id": "d-1", "task": {"issue": 99}}), encoding="utf-8")

    directive.consume(tmp_path, "d-1", landed={99: True})

    records = ledger.read_all(tmp_path / ledger.DEFAULT_LEDGER)
    assert len(records) == 1
    assert records[0]["outcome"] == "ok"


def test_a_record_missing_a_required_field_is_refused_by_the_schema(tmp_path):
    # render() always fills every key, so drive the schema validator directly
    # to prove a malformed record is refused rather than silently accepted.
    bad = {"schema": ledger.SCHEMA, "kind": "decision", "action": "close"}  # missing subject/outcome/detail
    from governance.modules import schema as _schema

    schema_doc = ledger._record_schema("ledgerRecord")
    problems = _schema.problems(bad, schema_doc)
    assert problems, "a record missing required keys must not validate"
