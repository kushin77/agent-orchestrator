"""Timesheet accumulation and the derived cost rollup (issue #650, AC2)."""

from __future__ import annotations

import pytest

from integrations.erp.crm import flows, timesheet
from integrations.erp.crm.model import Document, Refused

AT = "2026-09-01T09:00:00Z"


def task_fields(**overrides):
    fields = {
        "project": "PROJ-0001",
        "task_type": "implementation",
        "estimate_minutes": 240,
        "rate_minor": 9000,
        "currency": "EUR",
    }
    fields.update(overrides)
    return fields


def build(space, **task_overrides):
    """A project with one task, ready for timesheet entries."""
    space = flows.start_project(
        space,
        "PROJ-0001",
        {"project_type": "delivery", "currency": "EUR"},
        actor="pm",
        at=AT,
    )
    return flows.start_task(space, "TASK-0001", task_fields(**task_overrides), actor="pm", at=AT)


def entry(space, entry_id: str, **overrides):
    fields = {
        "project": "PROJ-0001",
        "task": "TASK-0001",
        "minutes": 60,
        "work_date": "2026-09-06",
        "rate_minor": 9000,
        "currency": "EUR",
    }
    fields.update(overrides)
    return flows.log_timesheet(space, entry_id, fields, actor="rep-1", at=AT)


def test_per_line_amount_rounds_half_up_once() -> None:
    assert timesheet.amount_minor(120, 9000) == 18000
    assert timesheet.amount_minor(90, 9000) == 13500
    assert timesheet.amount_minor(30, 1) == 1  # 0.5 rounds up
    assert timesheet.amount_minor(29, 1) == 0  # 0.4833 rounds down
    assert timesheet.amount_minor(90, 1) == 2  # 1.5 rounds up
    assert timesheet.amount_minor(0, 1) == 0


def test_the_golden_path_rollup_counts_settled_work_only(golden) -> None:
    rollup = golden.rollup
    assert (rollup.project, rollup.task, rollup.currency) == ("PROJ-0001", "TASK-0001", "EUR")
    assert rollup.minutes == 210
    assert rollup.amount_minor == 31500
    assert [line.entry for line in rollup.lines] == ["TS-0001", "TS-0002"]


def test_a_rejected_entry_is_excluded_and_a_draft_one_too(golden) -> None:
    """Counting either would inflate a cost report with work nobody accepted."""
    assert golden.workspace.get("TS-0003").state == "rejected"
    assert "TS-0003" not in [line.entry for line in golden.rollup.lines]


def test_the_rollup_is_derived_not_stored(golden) -> None:
    """Accepting the rejected entry changes the derived value and nothing else."""
    space = golden.workspace
    space = flows.advance(space, "TS-0003", "draft", actor="rep-1", at=AT)
    space = flows.advance(space, "TS-0003", "submitted", actor="rep-1", at=AT)
    space = flows.approve_timesheet(space, "TS-0003", actor="pm", at=AT)
    rollup = flows.project_costs(space, "PROJ-0001", "TASK-0001")
    assert rollup.minutes == 240
    assert rollup.amount_minor == 36000
    assert len(rollup.lines) == 3


def test_a_submitted_entry_counts_and_an_approved_one_counts(golden) -> None:
    assert golden.workspace.get("TS-0002").state == "submitted"
    assert [line.minutes for line in golden.rollup.lines] == [120, 90]


def test_the_rollup_is_deterministic(golden) -> None:
    first = flows.project_costs(golden.workspace, "PROJ-0001", "TASK-0001")
    second = flows.project_costs(golden.workspace, "PROJ-0001", "TASK-0001")
    assert first.to_dict() == second.to_dict()


def test_the_rate_falls_back_from_the_entry_to_the_task(space) -> None:
    space = build(space)
    space = flows.log_timesheet(
        space,
        "TS-0001",
        {"project": "PROJ-0001", "task": "TASK-0001", "minutes": 60, "work_date": "2026-09-06"},
        actor="rep-1",
        at=AT,
    )
    rollup = flows.project_costs(space, "PROJ-0001", "TASK-0001")
    assert rollup.lines[0].rate_minor == 9000
    assert rollup.currency == "EUR"


def test_a_missing_task_is_refused_by_name(space) -> None:
    space = build(space)
    with pytest.raises(Refused, match="unknown-task") as caught:
        timesheet.accumulate(
            space.all_documents(), project="PROJ-0001", task="TASK-9999", definitions=space.definitions
        )
    assert "TASK-9999" in caught.value.detail


def test_a_missing_project_is_refused_by_name(space) -> None:
    space = build(space)
    with pytest.raises(Refused, match="unknown-document") as caught:
        timesheet.accumulate(
            space.all_documents(), project="PROJ-9999", task="TASK-0001", definitions=space.definitions
        )
    assert "PROJ-9999" in caught.value.detail


def test_a_completed_project_is_refused_by_name(space) -> None:
    space = build(space)
    space = flows.advance(space, "PROJ-0001", "completed", actor="pm", at=AT)
    with pytest.raises(Refused, match="inactive-parent") as caught:
        timesheet.accumulate(
            space.all_documents(), project="PROJ-0001", task="TASK-0001", definitions=space.definitions
        )
    assert "PROJ-0001" in caught.value.detail and "completed" in caught.value.detail


def test_a_closed_task_is_refused_by_name(space) -> None:
    space = build(space)
    space = flows.advance(space, "TASK-0001", "in-progress", actor="pm", at=AT)
    space = flows.advance(space, "TASK-0001", "done", actor="pm", at=AT)
    with pytest.raises(Refused, match="inactive-parent") as caught:
        timesheet.accumulate(
            space.all_documents(), project="PROJ-0001", task="TASK-0001", definitions=space.definitions
        )
    assert "TASK-0001" in caught.value.detail


def test_a_task_in_another_project_is_refused_by_name(space) -> None:
    space = build(space)
    space = flows.start_project(
        space, "PROJ-0002", {"project_type": "internal", "currency": "EUR"}, actor="pm", at=AT
    )
    space = flows.start_task(
        space, "TASK-0002", {"project": "PROJ-0002", "task_type": "review"}, actor="pm", at=AT
    )
    with pytest.raises(Refused, match="wrong-project") as caught:
        timesheet.accumulate(
            space.all_documents(), project="PROJ-0001", task="TASK-0002", definitions=space.definitions
        )
    assert "PROJ-0002" in caught.value.detail and "PROJ-0001" in caught.value.detail


def test_an_entry_booked_to_another_project_is_refused_by_name(space) -> None:
    space = build(space)
    space = flows.start_project(
        space, "PROJ-0002", {"project_type": "internal", "currency": "EUR"}, actor="pm", at=AT
    )
    misplaced = Document(
        kind="timesheet",
        id="TS-9001",
        tenant="acme",
        state="submitted",
        fields={
            "project": "PROJ-0002",
            "task": "TASK-0001",
            "minutes": 60,
            "work_date": "2026-09-06",
            "rate_minor": 9000,
            "currency": "EUR",
        },
    )
    with pytest.raises(Refused, match="wrong-project") as caught:
        timesheet.accumulate(
            list(space.all_documents()) + [misplaced],
            project="PROJ-0001",
            task="TASK-0001",
            definitions=space.definitions,
        )
    assert "TS-9001" in caught.value.detail


def test_a_duplicated_entry_is_refused_by_name(space) -> None:
    space = build(space)
    space = entry(space, "TS-0001")
    with pytest.raises(Refused, match="duplicate-entry") as caught:
        timesheet.accumulate(
            list(space.all_documents()) + [space.get("TS-0001")],
            project="PROJ-0001",
            task="TASK-0001",
            definitions=space.definitions,
        )
    assert "TS-0001" in caught.value.detail


def test_a_currency_disagreement_is_refused_by_name(space) -> None:
    space = build(space)
    space = entry(space, "TS-0001", currency="USD")
    with pytest.raises(Refused, match="currency-mismatch") as caught:
        timesheet.accumulate(
            space.all_documents(), project="PROJ-0001", task="TASK-0001", definitions=space.definitions
        )
    assert "USD" in caught.value.detail and "EUR" in caught.value.detail


def test_minutes_outside_the_permitted_range_are_refused(space) -> None:
    space = build(space)
    for minutes in (0, 1441, -5):
        stray = Document(
            kind="timesheet",
            id="TS-0001",
            tenant="acme",
            state="submitted",
            fields={
                "project": "PROJ-0001",
                "task": "TASK-0001",
                "minutes": minutes,
                "work_date": "2026-09-06",
                "rate_minor": 9000,
                "currency": "EUR",
            },
        )
        with pytest.raises(Refused, match="invalid-value") as caught:
            timesheet.accumulate(
                list(space.all_documents()) + [stray],
                project="PROJ-0001",
                task="TASK-0001",
                definitions=space.definitions,
            )
        assert "outside 1..1440" in caught.value.detail


def test_an_entry_with_no_resolvable_rate_is_refused_by_name(space) -> None:
    space = flows.start_project(
        space,
        "PROJ-0001",
        {"project_type": "delivery", "currency": "EUR"},
        actor="pm",
        at=AT,
    )
    space = flows.start_task(
        space, "TASK-0001", {"project": "PROJ-0001", "task_type": "review"}, actor="pm", at=AT
    )
    space = flows.log_timesheet(
        space,
        "TS-0001",
        {"project": "PROJ-0001", "task": "TASK-0001", "minutes": 60, "work_date": "2026-09-06"},
        actor="rep-1",
        at=AT,
    )
    with pytest.raises(Refused, match="invalid-value") as caught:
        timesheet.accumulate(
            space.all_documents(), project="PROJ-0001", task="TASK-0001", definitions=space.definitions
        )
    assert "no rate can be resolved" in caught.value.detail


def test_a_rollup_against_no_entries_is_zero_not_a_refusal(space) -> None:
    """An empty rollup is a real answer; a missing parent is not."""
    space = build(space)
    rollup = flows.project_costs(space, "PROJ-0001", "TASK-0001")
    assert rollup.minutes == 0 and rollup.amount_minor == 0 and rollup.lines == ()
