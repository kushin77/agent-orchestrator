"""Schema-level tests for governance/lessons/model.py (issue #141).

Every finding code the model can raise is driven deliberately here, so the
vocabulary is exercised rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import action, incident, lesson, rca, suggestion
from model import (
    CODE_DUPLICATE_ID,
    CODE_INVALID_FIELD,
    CODE_LEDGER_INVALID,
    CODE_RECORD_INCOMPLETE,
    CODE_UNKNOWN_KIND,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    Entry,
    errors,
    warnings,
)


def parse(records, path="governance/lessons/ledger.jsonl"):
    from checker import parse_ledger_text

    text = "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n"
    return parse_ledger_text(text, Path(path))


def codes(records):
    return [finding.code for finding in parse(records).findings]


def test_a_complete_set_of_records_is_clean():
    records = [incident(1), rca(1), action(1), lesson(1), suggestion(1)]
    assert codes(records) == []


@pytest.mark.parametrize(
    "record",
    [incident(1), rca(1), action(1), lesson(1), suggestion(1)],
)
def test_each_record_kind_round_trips(record):
    parsed = parse([record])
    assert parsed.records[record["id"]] == record


def test_unknown_kind_is_reported():
    record = {"id": "WIDGET-1", "kind": "widget"}
    assert codes([record]) == [CODE_UNKNOWN_KIND]


@pytest.mark.parametrize(
    "field",
    ["id", "date", "summary", "severity", "class", "origin", "status"],
)
def test_a_missing_incident_field_is_reported(field):
    record = incident(1)
    del record[field]
    assert CODE_RECORD_INCOMPLETE in codes([record])


@pytest.mark.parametrize(
    "field",
    ["incident", "artifact", "corrective_actions", "reviewed_at"],
)
def test_a_missing_rca_field_is_reported(field):
    record = rca(1)
    del record[field]
    assert CODE_RECORD_INCOMPLETE in codes([record])


def test_an_empty_required_field_counts_as_missing():
    assert CODE_RECORD_INCOMPLETE in codes([incident(1, summary="   ")])


@pytest.mark.parametrize(
    "record",
    [
        incident(1, id="INC1"),
        rca(1, id="RC-0001"),
        action(1, id="ACT-1"),
        lesson(1, id="LESSON1"),
        suggestion(1, id="IDEA-1"),
    ],
)
def test_a_wrong_id_prefix_is_reported(record):
    assert CODE_INVALID_FIELD in codes([record])


@pytest.mark.parametrize("value", ["13-09-2026", "", "the other day", 42])
def test_a_bad_date_is_reported(value):
    assert CODE_INVALID_FIELD in codes([incident(1, date=value)])


def test_a_bad_status_is_reported():
    assert CODE_INVALID_FIELD in codes([incident(1, status="finished")])


def test_a_bad_severity_is_reported():
    assert CODE_INVALID_FIELD in codes([incident(1, severity="urgent")])


def test_a_bad_incident_class_is_reported():
    assert CODE_INVALID_FIELD in codes([incident(1, **{"class": "made-up"})])


def test_an_origin_that_is_not_an_object_is_reported():
    assert CODE_INVALID_FIELD in codes([incident(1, origin="issue #100")])


def test_an_unknown_origin_kind_is_reported():
    assert CODE_INVALID_FIELD in codes(
        [incident(1, origin={"kind": "rumour", "ref": "#1"})]
    )


def test_a_bad_reviewed_at_is_reported():
    assert CODE_INVALID_FIELD in codes([rca(1, reviewed_at="2026-9-1")])


def test_a_non_list_corrective_actions_field_is_reported():
    assert CODE_INVALID_FIELD in codes([rca(1, corrective_actions="CA-0001")])


def test_a_non_list_evidence_field_is_reported():
    assert CODE_INVALID_FIELD in codes([lesson(1, evidence="abc1234")])


def test_an_evidence_entry_that_is_not_an_object_is_reported():
    assert CODE_INVALID_FIELD in codes([lesson(1, evidence=["abc1234"])])


def test_an_unknown_evidence_kind_is_reported():
    assert CODE_INVALID_FIELD in codes(
        [lesson(1, evidence=[{"kind": "hearsay", "ref": "x"}])]
    )


def test_evidence_without_a_ref_is_reported():
    assert CODE_INVALID_FIELD in codes(
        [lesson(1, evidence=[{"kind": "commit", "ref": "  "}])]
    )


def test_a_lesson_class_outside_the_ladder_is_reported():
    assert CODE_INVALID_FIELD in codes([lesson(1, **{"class": "legendary"})])


def test_a_duplicate_id_is_reported_once():
    assert codes([incident(1), incident(1)]) == [CODE_DUPLICATE_ID]


def test_a_malformed_line_is_a_hard_finding():
    from checker import parse_ledger_text

    ledger = parse_ledger_text('{"id": "INC-0001",\n', Path("ledger.jsonl"))
    assert [f.code for f in ledger.findings] == [CODE_LEDGER_INVALID]
    assert ledger.entries == []


def test_a_line_that_is_not_an_object_is_a_hard_finding():
    from checker import parse_ledger_text

    ledger = parse_ledger_text('["not", "a", "record"]\n', Path("ledger.jsonl"))
    assert [f.code for f in ledger.findings] == [CODE_LEDGER_INVALID]


def test_blank_lines_are_tolerated():
    from checker import parse_ledger_text

    ledger = parse_ledger_text("\n\n", Path("ledger.jsonl"))
    assert ledger.entries == []
    assert ledger.findings == []


def test_finding_severity_defaults_to_error_and_splits_cleanly():
    finding = next(f for f in parse([incident(1, summary="")]).findings)
    assert finding.is_error is True
    assert errors([finding]) == [finding]
    assert warnings([finding]) == []


def test_finding_renders_its_remediation():
    finding = next(f for f in parse([incident(1, status="finished")]).findings)
    rendered = finding.render()
    assert finding.message in rendered
    assert finding.remediation in rendered
    assert finding.as_dict()["severity"] == SEVERITY_ERROR


def test_a_warning_is_not_an_error():
    from model import Finding

    soft = Finding(
        code="suggestion-open",
        message="SUGGEST-0001 is open",
        severity=SEVERITY_WARNING,
    )
    assert soft.is_error is False
    assert warnings([soft]) == [soft]
    assert errors([soft]) == []


def test_report_serializes_errors_and_deviations_separately():
    from model import Report

    report = Report(ledger="ledger.jsonl", generated_at="2026-09-15T00:00:00Z")
    payload = report.as_dict()
    assert payload["errors"] == []
    assert payload["deviations"] == []
    assert payload["schema"].startswith("cmr.lessons/")


def test_entry_without_a_record_is_inert():
    entry = Entry(line=7, raw="")
    assert entry.id == ""
    assert entry.kind == ""
