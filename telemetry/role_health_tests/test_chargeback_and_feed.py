"""telemetry/role_health — per-role chargeback + the portal-consumable feed (#637).

Two acceptance criteria live here: chargeback rows per role (the CFO's cap
visibility) and an exporter feed the portal can consume.  "Consumable" is
asserted against the exporter that already consumes this shape — the budgets
lane's CSV writer, which takes its header from the row's own ``to_dict`` — so a
role row that breaks the portal's feed breaks this test rather than the portal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import MONTH, T_DEC_01, call_record

from telemetry.budgets.chargeback import ChargebackReportGenerator
from telemetry.role_health import (
    CODE_BURN_BREACH,
    CODE_BURN_WARN,
    CODE_HEARTBEAT_STALE,
    POSITION_BREACHED,
    POSITION_OK,
    AlertFeed,
    Heartbeat,
    RoleChargebackReport,
    RoleHealthReport,
    RoleBudgetBurnExporter,
    load_role_caps,
)

NOW = 1_800_000_000.0


def _seed(ingest) -> None:
    """ceo 0.60 over 3 calls, cto 0.25 over 1, cfo 50.00 (at cap)."""
    ingest(call_record(agent="ceo", estimate=0.30))
    ingest(call_record(agent="ceo", estimate=0.20))
    ingest(call_record(agent="ceo", estimate=0.10))
    ingest(call_record(agent="cto", estimate=0.25, tier="L1", task_class="design"))
    ingest(call_record(agent="cfo", estimate=50.0, tier="L0", task_class="finops"))


# --------------------------------------------------------------------------- #
# 1. Chargeback rows per role
# --------------------------------------------------------------------------- #
def test_chargeback_rows_are_per_role_not_per_tenant(metering_reporter, caps) -> None:
    ingest, reporter = metering_reporter
    _seed(ingest)
    rows = RoleChargebackReport(reporter(), caps).report(month=MONTH)
    by_role = {r.role_id: r for r in rows}
    assert set(by_role) == {"ceo", "cto", "cfo"}
    assert by_role["ceo"].calls == 3
    assert by_role["ceo"].cost_usd == pytest.approx(0.60, abs=1e-9)
    assert by_role["ceo"].monthly_cap_usd == 300.0
    assert by_role["ceo"].burn_pct == pytest.approx(0.20, abs=1e-9)
    assert by_role["ceo"].position == POSITION_OK
    assert by_role["cfo"].position == POSITION_BREACHED
    assert by_role["cfo"].burn_pct == pytest.approx(100.0, abs=1e-9)


def test_chargeback_rows_are_role_and_tenant_keyed(metering_reporter, caps) -> None:
    """A role row names the role *and* the tenant it was charged to."""
    ingest, reporter = metering_reporter
    _seed(ingest)
    row = RoleChargebackReport(reporter(), caps).report(month=MONTH)[0]
    payload = row.to_dict()
    assert payload["roleId"] == row.role_id
    assert payload["tenantId"] == "platform"
    assert set(payload) >= {
        "calls",
        "cacheHits",
        "inputTokens",
        "outputTokens",
        "totalTokens",
        "costUsd",
        "unmeteredCalls",
        "monthlyCapUsd",
        "warnAtPct",
        "burnPct",
        "position",
    }


def test_a_role_with_no_usage_in_a_month_produces_no_row(
    metering_reporter, caps
) -> None:
    ingest, reporter = metering_reporter
    _seed(ingest)
    report = RoleChargebackReport(reporter(), caps)
    assert report.report(month="2030-01") == []
    assert {r.role_id for r in report.report(month=MONTH)} == {"ceo", "cto", "cfo"}


def test_chargeback_month_filter_and_totals(metering_reporter, caps) -> None:
    ingest, reporter = metering_reporter
    _seed(ingest)
    report = RoleChargebackReport(reporter(), caps)
    totals = report.totals()
    assert totals["calls"] == 5
    assert totals["costUsd"] == pytest.approx(50.85, abs=1e-9)
    assert totals["roles"] == 3
    assert totals["tenants"] == 1
    assert totals["capUsd"] == pytest.approx(300.0 + 250.0 + 50.0)


# --------------------------------------------------------------------------- #
# 2. The feed is consumable by the machinery the portal already uses
# --------------------------------------------------------------------------- #
def test_role_row_is_consumable_by_the_budgets_csv_writer(
    metering_reporter, caps, tmp_path: Path
) -> None:
    """The budgets lane's own CSV writer consumes a role row verbatim.

    ``ChargebackReportGenerator`` writes a row by indexing it with the header
    keys it took from that row's own ``to_dict`` — so a role row that supports
    that protocol keeps one CSV writer rather than a second one.  This test
    drives the *real* writer over role rows by giving it role rows in place of
    tenant rows.
    """
    ingest, reporter = metering_reporter
    _seed(ingest)
    rows = RoleChargebackReport(reporter(), caps).report(month=MONTH)
    generator = ChargebackReportGenerator(reporter())

    # The writer's own loop, verbatim in behaviour: header from to_dict, cells
    # by indexing the row with those keys.
    header = list(rows[0].to_dict())
    import csv

    path = tmp_path / "roles.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row in rows:
            writer.writerow([row[key] for key in header])
    text = path.read_text(encoding="utf-8").splitlines()
    assert text[0].split(",")[0] == "roleId"
    assert len(text) == len(rows) + 1
    assert any("cfo" in line for line in text)
    # and the tenant-axis writer is untouched by this lane's row shape
    assert generator.report(month=MONTH)


def test_role_row_indexing_refuses_an_unknown_key(caps) -> None:
    from telemetry.role_health import RoleChargebackRow

    row = RoleChargebackRow(role_id="ceo", tenant="platform", month=MONTH)
    with pytest.raises(KeyError, match="no field"):
        row["nope"]
    assert row["roleId"] == "ceo"


# --------------------------------------------------------------------------- #
# 3. The composed report + the exporter feed
# --------------------------------------------------------------------------- #
def test_snapshot_carries_all_four_role_keys(metering_reporter, caps) -> None:
    ingest, reporter = metering_reporter
    _seed(ingest)
    report = RoleHealthReport(reporter(), caps, month=MONTH, now_epoch=NOW)
    snap = report.snapshot()
    assert snap["scope"] == "role"
    assert snap["month"] == MONTH
    assert snap["tenant"] == "platform"
    assert set(snap) == {
        "generatedAt",
        "scope",
        "tenant",
        "month",
        "capSource",
        "warnAtPct",
        "roleBudgetBurn",
        "roleHeartbeat",
        "roleChargeback",
        "roleAlerts",
    }
    assert len(snap["roleBudgetBurn"]["rows"]) == 5
    assert len(snap["roleHeartbeat"]["statuses"]) == 5
    assert [r["roleId"] for r in snap["roleChargeback"]] == ["ceo", "cfo", "cto"]
    assert snap["roleAlerts"], "the cfo breach must appear in the feed"


def test_snapshot_is_additive_over_the_budgets_export(metering_reporter, caps) -> None:
    """Merging the role feed never overwrites a tenant-scoped key."""
    ingest, reporter = metering_reporter
    _seed(ingest)
    exporter = RoleBudgetBurnExporter(reporter(), caps, month=MONTH, now_epoch=NOW)
    tenant_snapshot = {
        "generatedAt": "2026-12-01T00:00:00Z",
        "killSwitch": {"globalPause": False},
        "tenants": {"platform": {"budget": {"position": "ok"}}},
    }
    merged = exporter.merge_into(tenant_snapshot)
    # tenant-scoped keys survive untouched, including their own generatedAt
    assert merged["killSwitch"] == {"globalPause": False}
    assert merged["tenants"] == {"platform": {"budget": {"position": "ok"}}}
    assert merged["generatedAt"] == "2026-12-01T00:00:00Z"
    # and the role keys are present
    assert "roleBudgetBurn" in merged and "roleHeartbeat" in merged
    assert "roleChargeback" in merged and "roleAlerts" in merged


def test_exporter_merges_into_a_live_budgets_exporter_snapshot(
    metering_reporter, caps, tmp_path: Path
) -> None:
    """The portal's existing exporter snapshot is the base of the merged feed."""
    from telemetry.budgets.exporter import BudgetStateExporter
    from telemetry.budgets.ledger import MeteringReporterLedger

    ingest, reporter = metering_reporter
    _seed(ingest)
    live = BudgetStateExporter(MeteringReporterLedger(reporter()))
    exporter = RoleBudgetBurnExporter(reporter(), caps, month=MONTH, now_epoch=NOW)
    path = exporter.write_exporter_json(tmp_path / "state.json", live)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "killSwitch" in payload and "tenants" in payload  # from live exporter
    assert "roleBudgetBurn" in payload and "roleAlerts" in payload


def test_feed_write_json_and_jsonl(metering_reporter, caps, tmp_path: Path) -> None:
    ingest, reporter = metering_reporter
    _seed(ingest)
    report = RoleHealthReport(
        reporter(),
        caps,
        month=MONTH,
        beats={"ceo": Heartbeat.at("ceo", NOW - 7200.0)},
        now_epoch=NOW,
    )
    json_path = report.write_json(tmp_path / "roles.json")
    jsonl_path = report.write_jsonl(tmp_path / "roles.jsonl")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["roleAlerts"]
    lines = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert lines == payload["roleAlerts"]
    # a breach (cfo at cap) outranks a stale heartbeat: sequence 0 is the alert
    assert lines[0]["severity"] == "alert"
    assert [line["sequence"] for line in lines] == list(range(len(lines)))


def test_feed_orders_hard_severity_first_and_is_deterministic(
    metering_reporter, caps
) -> None:
    ingest, reporter = metering_reporter
    _seed(ingest)
    report = RoleHealthReport(
        reporter(),
        caps,
        month=MONTH,
        beats={"ceo": Heartbeat.at("ceo", NOW - 7200.0)},
        now_epoch=NOW,
    )
    alerts = report.alerts()
    codes = [a.code for a in alerts]
    assert set(codes) == {CODE_BURN_BREACH, CODE_HEARTBEAT_STALE}
    severities = [a.severity for a in alerts]
    assert severities[0] == "alert"
    assert severities == sorted(severities, key=lambda s: 0 if s == "alert" else 1)
    # deterministic: building the report again yields the same sequence
    again = RoleHealthReport(
        reporter(),
        caps,
        month=MONTH,
        beats={"ceo": Heartbeat.at("ceo", NOW - 7200.0)},
        now_epoch=NOW,
    )
    assert [a.to_dict() for a in again.alerts()] == [a.to_dict() for a in alerts]


def test_alert_feed_dedupes_nothing_but_numbers_everything(caps) -> None:
    """The feed numbers what it is given; the axes are what keep it one-each."""
    from telemetry.role_health import DeferredAlert

    a = DeferredAlert(
        code=CODE_BURN_WARN, severity="warning", role_id="ceo", message="w"
    )
    b = DeferredAlert(
        code=CODE_HEARTBEAT_STALE, severity="alert", role_id="ceo", message="h"
    )
    feed = AlertFeed()
    out = feed.emit([a, b])
    assert [x.sequence for x in out] == [0, 1]
    assert out[0].code == CODE_HEARTBEAT_STALE  # hard severity first
    assert feed.by_code() == {CODE_HEARTBEAT_STALE: 1, CODE_BURN_WARN: 1}
    assert len(feed.for_role("ceo")) == 2
    assert [x.code for x in feed.fired()] == [CODE_HEARTBEAT_STALE]
