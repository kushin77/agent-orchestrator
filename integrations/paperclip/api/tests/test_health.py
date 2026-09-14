"""``/api/health`` reads real dependencies and never reports a green lie (#413)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from integrations.paperclip.api import health

#: A probe that reports its dependency missing without touching the tree.
def _absent(name: str):
    def probe(_root: Path, _now) -> health.DependencyState:
        return health.DependencyState(name, health.STATE_MISSING, "provoked absent")

    return probe


def test_health_is_ok_when_every_dependency_is_ok(tree: Path, now: datetime) -> None:
    report = health.health(tree, now=now)
    assert report.status == health.STATUS_OK
    assert report.http_status == 200
    assert {d.name for d in report.dependencies} == {"claim_ledger", "ticket_projection"}
    assert all(d.state == health.STATE_OK for d in report.dependencies)


def test_health_is_unhealthy_503_when_the_ledger_is_absent(tree: Path, now: datetime) -> None:
    (tree / ".board" / "claims.jsonl").unlink()
    report = health.health(tree, now=now)
    assert report.status == health.STATUS_UNHEALTHY
    assert report.http_status == 503
    ledger = next(d for d in report.dependencies if d.name == "claim_ledger")
    assert ledger.state == health.STATE_MISSING


def test_health_is_degraded_when_the_projection_is_stale(tree: Path, now: datetime) -> None:
    stale = now - timedelta(minutes=health.SNAPSHOT_STALENESS_MINUTES + 5)
    (tree / ".board" / "snapshot.json").write_text(
        json.dumps({"generated_at": stale.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8"
    )
    report = health.health(tree, now=now)
    assert report.status == health.STATUS_DEGRADED
    assert report.http_status == 200
    projection = next(d for d in report.dependencies if d.name == "ticket_projection")
    assert projection.state == health.STATE_STALE


def test_health_is_unhealthy_when_the_projection_is_unreadable(tree: Path, now: datetime) -> None:
    (tree / ".board" / "snapshot.json").write_text("{ not json", encoding="utf-8")
    report = health.health(tree, now=now)
    assert report.status == health.STATUS_UNHEALTHY
    assert report.http_status == 503


def test_health_is_unhealthy_when_a_probe_reports_a_dependency_missing(
    tree: Path, now: datetime
) -> None:
    report = health.health(tree, now=now, probes=(_absent("claim_ledger"),))
    assert report.status == health.STATUS_UNHEALTHY
    assert report.http_status == 503


def test_check_report_refuses_a_green_lie_by_name(tree: Path, now: datetime) -> None:
    lying = health.HealthReport(
        status=health.STATUS_OK,
        http_status=200,
        dependencies=(
            health.DependencyState("claim_ledger", health.STATE_OK, "claimed ok"),
            health.DependencyState("ticket_projection", health.STATE_OK, "claimed ok"),
        ),
    )
    probes = (_absent("claim_ledger"), _absent("ticket_projection"))
    findings = health.check_report(lying, tree, now=now, probes=probes)
    assert any("'claim_ledger'" in f and "ok while" in f for f in findings)
    assert any("overall ok" in f for f in findings)


def test_check_report_is_clean_for_an_honest_report(tree: Path, now: datetime) -> None:
    honest = health.health(tree, now=now)
    assert health.check_report(honest, tree, now=now) == []


def test_report_serializes_deterministically(tree: Path, now: datetime) -> None:
    report = health.health(tree, now=now)
    assert set(report.to_dict()) == {"status", "dependencies"}
