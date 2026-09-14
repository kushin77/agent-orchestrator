"""The append-only audit trail: one refusal, one record — and never a rewrite.

The registry's refusals are its compliance signal, so the trail that keeps them
has to be as honest as the registry: a refusal appends exactly one record, every
judged name is recorded once, a second append preserves the first byte for byte,
and a trail of another shape is refused rather than mixed into.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from conftest import BASE_ROWS

from governance.modules import audit, policy, registry, schema
from governance.modules.cli import EXIT_OK, main


def record_schema():
    """The frozen audit-record shape, as a standalone schema for one record."""
    loaded = schema.load()
    return {"$schema": schema.DIALECT, "$defs": loaded["$defs"], "$ref": "#/$defs/auditRecord"}


def judged(doc):
    return audit.records(doc, policy.load())


# --------------------------------------------------------------------------- #
# what the trail records
# --------------------------------------------------------------------------- #
def test_one_record_per_refusal_and_per_judged_name(built) -> None:
    records = judged(built)
    counted = audit.summary(records)
    assert counted[audit.KIND_REFUSAL] == len(built["refusals"]) == 0
    assert counted[audit.KIND_JUDGMENT] == len(built["modules"]) + len(built["not_modules"])
    assert len(records) == len(built["modules"]) + len(built["not_modules"])
    declared = policy.load()
    for record in records:
        assert record["schema"] == audit.SCHEMA
        assert record["disposition"] == declared.judged_disposition(record["state"])
        assert record["condition"] is None and record["code"] is None


def test_every_judged_name_is_recorded_exactly_once(built) -> None:
    subjects = [record["subject"] for record in judged(built)]
    expected = [entry["id"] for entry in built["modules"]] + [
        entry["id"] for entry in built["not_modules"]
    ]
    assert subjects == expected
    assert len(set(subjects)) == len(subjects)


def test_every_record_carries_the_frozen_shape(built) -> None:
    for record in judged(built):
        assert schema.problems(record, record_schema()) == ()
        assert sorted(record) == sorted(audit.RECORD_KEYS)


def test_one_refusal_appends_exactly_one_refusal_record(
    consumer, targets, make_hub, tmp_path
) -> None:
    rows = list(BASE_ROWS) + [["ghost-module", "ghost-module", "ghost.json", "provoked"]]
    doc = registry.build(consumer, make_hub("hub-ghost", rows=rows), targets)
    assert len(doc["refusals"]) == 1
    records = judged(doc)
    assert audit.summary(records) == {audit.KIND_REFUSAL: 1, audit.KIND_JUDGMENT: len(records) - 1}

    trail = tmp_path / "trail.jsonl"
    written = audit.append(trail, records)
    assert written == len(records)
    assert len(audit.read(trail)) == written

    refusal = [record for record in audit.read(trail) if record["kind"] == audit.KIND_REFUSAL]
    assert len(refusal) == 1
    assert refusal[0]["code"] == "MODULE-UNREGISTERED-MANDATORY"
    assert refusal[0]["subject"] == "ghost-module"
    assert refusal[0]["condition"] == "hub-mandatory-drift"
    assert refusal[0]["disposition"] == policy.DISPOSITION_FATAL


def test_an_unjudged_refusal_is_never_recorded(built) -> None:
    doc = copy.deepcopy(built)
    doc["refusals"] = [
        {
            "code": "MODULE-DUPLICATE-ID",
            "subject": "probe",
            "detail": "probe",
            "source": "probe",
            "disposition": "",
            "condition": "",
        }
    ]
    with pytest.raises(audit.AuditUnavailable) as excinfo:
        judged(doc)
    assert "MODULE-DUPLICATE-ID" in str(excinfo.value)


def test_a_judgment_the_policy_does_not_declare_is_never_recorded(built) -> None:
    doc = copy.deepcopy(built)
    doc["modules"][0]["state"] = "something-else"
    with pytest.raises(audit.AuditUnavailable) as excinfo:
        judged(doc)
    assert "something-else" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# append-only
# --------------------------------------------------------------------------- #
def test_the_trail_is_append_only(built, tmp_path) -> None:
    trail = tmp_path / "trail.jsonl"
    records = judged(built)

    assert audit.append(trail, records) == len(records)
    first = trail.read_bytes()
    assert len(audit.read(trail)) == len(records)

    assert audit.append(trail, records) == len(records)
    second = trail.read_bytes()

    assert second.startswith(first)
    assert len(second) == 2 * len(first)
    stored = audit.read(trail)
    assert len(stored) == 2 * len(records)
    assert stored[: len(records)] == stored[len(records):] == records


def test_a_trail_of_another_shape_is_refused_and_left_untouched(built, tmp_path) -> None:
    trail = tmp_path / "foreign.jsonl"
    foreign = json.dumps({"schema": "someone.else/trail-v9"}) + "\n"
    trail.write_text(foreign, encoding="utf-8")

    with pytest.raises(audit.AuditUnavailable) as excinfo:
        audit.append(trail, judged(built))
    assert "someone.else/trail-v9" in str(excinfo.value)
    assert trail.read_text(encoding="utf-8") == foreign


def test_a_malformed_line_is_refused_not_skipped(tmp_path) -> None:
    trail = tmp_path / "trail.jsonl"
    trail.write_text('{"schema": "ao.module-registry/audit-v1"}\n', encoding="utf-8")
    with pytest.raises(audit.AuditUnavailable) as excinfo:
        audit.read(trail)
    assert "missing" in str(excinfo.value)


def test_an_absent_trail_reads_as_empty(tmp_path) -> None:
    assert audit.read(tmp_path / "never-written.jsonl") == []


def test_the_records_are_deterministic_over_one_revision(hub, consumer, targets) -> None:
    first = registry.build(consumer, hub, targets)
    second = registry.build(consumer, hub, targets)
    assert audit.render(judged(first)) == audit.render(judged(second))


# --------------------------------------------------------------------------- #
# the CLI path the gate drives
# --------------------------------------------------------------------------- #
def test_the_cli_appends_the_judged_records(consumer, targets, make_hub, tmp_path, capsys) -> None:
    args = [
        "verify",
        "--repo",
        str(consumer),
        "--hub",
        str(make_hub("hub-cli")),
        "--targets",
        str(targets),
    ]
    trail = tmp_path / "cli-trail.jsonl"

    assert main([*args, "--audit", str(trail)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "audit trail" in out
    first = len(audit.read(trail))
    assert first > 0

    assert main([*args, "--audit", str(trail)]) == EXIT_OK
    capsys.readouterr()
    assert len(audit.read(trail)) == 2 * first


def test_the_build_command_does_not_write_a_trail_unless_asked(
    consumer, targets, make_hub, tmp_path, capsys
) -> None:
    """The registry reads the tree and writes nothing into it (NG4)."""
    assert (
        main(
            [
                "build",
                "--repo",
                str(consumer),
                "--hub",
                str(make_hub("hub-build")),
                "--targets",
                str(targets),
            ]
        )
        == EXIT_OK
    )
    capsys.readouterr()
    assert sorted(entry.name for entry in Path(consumer).iterdir()) == [".gitmodules", "module.json"]
