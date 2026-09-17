from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate import load_exceptions, run_gate, write_report
from model import (
    STATUS_CANNOT_ASSESS,
    STATUS_EXCEPTED,
    STATUS_NOT_OK,
    STATUS_OK,
    ExceptionInvalid,
    REPEATED_VIOLATION_THRESHOLD,
)


def _script(tmp_path: Path, name: str, exit_code: int, output: str = "") -> str:
    path = tmp_path / name
    path.write_text(
        "#!/usr/bin/env bash\necho '%s'\nexit %d\n" % (output, exit_code),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return "bash %s" % path


def test_all_ok_checks_pass_gate(tmp_path: Path):
    checks = [("a", _script(tmp_path, "a.sh", 0)), ("b", _script(tmp_path, "b.sh", 0))]
    report = run_gate(tmp_path, checks=checks)
    assert report.status == STATUS_OK
    assert all(c.status == STATUS_OK for c in report.checks)


def test_not_ok_check_fails_gate(tmp_path: Path):
    checks = [("a", _script(tmp_path, "a.sh", 0)), ("b", _script(tmp_path, "b.sh", 1, "boom"))]
    report = run_gate(tmp_path, checks=checks)
    assert report.status == STATUS_NOT_OK
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["a"] == STATUS_OK
    assert statuses["b"] == STATUS_NOT_OK


def test_cannot_assess_check_is_never_reported_as_pass(tmp_path: Path):
    checks = [("a", _script(tmp_path, "a.sh", 0)), ("b", _script(tmp_path, "b.sh", 2))]
    report = run_gate(tmp_path, checks=checks)
    assert report.status == STATUS_CANNOT_ASSESS
    assert report.status != STATUS_OK


def test_active_exception_downgrades_failure_but_stays_visible(tmp_path: Path):
    (tmp_path / "governance" / "board").mkdir(parents=True)
    exceptions_path = tmp_path / "governance" / "board" / "exceptions.yaml"
    exceptions_path.write_text(
        "exceptions:\n"
        "  - check: b\n"
        "    reason: 'in-flight migration'\n"
        "    approved_by: chair\n"
        "    expires: '2099-01-01'\n",
        encoding="utf-8",
    )
    checks = [("a", _script(tmp_path, "a.sh", 0)), ("b", _script(tmp_path, "b.sh", 1))]
    report = run_gate(tmp_path, checks=checks, exceptions_path=exceptions_path,
                       ledger_path=tmp_path / "violations.jsonl")
    assert report.status == STATUS_OK
    b = next(c for c in report.checks if c.name == "b")
    assert b.status == STATUS_EXCEPTED
    assert b.exception_applied == "in-flight migration"


def test_expired_exception_does_not_suppress_failure(tmp_path: Path):
    exceptions_path = tmp_path / "exceptions.yaml"
    exceptions_path.write_text(
        "exceptions:\n"
        "  - check: b\n"
        "    reason: 'old'\n"
        "    approved_by: chair\n"
        "    expires: '2020-01-01'\n",
        encoding="utf-8",
    )
    checks = [("b", _script(tmp_path, "b.sh", 1))]
    report = run_gate(tmp_path, checks=checks, exceptions_path=exceptions_path,
                       ledger_path=tmp_path / "violations.jsonl")
    assert report.status == STATUS_NOT_OK
    assert "b" in report.expired_exceptions


def test_repeated_violation_escalates(tmp_path: Path):
    ledger_path = tmp_path / "violations.jsonl"
    checks = [("b", _script(tmp_path, "b.sh", 1))]
    for _ in range(REPEATED_VIOLATION_THRESHOLD):
        report = run_gate(
            tmp_path,
            checks=checks,
            exceptions_path=tmp_path / "no-exceptions.yaml",
            ledger_path=ledger_path,
        )
    assert report.escalations
    assert report.escalations[0]["check"] == "b"
    assert report.escalations[0]["occurrences"] >= REPEATED_VIOLATION_THRESHOLD


def test_malformed_exceptions_file_raises():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "exceptions.yaml"
        path.write_text("exceptions:\n  - check: a\n", encoding="utf-8")
        with pytest.raises(ExceptionInvalid):
            load_exceptions(path)


def test_write_report_roundtrip(tmp_path: Path):
    checks = [("a", _script(tmp_path, "a.sh", 0))]
    report = run_gate(tmp_path, checks=checks, ledger_path=tmp_path / "violations.jsonl")
    out = tmp_path / ".verify" / "board-report.json"
    write_report(report, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == STATUS_OK
    assert data["checks"][0]["name"] == "a"
