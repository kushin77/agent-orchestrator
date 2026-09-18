from __future__ import annotations

from datetime import date

import pytest

from model import (
    STATUS_CANNOT_ASSESS,
    STATUS_EXCEPTED,
    STATUS_NOT_OK,
    STATUS_OK,
    CheckResult,
    Exception_,
    ExceptionInvalid,
    aggregate_status,
)


def _result(status: str, name: str = "x") -> CheckResult:
    return CheckResult(name=name, command="true", status=status, exit_code=0)


def test_aggregate_status_empty_is_cannot_assess():
    assert aggregate_status([]) == STATUS_CANNOT_ASSESS


def test_aggregate_status_all_ok():
    assert aggregate_status([_result(STATUS_OK), _result(STATUS_OK)]) == STATUS_OK


def test_aggregate_status_any_not_ok_wins():
    checks = [_result(STATUS_OK), _result(STATUS_NOT_OK), _result(STATUS_CANNOT_ASSESS)]
    assert aggregate_status(checks) == STATUS_NOT_OK


def test_aggregate_status_cannot_assess_without_not_ok():
    checks = [_result(STATUS_OK), _result(STATUS_CANNOT_ASSESS)]
    assert aggregate_status(checks) == STATUS_CANNOT_ASSESS


def test_aggregate_status_excepted_does_not_fail_gate():
    checks = [_result(STATUS_OK), _result(STATUS_EXCEPTED)]
    assert aggregate_status(checks) == STATUS_OK


def test_exception_is_active_before_expiry():
    exc = Exception_(check="lessons", reason="r", approved_by="chair", expires="2099-01-01")
    assert exc.is_active(today=date(2026, 1, 1)) is True


def test_exception_is_not_active_after_expiry():
    exc = Exception_(check="lessons", reason="r", approved_by="chair", expires="2020-01-01")
    assert exc.is_active(today=date(2026, 1, 1)) is False


def test_exception_expiry_is_exclusive():
    exc = Exception_(check="lessons", reason="r", approved_by="chair", expires="2026-01-01")
    assert exc.is_active(today=date(2026, 1, 1)) is False


def test_exception_bad_date_raises():
    exc = Exception_(check="lessons", reason="r", approved_by="chair", expires="not-a-date")
    with pytest.raises(ExceptionInvalid):
        exc.is_active(today=date(2026, 1, 1))
