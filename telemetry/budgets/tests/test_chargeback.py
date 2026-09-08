"""telemetry/budgets — chargeback report tests (issue #34).

Per-tenant chargeback lines are an honest sum over the metering feed
(issue #33): cost/tokens per tenant per month, with unmetered calls surfaced
separately — never billed as zero.
"""

from __future__ import annotations

from pathlib import Path

from telemetry.budgets.chargeback import ChargebackReportGenerator
from conftest import T_SEP_08, call_record


def _reporter(ingest, reporter):
    # three billed calls for acme in 2026-09, one for globex
    ingest(call_record(tenant="acme", agent="coder-1", model="deepseek-chat",
                       provider="deepseek", estimate=0.01, ts=T_SEP_08))
    ingest(call_record(tenant="acme", agent="coder-1", model="deepseek-chat",
                       provider="deepseek", estimate=0.02, ts=T_SEP_08))
    ingest(call_record(tenant="acme", agent="arch-2", model="gemini-2.5-pro",
                       provider="gemini", estimate=0.03, ts=T_SEP_08))
    ingest(call_record(tenant="globex", agent="arch-1", model="deepseek-reasoner",
                       provider="deepseek", estimate=0.04, ts=T_SEP_08))
    return reporter()


def test_chargeback_rows_per_tenant(metering_reporter):
    ingest, reporter = metering_reporter
    usage_reporter = _reporter(ingest, reporter)
    generator = ChargebackReportGenerator(usage_reporter)
    rows = generator.report(month="2026-09")
    by_tenant = {r.tenant_id: r for r in rows}
    assert set(by_tenant) == {"acme", "globex"}
    assert by_tenant["acme"].calls == 3
    assert by_tenant["acme"].total_tokens == 0  # CallRecords carry no tokens
    assert abs(by_tenant["acme"].cost_usd - 0.06) < 1e-9
    assert abs(by_tenant["globex"].cost_usd - 0.04) < 1e-9


def test_chargeback_month_filter(metering_reporter):
    ingest, reporter = metering_reporter
    usage_reporter = _reporter(ingest, reporter)
    generator = ChargebackReportGenerator(usage_reporter)
    rows = generator.report(month="2030-01")  # a month with no usage
    assert rows == []


def test_chargeback_totals(metering_reporter):
    ingest, reporter = metering_reporter
    usage_reporter = _reporter(ingest, reporter)
    generator = ChargebackReportGenerator(usage_reporter)
    totals = generator.totals()
    assert totals["calls"] == 4
    assert abs(totals["costUsd"] - 0.10) < 1e-9
    assert totals["tenants"] == 2


def test_chargeback_csv(tmp_path: Path, metering_reporter):
    ingest, reporter = metering_reporter
    usage_reporter = _reporter(ingest, reporter)
    generator = ChargebackReportGenerator(usage_reporter)
    out = generator.write_csv(tmp_path / "chargeback.csv", month="2026-09")
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("tenantId")
    assert len(lines) == 3  # header + acme + globex


def test_chargeback_json(tmp_path: Path, metering_reporter):
    ingest, reporter = metering_reporter
    usage_reporter = _reporter(ingest, reporter)
    generator = ChargebackReportGenerator(usage_reporter)
    out = generator.write_json(tmp_path / "chargeback.json")
    import json

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["rows"]) == 2  # one per tenant (no month filter)
    assert payload["totals"]["calls"] == 4
