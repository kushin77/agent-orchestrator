"""The out-of-epic pool rail (epic #707, lane F6 / issue #721).

The pool is where an `out-of-epic-pooled` refusal parks deferred work. Every
test here asserts a *property of the parking*, not just a return value: what was
recorded, what a drain reports, and what happens to a corrupt rail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pool
import pytest


def test_note_appends_one_record_per_park(tmp_path):
    rail = tmp_path / "pool.jsonl"
    pool.note(910, pool.REASON_OUT_OF_EPIC, at="2026-09-14T00:00:00Z", path=rail)
    pool.note(911, pool.REASON_OUT_OF_EPIC, at="2026-09-14T00:00:01Z", path=rail)

    records = pool.read(rail)
    assert [(r.issue, r.reason, r.at) for r in records] == [
        (910, "out-of-epic", "2026-09-14T00:00:00Z"),
        (911, "out-of-epic", "2026-09-14T00:00:01Z"),
    ]


def test_note_stamps_a_timestamp_when_none_is_given(tmp_path):
    rail = tmp_path / "pool.jsonl"
    record = pool.note(910, pool.REASON_OUT_OF_EPIC, path=rail)
    assert record.at.endswith("Z") and len(record.at) == len("2026-09-14T00:00:00Z")


def test_a_missing_rail_reads_as_empty_not_as_an_error(tmp_path):
    """A fresh checkout has no pool, which is different from a pool that failed."""
    assert pool.read(tmp_path / "pool.jsonl") == []
    assert pool.numbers(tmp_path / "pool.jsonl") == []
    assert pool.drain(tmp_path / "pool.jsonl") == []


def test_numbers_deduplicates_and_keeps_first_park_order(tmp_path):
    rail = tmp_path / "pool.jsonl"
    for issue in (911, 910, 911):
        pool.note(issue, pool.REASON_OUT_OF_EPIC, path=rail)
    assert pool.numbers(rail) == [911, 910]


def test_a_malformed_line_is_reported_with_its_line_number(tmp_path):
    """A reader that skips what it cannot parse makes a corrupt pool look empty."""
    rail = tmp_path / "pool.jsonl"
    rail.write_text(
        json.dumps({"issue": 910, "reason": "out-of-epic", "at": "2026-09-14T00:00:00Z"}) + "\nnot json\n",
        encoding="utf-8",
    )
    with pytest.raises(pool.PoolInvalid) as excinfo:
        pool.read(rail)
    assert ":2" in str(excinfo.value)
    assert "invalid JSON" in str(excinfo.value)


@pytest.mark.parametrize(
    "mutant,needle",
    [
        ({"issue": "910", "reason": "out-of-epic", "at": "t"}, "issue"),
        ({"issue": 910, "at": "t"}, "reason"),
        ({"issue": 910, "reason": "  ", "at": "t"}, "reason"),
        ({"issue": True, "reason": "out-of-epic", "at": "t"}, "issue"),
        ({"issue": 0, "reason": "out-of-epic", "at": "t"}, "issue"),
        ({"issue": 910, "reason": "out-of-epic"}, "at"),
    ],
)
def test_a_schema_invalid_record_is_refused(tmp_path, mutant, needle):
    rail = tmp_path / "pool.jsonl"
    rail.write_text(json.dumps(mutant) + "\n", encoding="utf-8")
    with pytest.raises(pool.PoolInvalid) as excinfo:
        pool.read(rail)
    assert needle in str(excinfo.value)


def test_drain_returns_and_clears(tmp_path):
    """A drain REPORTS what it took; a drain that returned nothing would be a drop."""
    rail = tmp_path / "pool.jsonl"
    pool.note(910, pool.REASON_OUT_OF_EPIC, path=rail)
    pool.note(911, pool.REASON_OUT_OF_EPIC, path=rail)

    assert pool.drain(rail) == [910, 911]
    assert pool.numbers(rail) == []
    assert pool.read(rail) == []


def test_drain_is_idempotent(tmp_path):
    rail = tmp_path / "pool.jsonl"
    pool.note(910, pool.REASON_OUT_OF_EPIC, path=rail)
    assert pool.drain(rail) == [910]
    assert pool.drain(rail) == []


def test_drain_and_report_names_every_drained_issue(tmp_path):
    rail = tmp_path / "pool.jsonl"
    pool.note(910, pool.REASON_OUT_OF_EPIC, path=rail)
    pool.note(911, pool.REASON_OUT_OF_EPIC, path=rail)

    lines = pool.drain_and_report(rail, epic=707)
    assert len(lines) == 1
    assert "#910" in lines[0] and "#911" in lines[0]
    assert "707" in lines[0]
    assert pool.numbers(rail) == []


def test_drain_and_report_is_silent_on_an_empty_pool(tmp_path):
    assert pool.drain_and_report(tmp_path / "pool.jsonl") == []


def test_note_refuses_a_bad_issue_number(tmp_path):
    for bad in (0, -1, True, "910"):
        with pytest.raises(pool.PoolInvalid):
            pool.note(bad, pool.REASON_OUT_OF_EPIC, path=tmp_path / "pool.jsonl")


def test_note_refuses_a_blank_reason(tmp_path):
    with pytest.raises(pool.PoolInvalid):
        pool.note(910, "   ", path=tmp_path / "pool.jsonl")


def test_the_rail_is_append_only_across_writers(tmp_path):
    """Two parks of the same issue both survive — the rail is a log, not a set."""
    rail = tmp_path / "pool.jsonl"
    pool.note(910, pool.REASON_OUT_OF_EPIC, at="2026-09-14T00:00:00Z", path=rail)
    pool.note(910, pool.REASON_OUT_OF_EPIC, at="2026-09-14T01:00:00Z", path=rail)
    assert [r.at for r in pool.read(rail)] == ["2026-09-14T00:00:00Z", "2026-09-14T01:00:00Z"]
    assert pool.numbers(rail) == [910]


def test_self_control_is_clean():
    assert pool.self_control() == []


def test_the_pool_path_is_the_board_rail():
    """The default is the committed board rail (the autouse fixture only patches it)."""
    source = Path(pool.__file__).read_text(encoding="utf-8")
    assert 'POOL_PATH = Path(".board/pool.jsonl")' in source