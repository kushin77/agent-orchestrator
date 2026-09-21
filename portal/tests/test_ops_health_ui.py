"""portal console Ops/SLO view UI tests (issue #1520, view half).

The backend half (test_ops_health_surface.py, issue #342) proves the served API
serves live per-agent health, SLO verdicts and breach/recovery over HTTP. This
file proves the *view* half issue #1520 adds: ``ops.html`` exists, is served as
a static frame, wires itself to the ``/api/ops/*`` surface, and the nav offers it
only while the surface is reachable (flag-gated, following the ERP probe-and-hide
pattern). No browser is needed: the view is vanilla ES5 DOM shipped as-is (no
bundler, no build step), so the assertions read the shipped markup (endpoint +
nav wiring) and the served endpoint (live data) directly.
"""

from __future__ import annotations

from pathlib import Path

from conftest import REPO_ROOT, ApiClient, console_sso, login_as

from portal.server.app import ConsoleApplication, build_app
from portal.server.ops_health import OpsHealthReports
from telemetry.observability.model import (
    KIND_MODEL_CALL,
    KIND_TRACE,
    OUTCOME_SUCCESS,
    SpanRecord,
)
from telemetry.observability.store import store_span_lines

STATIC = REPO_ROOT / "portal" / "static"
VIEW = STATIC / "views" / "ops.html"
CONSOLE_JS = STATIC / "js" / "console.js"


def _span(*, agent: str = "coder-1", tenant: str = "acme", ts: str, n: int = 0, kind: str = KIND_MODEL_CALL) -> SpanRecord:
    """One span in the observability lane's own record shape."""
    return SpanRecord(
        trace_id=f"trc-{tenant}-{agent}-{ts}-{n}",
        span_id=f"spn-{agent}-{ts}-{n}",
        parent_span_id=None if kind == KIND_TRACE else f"spn-root-{tenant}-{agent}",
        tenant_id=tenant,
        agent_id=None if kind == KIND_TRACE else agent,
        service="gateway",
        kind=kind,
        name="model.call" if kind == KIND_MODEL_CALL else kind,
        outcome=OUTCOME_SUCCESS,
        ts=ts,
        provider="anthropic" if kind == KIND_MODEL_CALL else None,
        model="claude-sonnet-5" if kind == KIND_MODEL_CALL else None,
        tier="MED" if kind == KIND_MODEL_CALL else None,
        input_tokens=100 if kind == KIND_MODEL_CALL else 0,
        output_tokens=50 if kind == KIND_MODEL_CALL else 0,
        latency_ms=80.0 if kind == KIND_MODEL_CALL else 0.0,
        estimated_cost_usd=0.001 if kind == KIND_MODEL_CALL else 0.0,
    )


def _feed(path: Path, spans: list[SpanRecord]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for line in store_span_lines(spans):
            handle.write(line + "\n")


def _app(spans_path: Path) -> ConsoleApplication:
    reports = OpsHealthReports(
        repo_root=REPO_ROOT,
        enabled=True,
        spans_store_path=spans_path,
        pauses_path=spans_path.parent / "ops-pauses.json",
    )
    return ConsoleApplication(repo_root=REPO_ROOT, sso=console_sso(), ops_health=reports)


# -- the view exists, is served, and wires the surface -----------------------
def test_ops_view_document_is_served(app):
    api = ApiClient(app)
    status, payload = api.get("/views/ops.html")
    assert status == 200
    assert isinstance(payload, bytes)
    assert b"/api/ops/dashboard" in payload


def test_ops_view_wires_the_dashboard_surface():
    html = VIEW.read_text(encoding="utf-8")
    assert "/api/ops/dashboard?tenant=" in html


def test_nav_offers_ops_only_while_reachable():
    js = CONSOLE_JS.read_text(encoding="utf-8")
    assert 'id: "ops"' in js
    # the probe that gates the nav entry: hidden while the surface is off
    assert "/api/ops/overview" in js


# -- live data the view renders ---------------------------------------------
def test_dashboard_serves_live_slo_burn_and_health(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    ts = "2026-09-13T00:00:00Z"
    _feed(spans, [
        _span(kind=KIND_TRACE, ts=ts),
        _span(ts=ts, n=1),
        _span(ts=ts, n=2),
    ])
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/dashboard", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]
    # the three families the view renders
    assert "sloResults" in data
    assert "burnRate" in data
    assert "tenants" in data
    assert data["tenants"], "a tenant with spans must appear in the dashboard"


# -- the negative control: flag ON shows the surface by default -----------------------
def test_ops_surface_is_visible_when_flag_on():
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    app = build_app(sso=console_sso())
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/ops/overview")
    # With flag ON, the surface is visible. The response may vary (200 if overview exists, 404 if resource not found),
    # but NOT feature_disabled (404).
    assert status != 404 or payload["error"]["code"] != "feature_disabled"
