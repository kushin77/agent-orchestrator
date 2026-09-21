"""telemetry/role_health — per-role budget burn rollup + alerting (#637).

---knowledge---
module_id: telemetry.role_health_tests.test_burn_rollup
system: telemetry
app: role_health_tests
solution_class: enterprise
patterns: [declared-not-restated, boundary-pinned-assertions]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: ["pytest suite over the per-role burn rollup and alerting"]
invariants: "the burn arithmetic is pinned against the declared cap, so a passing test cannot disagree with what FinOps enforces"
gotchas: ""
related: ["#637", "#1510"]
do_not_duplicate: null
---knowledge---


The burn arithmetic is pinned here against the *declared* cap, not against a
number restated in the test: the cap is read from the declaration the platform
consumes, so a test that passes cannot disagree with what FinOps enforces.
"""

from __future__ import annotations

import pytest

from conftest import MONTH, T_DEC_01, call_record

from telemetry.role_health import (
    CODE_BURN_BREACH,
    CODE_BURN_WARN,
    POSITION_BREACHED,
    POSITION_OK,
    POSITION_WARNING,
    BudgetBurn,
    RoleBudgetBurnReport,
    load_role_caps,
)


def _burn(cost: float, cap: float, *, warn: float = 100.0, metered: bool = True) -> BudgetBurn:
    return BudgetBurn(
        role_id="cto",
        month=MONTH,
        cost_usd=cost,
        monthly_cap_usd=cap,
        warn_at_pct=warn,
        has_metered_calls=metered,
    )


# --------------------------------------------------------------------------- #
# 1. The arithmetic is pinned
# --------------------------------------------------------------------------- #
def test_burn_pct_arithmetic_is_pinned() -> None:
    """burn_pct == cost / cap * 100, exactly."""
    assert _burn(50.0, 100.0).burn_pct == pytest.approx(50.0, abs=1e-12)
    assert _burn(125.0, 250.0).burn_pct == pytest.approx(50.0, abs=1e-12)
    assert _burn(0.0, 250.0).burn_pct == pytest.approx(0.0, abs=1e-12)
    assert _burn(250.0, 250.0).burn_pct == pytest.approx(100.0, abs=1e-12)


def test_zero_cap_has_no_meaningful_denominator() -> None:
    """A zero cap with spend is over it; a zero cap with none is within it."""
    assert _burn(0.0, 0.0).burn_pct == 0.0
    assert _burn(0.0, 0.0).position == POSITION_OK
    assert _burn(0.01, 0.0).burn_pct == 100.0
    assert _burn(0.01, 0.0).position == POSITION_BREACHED


def test_position_ladder_is_pinned_at_the_boundaries() -> None:
    """ok < warn <= warning < cap <= breached, with the boundaries inclusive."""
    # 79.999% of a 100 cap, warn at 80 -> ok
    assert _burn(79.999, 100.0, warn=80.0).position == POSITION_OK
    # exactly at warn -> warning
    assert _burn(80.0, 100.0, warn=80.0).position == POSITION_WARNING
    # just under the cap -> still warning
    assert _burn(99.999, 100.0, warn=80.0).position == POSITION_WARNING
    # exactly at the cap -> breached
    assert _burn(100.0, 100.0, warn=80.0).position == POSITION_BREACHED
    # over the cap -> breached
    assert _burn(100.001, 100.0, warn=80.0).position == POSITION_BREACHED


def test_position_without_metered_calls_is_ok_not_a_fabricated_breach() -> None:
    """No metered calls is 'no data' on a separate flag, never an invented pct."""
    row = _burn(0.0, 50.0, metered=False)
    assert row.has_metered_calls is False
    assert row.position == POSITION_OK
    assert row.burn_pct == 0.0
    assert row.alert() is None


def test_alert_is_absent_below_thresholds() -> None:
    assert _burn(10.0, 100.0, warn=80.0).alert() is None


def test_burn_warning_alert_names_the_role_and_the_code() -> None:
    alert = _burn(80.0, 100.0, warn=80.0).alert()
    assert alert is not None
    assert alert.code == CODE_BURN_WARN
    assert alert.role_id == "cto"
    assert alert.severity == "warning"
    assert alert.detail["burnPct"] == pytest.approx(80.0, abs=1e-6)
    assert alert.detail["monthlyCapUsd"] == 100.0


def test_burn_breach_alert_is_a_hard_severity() -> None:
    alert = _burn(300.0, 300.0).alert()
    assert alert is not None
    assert alert.code == CODE_BURN_BREACH
    assert alert.severity == "alert"
    assert alert.detail["position"] == POSITION_BREACHED


# --------------------------------------------------------------------------- #
# 2. The rollup reads the durable metering store, grouped by role
# --------------------------------------------------------------------------- #
def test_rollup_groups_the_metering_feed_by_role(metering_reporter, caps) -> None:
    ingest, reporter = metering_reporter
    # ceo: 0.50 in 2026-12. cto: 0.25. Everything else unmetered.
    ingest(call_record(agent="ceo", estimate=0.20))
    ingest(call_record(agent="ceo", estimate=0.30))
    ingest(call_record(agent="cto", estimate=0.25, tier="L1", task_class="design"))
    report = RoleBudgetBurnReport(reporter(), caps, month=MONTH)
    by_role = report.by_role()

    assert set(by_role) == {"ceo", "cto", "coo", "cfo", "cmo"}
    assert by_role["ceo"].cost_usd == pytest.approx(0.50, abs=1e-9)
    assert by_role["ceo"].calls == 2
    assert by_role["ceo"].monthly_cap_usd == 300.0  # declared, not restated
    assert by_role["ceo"].burn_pct == pytest.approx(0.50 / 300.0 * 100.0, abs=1e-9)
    assert by_role["cto"].cost_usd == pytest.approx(0.25, abs=1e-9)
    assert by_role["cto"].monthly_cap_usd == 250.0
    assert by_role["cto"].has_metered_calls is True


def test_rollup_reports_unmetered_roles_as_unmetered_not_zero(
    metering_reporter, caps
) -> None:
    """A role the feed never metered is flagged, so 'no data' stays visible."""
    ingest, reporter = metering_reporter
    ingest(call_record(agent="ceo", estimate=0.10))
    report = RoleBudgetBurnReport(reporter(), caps, month=MONTH)
    by_role = report.by_role()
    assert by_role["ceo"].has_metered_calls is True
    assert by_role["cfo"].has_metered_calls is False
    assert by_role["cfo"].cost_usd == 0.0
    totals = report.totals()
    assert totals["meteredRoles"] == 1
    assert totals["unmeteredRoles"] == 4
    assert totals["roles"] == 5
    assert totals["capUsd"] == pytest.approx(300.0 + 250.0 + 100.0 + 50.0 + 200.0)


def test_rollup_is_bounded_to_the_month_bucket(metering_reporter, caps) -> None:
    """A call in another month never inflates this month's burn."""
    ingest, reporter = metering_reporter
    ingest(call_record(agent="ceo", estimate=0.40, ts=T_DEC_01))
    ingest(call_record(agent="ceo", estimate=9.99, ts="2026-11-30T23:59:59Z"))
    report = RoleBudgetBurnReport(reporter(), caps, month=MONTH)
    assert report.by_role()["ceo"].cost_usd == pytest.approx(0.40, abs=1e-9)
    assert report.by_role()["ceo"].calls == 1


def test_rollup_raises_burn_alerts_for_the_roles_over_threshold(
    metering_reporter, caps
) -> None:
    """Breaching the cfo cap ($50, daily beat) raises exactly one breach alert."""
    ingest, reporter = metering_reporter
    ingest(call_record(agent="cfo", estimate=50.0, task_class="finops", tier="L0"))
    ingest(call_record(agent="ceo", estimate=0.01))
    report = RoleBudgetBurnReport(reporter(), caps, month=MONTH)
    alerts = report.alerts()
    assert len(alerts) == 1
    assert alerts[0].code == CODE_BURN_BREACH
    assert alerts[0].role_id == "cfo"
    assert report.totals()["breaches"] == 1
    assert report.totals()["warns"] == 0


def test_rollup_warn_threshold_is_the_declared_one(metering_reporter) -> None:
    """With a warn threshold of 80%, 80% of the cap warns rather than breaches."""
    ingest, reporter = metering_reporter
    ingest(call_record(agent="cfo", estimate=40.0, task_class="finops", tier="L0"))
    caps = load_role_caps()
    object.__setattr__(caps, "warn_at_pct", 80.0)  # consumed value, overridden here
    report = RoleBudgetBurnReport(reporter(), caps, month=MONTH)
    alerts = report.alerts()
    assert len(alerts) == 1
    assert alerts[0].code == CODE_BURN_WARN
    assert alerts[0].role_id == "cfo"
    assert alerts[0].detail["burnPct"] == pytest.approx(80.0, abs=1e-6)


def test_burn_row_serializes_every_figure(caps) -> None:
    row = _burn(125.0, 250.0)
    payload = row.to_dict()
    assert payload == {
        "roleId": "cto",
        "month": MONTH,
        "costUsd": 125.0,
        "calls": 0,
        "tokens": 0,
        "monthlyCapUsd": 250.0,
        "warnAtPct": 100.0,
        "burnPct": 50.0,
        "position": POSITION_OK,
        "hasMeteredCalls": True,
    }
