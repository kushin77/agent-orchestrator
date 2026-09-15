"""telemetry/role_health — heartbeat staleness per declared schedule (#637).

The acceptance criterion this file exists for: *a role that misses its schedule
raises exactly ONE named alert*.  "Exactly one" is asserted as a count of the
alerts naming that role, and "named" is asserted as the alert's code — so a
second alert for the same missed schedule, or an alert on a different code,
fails the test rather than passing as extra signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telemetry.role_health import (
    CADENCE_SECONDS,
    CODE_BURN_BREACH,
    CODE_HEARTBEAT_STALE,
    CODE_HEARTBEAT_UNKNOWN,
    STATUS_OK,
    STATUS_STALE,
    STATUS_UNKNOWN,
    AlertFeed,
    DeferredAlert,
    Heartbeat,
    HeartbeatStatus,
    RoleHeartbeatMonitor,
    heartbeats_from_sessions,
    load_role_caps,
)

NOW = 1_800_000_000.0


# --------------------------------------------------------------------------- #
# 1. The verdict against each declared cadence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("schedule", sorted(CADENCE_SECONDS))
def test_a_beat_inside_its_cadence_is_ok(schedule: str) -> None:
    cadence = CADENCE_SECONDS[schedule]
    beat = Heartbeat.at("ceo", NOW - (cadence - 1.0))
    status = HeartbeatStatus.evaluate("ceo", schedule, beat, now_epoch=NOW)
    assert status.status == STATUS_OK
    assert status.ok is True
    assert status.threshold_seconds == cadence
    assert status.alert() is None


@pytest.mark.parametrize("schedule", sorted(CADENCE_SECONDS))
def test_a_beat_past_its_cadence_is_stale(schedule: str) -> None:
    cadence = CADENCE_SECONDS[schedule]
    beat = Heartbeat.at("ceo", NOW - (cadence + 1.0))
    status = HeartbeatStatus.evaluate("ceo", schedule, beat, now_epoch=NOW)
    assert status.status == STATUS_STALE
    assert status.ok is False
    assert status.overdue_seconds == pytest.approx(1.0, abs=1e-6)
    alert = status.alert()
    assert alert is not None and alert.code == CODE_HEARTBEAT_STALE


def test_the_boundary_is_exactly_one_cadence() -> None:
    """age == threshold is still OK; one second more is stale (inclusive OK)."""
    beat = Heartbeat.at("coo", NOW - 900.0)
    on_time = HeartbeatStatus.evaluate("coo", "every-15m", beat, now_epoch=NOW)
    assert on_time.status == STATUS_OK
    late = HeartbeatStatus.evaluate(
        "coo", "every-15m", Heartbeat.at("coo", NOW - 901.0), now_epoch=NOW
    )
    assert late.status == STATUS_STALE


def test_a_daily_role_is_not_stale_at_23_hours() -> None:
    """The cadence that separates 'hourly' from 'daily' is real arithmetic."""
    beat = Heartbeat.at("cfo", NOW - 82_800.0)  # 23h
    status = HeartbeatStatus.evaluate("cfo", "daily", beat, now_epoch=NOW)
    assert status.status == STATUS_OK
    assert status.threshold_seconds == 86_400.0


def test_a_role_that_never_beat_and_declares_a_cadence_is_stale() -> None:
    """No beat for a declared cadence missed every schedule so far."""
    status = HeartbeatStatus.evaluate("ceo", "hourly", None, now_epoch=NOW)
    assert status.status == STATUS_STALE
    assert status.last_beat is None
    assert status.alert() is not None


# --------------------------------------------------------------------------- #
# 2. Fail-closed: an unreadable schedule is unknown, never healthy
# --------------------------------------------------------------------------- #
def test_missing_schedule_is_unknown_not_ok() -> None:
    status = HeartbeatStatus.evaluate("ghost", None, None, now_epoch=NOW)
    assert status.status == STATUS_UNKNOWN
    assert status.ok is False
    assert status.alert() is not None
    assert status.alert().code == CODE_HEARTBEAT_UNKNOWN


def test_event_cadence_is_unknown_not_ok() -> None:
    """An event-driven role makes no wall-clock promise, so it cannot be 'ok'."""
    status = HeartbeatStatus.evaluate("ghost", "event", None, now_epoch=NOW)
    assert status.status == STATUS_UNKNOWN
    assert status.reason == "schedule 'event' is event-driven (no wall-clock promise)"


def test_non_platform_schedule_is_unknown_and_says_so() -> None:
    status = HeartbeatStatus.evaluate("ghost", "every-7m", None, now_epoch=NOW)
    assert status.status == STATUS_UNKNOWN
    assert status.threshold_seconds is None
    assert "not a platform cadence" in status.reason


# --------------------------------------------------------------------------- #
# 3. THE acceptance criterion: exactly one named alert per missed schedule
# --------------------------------------------------------------------------- #
def test_a_role_that_misses_its_schedule_raises_exactly_one_named_alert() -> None:
    """One missed schedule -> exactly one alert, and it names the role + fact."""
    caps = load_role_caps()
    # cto declares `every-30m`; its last beat is two hours old.
    beats = {"cto": Heartbeat.at("cto", NOW - 7200.0)}
    monitor = RoleHeartbeatMonitor(caps, beats, now_epoch=NOW)
    feed = AlertFeed()
    alerts = feed.emit(monitor.alerts())

    for_cto = [a for a in alerts if a.role_id == "cto"]
    assert len(for_cto) == 1, f"expected exactly one alert for cto, got {for_cto}"
    alert = for_cto[0]
    assert alert.code == CODE_HEARTBEAT_STALE
    assert alert.severity == "alert"
    assert "every-30m" in alert.message
    assert alert.detail["schedule"] == "every-30m"
    assert alert.detail["thresholdSeconds"] == 1800.0
    assert alert.detail["ageSeconds"] == pytest.approx(7200.0, abs=1e-6)
    assert alert.detail["overdueSeconds"] == pytest.approx(5400.0, abs=1e-6)
    # every alert in the feed is the same *fact* (a missed cadence) — the feed
    # carries no second, different-coded alert for the same miss.
    assert {a.code for a in alerts} == {CODE_HEARTBEAT_STALE}
    # and one alert per role, never a cross-product
    assert len({a.role_id for a in alerts}) == len(alerts)


def test_one_alert_per_missed_role_even_when_several_roles_are_late() -> None:
    """Each late role gets exactly one alert; a met role gets none.

    ``cfo`` declares ``daily`` and beat 1h ago, so it is healthy; ``ceo``
    (hourly, 2h old), ``coo`` (every-15m, ~32m old), ``cmo`` (hourly, no beat
    ever) and ``cto`` (every-30m, no beat ever) each missed their declared
    schedule.  A beat for a role that is not declared (``cot``) is ignored —
    the declaration defines the universe.
    """
    caps = load_role_caps()
    beats = {
        "ceo": Heartbeat.at("ceo", NOW - 7200.0),   # hourly, 2h old -> stale
        "cot": Heartbeat.at("cot", NOW),            # not a declared role
        "coo": Heartbeat.at("coo", NOW - 1900.0),   # every-15m -> stale
        "cfo": Heartbeat.at("cfo", NOW - 3600.0),   # daily -> ok
    }
    monitor = RoleHeartbeatMonitor(caps, beats, now_epoch=NOW)
    alerts = monitor.alerts()
    by_role = {}
    for alert in alerts:
        by_role.setdefault(alert.role_id, []).append(alert)
    assert sorted(by_role) == ["ceo", "cmo", "coo", "cto"]
    assert all(len(v) == 1 for v in by_role.values())
    assert "cfo" not in by_role  # met its daily cadence
    assert "cot" not in by_role  # not a declared role
    assert {a.code for a in alerts} == {CODE_HEARTBEAT_STALE}


def test_a_role_that_meets_its_schedule_raises_no_alert() -> None:
    caps = load_role_caps()
    beats = {
        "ceo": Heartbeat.at("ceo", NOW - 600.0),
        "cto": Heartbeat.at("cto", NOW - 600.0),
        "coo": Heartbeat.at("coo", NOW - 600.0),
        "cfo": Heartbeat.at("cfo", NOW - 600.0),
        "cmo": Heartbeat.at("cmo", NOW - 600.0),
    }
    monitor = RoleHeartbeatMonitor(caps, beats, now_epoch=NOW)
    assert monitor.alerts() == []
    assert all(s.status == STATUS_OK for s in monitor.statuses())


def test_a_refreshed_beat_clears_the_alert() -> None:
    """The alert is a function of the beat age, so accepting a beat clears it."""
    caps = load_role_caps()
    monitor = RoleHeartbeatMonitor(
        caps, {"cto": Heartbeat.at("cto", NOW - 7200.0)}, now_epoch=NOW
    )
    assert "cto" in {a.role_id for a in monitor.alerts()}
    assert monitor.by_role()["cto"].status == STATUS_STALE
    monitor.accept(Heartbeat.at("cto", NOW - 10.0))
    assert "cto" not in {a.role_id for a in monitor.alerts()}
    assert monitor.by_role()["cto"].status == STATUS_OK


def test_monitor_covers_every_declared_role(caps) -> None:
    """With no beats at all, every declared role missed its schedule."""
    monitor = RoleHeartbeatMonitor(caps, {}, now_epoch=NOW)
    assert [s.role_id for s in monitor.statuses()] == caps.role_ids()
    assert len(monitor.alerts()) == len(caps.role_ids()) == 5
    assert all(s.status == STATUS_STALE for s in monitor.statuses())


# --------------------------------------------------------------------------- #
# 4. Consuming the fleet's own heartbeat files
# --------------------------------------------------------------------------- #
def test_session_heartbeat_files_are_consumed_not_reimplemented(tmp_path: Path) -> None:
    (tmp_path / "s1.json").write_text(
        json.dumps(
            {
                "session_id": "s1",
                "issue": 637,
                "agent": "cto",
                "lane": "wb-telemetry",
                "at": NOW - 120.0,
                "at_iso": "2027-01-15T07:58:00Z",
            }
        ),
        encoding="utf-8",
    )
    beats = heartbeats_from_sessions([tmp_path])
    assert set(beats) == {"cto"}
    assert beats["cto"].at_epoch == pytest.approx(NOW - 120.0)


def test_the_newest_beat_wins_and_unreadable_files_are_skipped(
    tmp_path: Path,
) -> None:
    (tmp_path / "old.json").write_text(
        json.dumps({"agent": "cto", "at": NOW - 9000.0}), encoding="utf-8"
    )
    (tmp_path / "new.json").write_text(
        json.dumps({"agent": "cto", "at": NOW - 30.0}), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "undated.json").write_text(
        json.dumps({"agent": "ceo"}), encoding="utf-8"
    )
    (tmp_path / "no-role.json").write_text(
        json.dumps({"at": NOW}), encoding="utf-8"
    )
    beats = heartbeats_from_sessions([tmp_path])
    assert set(beats) == {"cto"}
    assert beats["cto"].at_epoch == pytest.approx(NOW - 30.0)


def test_heartbeat_parse_refuses_an_undatable_value() -> None:
    with pytest.raises(ValueError):
        Heartbeat.parse("ceo", "not-a-timestamp")
    with pytest.raises(ValueError):
        Heartbeat.parse("ceo", "2026-12-01T10:00:00")  # no timezone


def test_alert_feed_refuses_an_undeclared_code() -> None:
    with pytest.raises(ValueError, match="unknown alert code"):
        DeferredAlert(code="heartbeat.whatever", severity="alert", role_id="ceo", message="x")


def test_alert_feed_refuses_a_severity_the_code_does_not_carry() -> None:
    with pytest.raises(ValueError, match="carries severity"):
        DeferredAlert(
            code=CODE_BURN_BREACH,
            severity="warning",
            role_id="ceo",
            message="x",
        )
