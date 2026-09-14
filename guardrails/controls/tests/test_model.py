"""Model invariants: default OFF, closed status vocabulary, single-record toggle."""

from __future__ import annotations

import pytest

from controls.model import (
    BLOCKED_STATUSES,
    GUARDRAIL_STATUS,
    PASSED_STATUSES,
    STATUS_BLOCKED,
    STATUS_PASSED,
    ControlError,
    ControlSet,
    ControlState,
    PolicyControl,
    UnknownControlError,
    assert_refuses_default_on,
    is_status,
    status_name,
)


def _control(control_id: str = "demo", mode: str = "block") -> PolicyControl:
    return PolicyControl(
        id=control_id,
        name="Demo",
        description="demo control",
        mode=mode,
        owner="guardrails/policy",
        audit_ref=f"guardrails/policy/controls.yaml#{control_id}",
    )


def test_status_vocabulary_is_closed():
    assert is_status(STATUS_PASSED) and is_status(STATUS_BLOCKED)
    assert not is_status(999)
    assert not is_status("246")
    assert not is_status(True)  # bool is not a status code
    assert status_name(STATUS_PASSED) == "PASSED"
    assert status_name(STATUS_BLOCKED) == "BLOCKED"
    with pytest.raises(ControlError):
        status_name(999)


def test_status_frozensets_are_the_pair():
    assert GUARDRAIL_STATUS == frozenset({STATUS_PASSED, STATUS_BLOCKED})
    assert PASSED_STATUSES == frozenset({STATUS_PASSED})
    assert BLOCKED_STATUSES == frozenset({STATUS_BLOCKED})


def test_control_defaults_off():
    control = _control()
    assert control.default_enabled is False
    assert control.to_dict()["default_enabled"] is False


def test_control_refuses_default_on():
    with pytest.raises(ControlError):
        PolicyControl(
            id="bad",
            name="Bad",
            description="ships ON",
            mode="block",
            owner="x",
            audit_ref="x",
            default_enabled=True,
        )


def test_control_refuses_bad_id_and_mode():
    with pytest.raises(ControlError):
        _control("Bad_Id")
    with pytest.raises(ControlError):
        _control("demo", mode="explode")


def test_control_state_status_codes():
    control = _control()
    assert ControlState(control, enabled=False).status == STATUS_PASSED
    assert ControlState(control, enabled=True).status == STATUS_BLOCKED
    assert ControlState(control, enabled=True).status_name == "BLOCKED"


def test_control_set_is_default_off(control_set):
    assert control_set.all_default_off()
    assert control_set.enabled_ids() == ()
    assert all(not state.enabled for state in control_set.states())


def test_toggle_writes_exactly_one_audit_record(control_set, audit_log):
    control_id = control_set.ids()[0]
    record = control_set.toggle(control_id, True, actor="tester", audit_log=audit_log)
    assert len(audit_log) == 1
    assert record.sequence == 1
    assert record.control_id == control_id
    assert record.before is False and record.after is True
    assert record.status_before == STATUS_PASSED
    assert record.status_after == STATUS_BLOCKED
    assert record.actor == "tester"


def test_toggle_flips_when_target_omitted(control_set, audit_log):
    control_id = control_set.ids()[0]
    control_set.toggle(control_id, True, actor="tester", audit_log=audit_log)
    control_set.toggle(control_id, actor="tester", audit_log=audit_log)
    assert control_set.is_enabled(control_id) is False
    assert len(audit_log) == 2


def test_unknown_control_is_refused(control_set, audit_log):
    with pytest.raises(UnknownControlError):
        control_set.toggle("does-not-exist", True, actor="tester", audit_log=audit_log)
    with pytest.raises(UnknownControlError):
        control_set.is_enabled("does-not-exist")
    assert len(audit_log) == 0


def test_off_mode_control_cannot_be_enabled():
    control_set = ControlSet([_control("wired-nowhere", mode="off")])
    with pytest.raises(ControlError):
        control_set.toggle("wired-nowhere", True, actor="tester", audit_log=object())


def test_state_dict_round_trips(control_set):
    control_id = control_set.ids()[0]
    control_set.toggle(control_id, True, actor="tester", audit_log=_sink())
    state = control_set.state_dict()
    assert state["controls"][control_id] is True
    hydrated = ControlSet(
        [_control(control_id)], state=state["controls"]
    )
    assert hydrated.is_enabled(control_id) is True


def test_assert_refuses_default_on_bites():
    assert_refuses_default_on()  # raises AssertionError if the invariant lapses


def _sink():
    from controls.audit import InMemoryControlAuditLog

    return InMemoryControlAuditLog()
