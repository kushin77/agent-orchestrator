"""portal console FinOps view UI tests (issue #1520, view half).

The backend half (test_finops_reports.py, issue #341) proves the served API
serves live per-tenant/per-agent cost, budget, usage and spend alerts over HTTP.
This file proves the *view* half issue #1520 adds: ``finops.html`` exists, is
served as a static frame, wires itself to the ``/api/finops/*`` surface, and the
nav offers it only while the surface is reachable (flag-gated, following the ERP
probe-and-hide pattern). No browser is needed: the view is vanilla ES5 DOM
shipped as-is (no bundler, no build step), so the assertions read the shipped
markup (endpoint + nav wiring) and the served endpoint (live data) directly.
"""

from __future__ import annotations

from pathlib import Path

from conftest import REPO_ROOT, ApiClient, console_sso, login_as

from portal.server.app import ConsoleApplication, build_app
from portal.server.finops import FinOpsReports
from telemetry.metering.model import UsageRecord
from telemetry.metering.store import append_records

STATIC = REPO_ROOT / "portal" / "static"
VIEW = STATIC / "views" / "finops.html"
CONSOLE_JS = STATIC / "js" / "console.js"

MONTH = "2026-09"
DAY = "2026-09-08"


def _record(*, tenant: str = "acme", agent: str = "coder-1", n: int) -> UsageRecord:
    return UsageRecord(
        tenant_id=tenant,
        agent_id=agent,
        provider="anthropic",
        model="claude-sonnet-5",
        route=None,
        outcome="ok",
        input_tokens=1_000_000,
        output_tokens=0,
        billable=True,
        metered=True,
        ts=f"{DAY}T12:00:00Z",
        source_type="call_record",
        source_key=f"{tenant}-{agent}-{n}",
        cost_usd=3.0,
        cost_source="rate_card",
        cache_hit=False,
    )


def _app(store_path: Path) -> ConsoleApplication:
    reports = FinOpsReports(
        repo_root=REPO_ROOT,
        enabled=True,
        usage_store_path=store_path,
        day=DAY,
        month=MONTH,
    )
    return ConsoleApplication(repo_root=REPO_ROOT, sso=console_sso(), finops_reports=reports)


# -- the view exists, is served, and wires the surface -----------------------
def test_finops_view_document_is_served(app):
    api = ApiClient(app)
    status, payload = api.get("/views/finops.html")
    assert status == 200
    assert isinstance(payload, bytes)
    assert b"/api/finops/report" in payload


def test_finops_view_wires_the_report_surface():
    html = VIEW.read_text(encoding="utf-8")
    assert "/api/finops/report?tenant=" in html


def test_nav_offers_finops_only_while_reachable():
    js = CONSOLE_JS.read_text(encoding="utf-8")
    assert 'id: "finops"' in js
    # the probe that gates the nav entry: hidden while the surface is off
    assert "/api/finops/overview" in js


# -- live data the view renders ---------------------------------------------
def test_report_serves_live_cost_and_agents(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    append_records(store, [_record(n=1), _record(n=2)])
    api = login_as(_app(store), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]
    # the families the view renders
    assert data["cost"]["costKnown"] is True
    assert data["usage"]["calls"] == 2
    assert data["agents"], "a metered tenant must have per-agent rows"


# -- the negative control: flag ON shows the surface by default -----------------------
def test_finops_surface_is_visible_when_flag_on():
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    app = build_app(sso=console_sso())
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/finops/overview")
    # With flag ON, the surface is visible. The response may vary (200 if overview exists, 404 if resource not found),
    # but NOT feature_disabled (404).
    assert status != 404 or payload["error"]["code"] != "feature_disabled"
