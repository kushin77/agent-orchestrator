"""The negative-control driver: complete coverage, and a driver that can fail."""

from __future__ import annotations

import io
from typing import List

from integrations.erp.ops import negative_control as nc
from integrations.erp.ops import procurement
from integrations.erp.ops.model import REFUSALS


def collect() -> nc.Report:
    return nc.run(stream=io.StringIO())


def test_every_declared_refusal_has_a_control() -> None:
    report = collect()
    assert report.missing == (), f"no control for {list(report.missing)}"
    assert report.extra == (), f"control(s) for undeclared code(s) {list(report.extra)}"


def test_every_control_is_refused_by_name() -> None:
    report = collect()
    offenders: List[str] = [
        f"{result.code} ({result.name}): {result.detail}"
        for result in report.results
        if result.verdict != "refused"
    ]
    assert offenders == []


def test_the_coverage_line_counts_every_code() -> None:
    report = collect()
    assert report.coverage_line() == (
        f"{len(REFUSALS)} of {len(REFUSALS)} declared refusal(s) provoked"
    )
    assert report.ok


def test_every_provocation_uses_a_code_the_model_declares() -> None:
    for provocation in nc.provocations():
        assert provocation.code in REFUSALS, provocation.code
        assert provocation.needle, f"{provocation.code} declares no offender to name"


def test_the_driver_can_fail(monkeypatch) -> None:
    """The property that makes the coverage line evidence rather than a claim.

    The rule that refuses a receipt citing no order is replaced with one that
    answers every citation, and the driver must notice: the provocation for
    ``receipt-without-order`` can no longer be refused, so the driver goes
    non-zero and the code drops out of the provoked set. A driver that still
    reported OK here would prove nothing about the controls it reports.
    """
    fake = {
        "id": "PO-9999",
        "doctype": "purchase-order",
        "state": "submitted",
        "docstatus": 1,
        "company": "COMPANY-1",
        "currency": "EUR",
        "supplier": "SUP-1",
        "lines": [{"item_code": "RAW-A", "qty": 1, "rate": 5.0}],
    }
    monkeypatch.setattr(
        procurement,
        "_purchase_order",
        lambda space, order_id, *, what: fake,
    )
    report = collect()
    assert not report.ok
    assert "receipt-without-order" not in report.refused
    assert any(
        result.code == "receipt-without-order" and result.verdict != "refused"
        for result in report.results
    )


def test_main_exits_non_zero_when_a_control_fails(monkeypatch) -> None:
    monkeypatch.setattr(procurement, "_purchase_order", lambda space, order_id, *, what: None)
    assert nc.main([]) == 1


def test_the_report_records_a_verdict_per_provocation() -> None:
    report = collect()
    assert len(report.results) == len(nc.provocations())
    verdicts = {result.verdict for result in report.results}
    assert verdicts == {"refused"}
