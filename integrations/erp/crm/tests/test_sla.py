"""Deterministic SLA ageing for support issues (issue #650, AC2)."""

from __future__ import annotations

import pytest

from integrations.erp.crm import sla
from integrations.erp.crm.model import Document, Refused

OPENED = "2026-09-10T08:00:00Z"


def issue(**overrides) -> Document:
    fields = {"priority": "p2", "channel": "email", "opened_at": OPENED}
    fields.update(overrides)
    fields = {name: value for name, value in fields.items() if value is not None}
    return Document(
        kind="support-issue", id="ISS-0001", tenant="acme", state="open", fields=fields
    )


def test_the_declared_windows_drive_the_due_instants(definitions) -> None:
    """p2: respond 1440 minutes, resolve 4320, warning at 75%."""
    state = sla.age(issue(), "2026-09-10T23:00:00Z", definitions=definitions)
    assert state.policy == "p2"
    assert state.response.due_at == "2026-09-11T08:00:00Z"
    assert state.resolution.due_at == "2026-09-13T08:00:00Z"


def test_a_clock_inside_the_warning_band_is_ok(definitions) -> None:
    state = sla.age(issue(), "2026-09-10T23:00:00Z", definitions=definitions)
    assert state.state == "ok"
    assert state.response.phase == "running"
    assert state.response.elapsed_minutes == 900
    assert state.resolution.state == "ok"


def test_the_warning_band_is_reported_as_due(definitions) -> None:
    state = sla.age(issue(), "2026-09-11T04:00:00Z", definitions=definitions)
    assert state.response.elapsed_minutes == 1200
    assert state.response.state == "due"
    assert state.resolution.state == "ok"
    assert state.state == "due"


def test_passing_the_window_is_a_breach(definitions) -> None:
    state = sla.age(issue(), "2026-09-11T12:00:00Z", definitions=definitions)
    assert state.response.state == "breached"
    assert state.response.remaining_minutes == 0
    assert state.resolution.state == "ok"
    assert state.state == "breached"


def test_the_worst_clock_decides_the_issue_state(definitions) -> None:
    response_ok = sla.age(issue(responded_at="2026-09-10T09:00:00Z"), "2026-09-10T09:30:00Z", definitions=definitions)
    assert response_ok.response.state == "ok"
    assert response_ok.state == "ok"

    late = sla.age(issue(responded_at="2026-09-13T08:00:00Z"), "2026-09-13T09:00:00Z", definitions=definitions)
    assert late.response.state == "breached"
    assert late.state == "breached"


def test_a_stopped_clock_is_judged_at_its_stop_not_at_the_reading_instant(definitions) -> None:
    """Closing an issue must not retroactively erase a breach."""
    state = sla.age(
        issue(responded_at="2026-09-11T13:00:00Z", resolved_at="2026-09-11T14:00:00Z"),
        "2026-09-20T08:00:00Z",
        definitions=definitions,
    )
    assert state.response.phase == "stopped"
    assert state.response.elapsed_minutes == 1740
    assert state.response.state == "breached"
    assert state.resolution.phase == "stopped"
    assert state.resolution.elapsed_minutes == 1800
    assert state.resolution.state == "ok"
    # The breach survives the close: the issue is breached on its response clock
    # even though the resolution clock stopped inside its own window.
    assert state.state == "breached"


def test_a_stop_later_than_the_reading_instant_leaves_the_clock_running(definitions) -> None:
    """Reading at 08:10 must not answer a question about the 13:00 response."""
    state = sla.age(
        issue(responded_at="2026-09-11T13:00:00Z"), "2026-09-10T08:10:00Z", definitions=definitions
    )
    assert state.response.phase == "running"
    assert state.response.elapsed_minutes == 10
    assert state.state == "ok"


def test_ageing_is_deterministic(definitions) -> None:
    first = sla.age(issue(), "2026-09-11T04:00:00Z", definitions=definitions)
    second = sla.age(issue(), "2026-09-11T04:00:00Z", definitions=definitions)
    assert first == second
    assert first.to_dict() == second.to_dict()


def test_the_golden_path_readings_cover_every_verdict(golden) -> None:
    verdicts = {state.state for state in golden.sla_states.values()}
    assert verdicts == {"ok", "due", "breached"}
    assert golden.sla_states["ISS-0002/breached"].response.state == "breached"


def test_the_policy_named_by_the_priority_field_is_the_one_used(golden) -> None:
    assert golden.sla_states["ISS-0002/due"].policy == "p2"
    assert golden.sla_states["ISS-0001/at-open"].policy == "p1"


def test_an_undeclared_policy_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="unknown-policy") as caught:
        sla.age(issue(priority="p9"), OPENED, definitions=definitions)
    assert "p9" in caught.value.detail


def test_an_issue_with_no_usable_priority_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="unknown-policy") as caught:
        sla.age(issue(priority=None), OPENED, definitions=definitions)
    assert "no usable priority field" in caught.value.detail


def test_a_reading_before_the_issue_opened_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="clock-regression") as caught:
        sla.age(issue(), "2026-09-10T07:00:00Z", definitions=definitions)
    assert "ISS-0001" in caught.value.detail


def test_a_response_before_the_issue_opened_is_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="clock-regression") as caught:
        sla.age(
            issue(responded_at="2026-09-10T07:30:00Z"),
            "2026-09-10T09:00:00Z",
            definitions=definitions,
        )
    assert "ISS-0001" in caught.value.detail


def test_a_reading_instant_that_is_not_a_timestamp_is_refused(definitions) -> None:
    for value in ("yesterday", "2026-09-10", "2026-09-10T08:00:00+02:00"):
        with pytest.raises(Refused, match="invalid-timestamp") as caught:
            sla.age(issue(), value, definitions=definitions)
        assert "not in the accepted form" in caught.value.detail


def test_a_non_string_timestamp_field_is_refused(definitions) -> None:
    with pytest.raises(Refused, match="invalid-timestamp") as caught:
        sla.age(issue(opened_at=20260910), OPENED, definitions=definitions)
    assert "expected an ISO-8601 UTC string" in caught.value.detail


def test_worst_orders_the_verdicts() -> None:
    assert sla.worst("ok", "ok") == "ok"
    assert sla.worst("ok", "due") == "due"
    assert sla.worst("due", "breached") == "breached"
    assert sla.worst() == "ok"


def test_timestamps_round_trip() -> None:
    moment = sla.parse_timestamp(OPENED, where="test")
    assert sla.format_timestamp(moment) == OPENED
    assert sla.add_minutes(moment, 1440).strftime(sla.TS_FORMAT) == "2026-09-11T08:00:00Z"
    assert sla.minutes_between(moment, sla.add_minutes(moment, 90)) == 90
