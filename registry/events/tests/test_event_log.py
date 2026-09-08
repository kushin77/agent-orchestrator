"""Tests for the append-only agent lifecycle event log (issue #10).

Covers: monotonic sequence + hash-chain verify; append-only invariants; tamper
detection (edit / delete / reorder / truncate) both in memory and against a
file-backed JSON Lines log; closed event-kind vocabulary; and schema
conformance of every emitted record.
"""

from __future__ import annotations

import json
import os

import pytest

from events import (
    EVENT_KINDS,
    GENESIS_HASH,
    EventLog,
    EventLogError,
    EventLogIntegrityError,
    open_event_log,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "event.schema.json"
)


def _schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate(record):
    from jsonschema import validate

    validate(record, _schema())


def _data_line_absolute_index(path, data_index):
    """Map a 0-based data-line index to its absolute line index in the file."""
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    seen = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            seen += 1
            if seen == data_index:
                return i
    raise AssertionError("not enough data lines")


def _rewrite_data_line(path, data_index, new_text):
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    abs_index = _data_line_absolute_index(path, data_index)
    lines[abs_index] = new_text
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------- #
# in-memory chain
# --------------------------------------------------------------------- #
def test_append_is_monotonic_and_chained():
    log = EventLog()
    first = log.append(
        "register", status="registered", tenant_id="acme", agent_id="worker-1"
    )
    second = log.append(
        "activate", status="active", tenant_id="acme", agent_id="worker-1"
    )
    assert first["seq"] == 1
    assert second["seq"] == 2
    assert first["prevHash"] == GENESIS_HASH
    assert second["prevHash"] == first["hash"]
    assert len(log) == 2
    assert log.tail() == second
    assert log.verify() == (2, second["hash"])
    _validate(first)
    _validate(second)


def test_empty_log_state_is_genesis():
    log = EventLog()
    assert len(log) == 0
    assert log.tail() is None
    assert log.verify() == (0, GENESIS_HASH)


def test_events_snapshot_is_defensive_copy():
    log = EventLog()
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    snapshot = log.events()
    snapshot[0]["status"] = "forged"
    assert log.events()[0]["status"] == "registered"


def test_unknown_event_kind_is_rejected():
    log = EventLog()
    with pytest.raises(EventLogError):
        log.append("rename", tenant_id="acme", agent_id="a")
    assert len(log) == 0


def test_closed_event_kinds_contract():
    assert EVENT_KINDS == (
        "register",
        "activate",
        "pause",
        "retire",
        "route",
        "session",
    )


def test_edit_of_past_record_is_detected():
    log = EventLog()
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    tampered = log.events()
    tampered[0]["status"] = "forged"
    log._records = tampered  # simulate a writer that rewrote a past record
    with pytest.raises(EventLogIntegrityError, match="hash mismatch"):
        log.verify()


def test_delete_of_middle_record_is_detected():
    log = EventLog()
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    log.append("pause", status="paused", tenant_id="acme", agent_id="a")
    log._records = [log.events()[0], log.events()[2]]  # drop seq 2
    with pytest.raises(EventLogIntegrityError, match="seq gap or duplicate"):
        log.verify()


def test_reorder_of_records_is_detected():
    log = EventLog()
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    log._records = [log.events()[1], log.events()[0]]  # swap
    with pytest.raises(EventLogIntegrityError):
        log.verify()


def test_truncation_detected_with_expected_state():
    log = EventLog()
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    expected = log.state()  # (2, hash)
    log._records = [log.events()[0]]  # trailing record dropped
    with pytest.raises(EventLogIntegrityError, match="expected state"):
        log.verify(expected=expected)


# --------------------------------------------------------------------- #
# file-backed persistence
# --------------------------------------------------------------------- #
def test_file_backed_roundtrip(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = open_event_log(path)
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    tail_state = log.state()

    reopened = open_event_log(path)
    assert len(reopened) == 2
    assert reopened.state() == tail_state
    assert reopened.events() == log.events()
    reopened.verify(expected=tail_state)


def test_file_tamper_edit_is_detected_on_reopen(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = open_event_log(path)
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    lines = open(path, encoding="utf-8").read().splitlines()
    data_index = _data_line_absolute_index(path, 0)
    record = json.loads(lines[data_index])
    record["status"] = "forged"
    _rewrite_data_line(path, 0, json.dumps(record, separators=(",", ":")))
    with pytest.raises(EventLogIntegrityError, match="hash mismatch"):
        open_event_log(path)


def test_file_tamper_delete_middle_is_detected_on_reopen(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = open_event_log(path)
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    log.append("pause", status="paused", tenant_id="acme", agent_id="a")
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    del lines[_data_line_absolute_index(path, 1)]  # delete seq 2
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    with pytest.raises(EventLogIntegrityError):
        open_event_log(path)


def test_file_tamper_reorder_is_detected_on_reopen(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = open_event_log(path)
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    i0 = _data_line_absolute_index(path, 0)
    i1 = _data_line_absolute_index(path, 1)
    lines[i0], lines[i1] = lines[i1], lines[i0]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    with pytest.raises(EventLogIntegrityError):
        open_event_log(path)


def test_file_truncation_detected_with_expected_state(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = open_event_log(path)
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    expected = log.state()
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    del lines[-1]  # drop the trailing record
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    reopened = EventLog(path=path)  # loads fine: truncation needs expected state
    assert reopened.state() == (1, reopened.events()[0]["hash"])
    with pytest.raises(EventLogIntegrityError, match="expected state"):
        reopened.verify(expected=expected)


def test_malformed_line_is_hard_failure(tmp_path):
    path = str(tmp_path / "events.jsonl")
    log = open_event_log(path)
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
    with pytest.raises(EventLogIntegrityError, match="malformed event line"):
        open_event_log(path)


# --------------------------------------------------------------------- #
# schema conformance
# --------------------------------------------------------------------- #
def test_emitted_records_conform_to_schema():
    log = EventLog()
    log.append("register", status="registered", tenant_id="acme", agent_id="a")
    log.append("activate", status="active", tenant_id="acme", agent_id="a")
    log.append("pause", status="paused", tenant_id="acme", agent_id="a")
    log.append("retire", status="retired", tenant_id="acme", agent_id="a")
    log.append(
        "route",
        status="set",
        tenant_id="acme",
        detail={"taskType": "code-review", "agentIds": ["r1", "r2"]},
    )
    log.append("session", status="issued", tenant_id="acme", agent_id="a")
    for record in log.events():
        _validate(record)


def test_event_schema_file_is_valid_json():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    assert schema["title"] == "Agent lifecycle registry event (v1)"
    assert "hash" in schema["required"]
