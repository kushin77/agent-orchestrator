"""portal ops/health/SLO surface tests (issue #342, server half).

Proves the acceptance criterion the server half can prove end to end, against
the served API:

* live per-agent health, SLO verdicts and the latency/error-rate/token figures
  are served from the real span feed (no demo rows);
* **an SLO breach flips the agent to ALERT, and a recovery flips it back to OK**
  — both directions, through the served API, over a feed that grows between the
  reads (the pane is live, not a snapshot);
* the honesty states are reachable and honest over HTTP: ``NO_DATA`` when
  nothing was measured (never ``OK``), ``PAUSED`` from the operator pause file
  (never hiding the breach underneath it);
* a corrupt feed fails closed instead of reporting health;
* the surface is feature-flag-gated OFF until promoted, and scoped by RBAC.

The feed is written with the observability lane's own serializer
(``store_span_lines``), so the suite fails if the portal ever diverges from the
real span-record shape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server.app import ConsoleApplication, build_app
from portal.server.ops_health import OpsHealthReports
from telemetry.observability.model import (
    KIND_GUARD,
    KIND_MODEL_CALL,
    KIND_TRACE,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    SpanRecord,
)
from telemetry.observability.store import store_span_lines

#: Fixed instants, hours apart, so every SLO window is deterministic: the
#: breach window straddles the healthy one, the recovery window clears both.
BASE = "2026-09-13T00:00:00Z"
BREACH_AT = "2026-09-13T00:30:00Z"
RECOVERED = "2026-09-13T02:00:00Z"

AGENT = "coder-1"
OTHER_AGENT = "reviewer-1"


def _iso(ts: str, seconds: int) -> str:
    """``ts`` shifted by ``seconds`` (the pane's timestamps are second-granular)."""
    moment = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (moment + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _span(
    *,
    agent: str = AGENT,
    outcome: str = OUTCOME_SUCCESS,
    ts: str = BASE,
    tenant: str = "acme",
    kind: str = KIND_MODEL_CALL,
    n: int = 0,
    latency_ms: float = 80.0,
) -> SpanRecord:
    """One span in the observability lane's own record shape.

    A trace root carries no tokens, latency or cost — it is a correlation
    anchor, not a call — so the usage figures below can only come from calls.
    """
    is_call = kind == KIND_MODEL_CALL
    return SpanRecord(
        trace_id=f"trc-{tenant}-{agent}-{ts}-{n}",
        span_id=f"spn-{agent}-{ts}-{n}",
        parent_span_id=None if kind == KIND_TRACE else f"spn-root-{tenant}-{agent}",
        tenant_id=tenant,
        agent_id=None if kind == KIND_TRACE else agent,
        service="gateway",
        kind=kind,
        name="model.call" if is_call else kind,
        outcome=outcome,
        ts=ts,
        provider="anthropic" if is_call else None,
        model="claude-sonnet-4-5" if is_call else None,
        tier="MED" if is_call else None,
        input_tokens=100 if is_call else 0,
        output_tokens=50 if is_call else 0,
        latency_ms=latency_ms if is_call else 0.0,
        estimated_cost_usd=0.001 if is_call else 0.0,
    )


def _feed(path: Path, spans: list[SpanRecord]) -> None:
    """Append spans to the feed in the lane's serialized shape."""
    with path.open("a", encoding="utf-8") as handle:
        for line in store_span_lines(spans):
            handle.write(line + "\n")


def _healthy(
    path: Path,
    *,
    tenant: str = "acme",
    agent: str = AGENT,
    n: int = 10,
    ts: str = BASE,
    failures: int = 0,
) -> None:
    """One trace root plus ``n`` serve attempts, the first ``failures`` failing."""
    _feed(
        path,
        [_span(kind=KIND_TRACE, ts=ts, tenant=tenant, agent=agent)]
        + [
            _span(
                tenant=tenant,
                agent=agent,
                ts=_iso(ts, i + 1),
                n=i,
                outcome=OUTCOME_FAILED if i < failures else OUTCOME_SUCCESS,
            )
            for i in range(n)
        ],
    )


def _app(spans_path: Path, **kwargs) -> ConsoleApplication:
    """A console whose ops surface reads ``spans_path`` and nothing else."""
    reports = OpsHealthReports(
        repo_root=REPO_ROOT,
        enabled=True,
        spans_store_path=spans_path,
        pauses_path=spans_path.parent / "ops-pauses.json",
        **kwargs,
    )
    return ConsoleApplication(
        repo_root=REPO_ROOT, sso=console_sso(), ops_health=reports
    )


def _agents(payload: dict) -> dict:
    return {row["agentId"]: row for row in payload["data"]["agents"]}


# --------------------------------------------------------------------------- #
# The flag gate (GR-5: a new surface ships OFF)
# --------------------------------------------------------------------------- #
def test_registry_declares_the_surface_off():
    registry = yaml.safe_load(
        (REPO_ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = registry["surfaces"]["ops_health"]
    # PyYAML reads the bare YAML 1.1 scalar `off` as boolean False — both
    # spellings mean OFF (same acceptance as scripts/check-feature-flags.py).
    assert entry["default"] in (False, "off")
    assert entry["promoted"] is False


def test_surface_is_refused_while_the_flag_is_off():
    """The default app (registry decides) refuses the whole family — before authN."""
    app = build_app(sso=console_sso())
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/ops/overview")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    # an *unauthenticated* probe sees the same 404: the surface is invisible,
    # not merely protected
    anonymous = login_as(app, "nobody@example.com", "acme")
    anonymous.cookies.clear()
    status, _ = anonymous.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 404


# --------------------------------------------------------------------------- #
# Live health / SLO / breach state, served per agent
# --------------------------------------------------------------------------- #
def test_live_agent_health_and_figures_are_served(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=10)
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    assert payload["data"]["referenceTs"] == "2026-09-13T00:00:10Z"
    agents = _agents(payload)
    assert set(agents) == {AGENT}
    row = agents[AGENT]

    # health is measured, not assumed
    assert row["state"]["state"] == "OK"
    assert row["state"]["severity"] == "ok"
    assert row["state"]["subject"] == f"acme/{AGENT}"
    assert row["verdicts"] == {
        "availability-requests:acme": "OK",
        "latency-p95:acme": "OK",
        "cost-budget:acme": "OK",
    }
    # the figures: window, calls, attempts, error rate, percentiles, tokens
    assert row["windowSeconds"] == 3600
    # every span in the row belongs to this agent: the trace root, which names
    # no agent, is counted at tenant level (below) rather than invented here
    assert row["spans"] == 10
    assert row["calls"] == 10
    assert row["attempts"] == 10
    assert row["served"] == 10
    assert row["failed"] == 0
    assert row["errorRatePct"] == 0.0
    assert row["latencyMs"] == {"samples": 10, "p50": 80.0, "p95": 80.0, "max": 80.0}
    assert row["tokens"] == {"input": 1000, "output": 500, "total": 1500}
    assert row["costUsd"] == pytest.approx(0.01)
    assert row["lastSpanAt"] == "2026-09-13T00:00:10Z"

    # the state vocabulary ships with the payload so a client cannot guess
    assert set(payload["data"]["states"]) == {
        "OK", "WARNING", "ALERT", "NO_DATA", "PAUSED"
    }


def test_overview_serves_tenant_state_and_totals(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=10)
    _healthy(spans, agent=OTHER_AGENT, n=4, failures=2)
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/overview")
    assert status == 200
    data = payload["data"]
    tenant = data["tenants"][0]
    assert tenant["tenantId"] == "acme"
    # 14 attempts, 12 served: below the 0.95 target, so the tenant is ALERT too
    assert tenant["attempts"] == 14
    assert tenant["failed"] == 2
    # the tenant row counts every span, trace roots included: nothing is lost
    # between the agent rows and the tenant's own figures
    assert tenant["spans"] == 16
    assert tenant["calls"] == 14
    assert tenant["verdicts"]["availability-requests:acme"] == "BREACHED"
    assert tenant["state"]["state"] == "ALERT"
    assert tenant["agents"]["total"] == 2
    assert tenant["agents"]["byState"]["OK"] == 1
    assert tenant["agents"]["byState"]["ALERT"] == 1
    assert data["totals"]["attention"] == 1

    rows = _agents(payload)
    assert rows[AGENT]["state"]["state"] == "OK"
    assert rows[OTHER_AGENT]["state"]["state"] == "ALERT"
    assert rows[OTHER_AGENT]["attempts"] == 4
    assert rows[OTHER_AGENT]["failed"] == 2
    assert rows[OTHER_AGENT]["errorRatePct"] == 50.0


# --------------------------------------------------------------------------- #
# The acceptance criterion: breach -> ALERT, recovery -> OK, over the API
# --------------------------------------------------------------------------- #
def test_slo_breach_flips_the_agent_to_alert_and_recovery_flips_it_back(
    tmp_path: Path,
):
    spans = tmp_path / "spans.jsonl"
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    # 1. a healthy window: the agent is OK
    _healthy(spans, n=10)
    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    assert _agents(payload)[AGENT]["state"]["state"] == "OK"

    # 2. the next window breaches: 6 of 10 serves fail (ratio 0.7 < 0.95)
    _healthy(spans, n=10, ts=BREACH_AT, failures=6)
    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    breach = _agents(payload)[AGENT]
    assert breach["state"]["state"] == "ALERT"
    assert breach["state"]["severity"] == "critical"
    assert breach["state"]["breached"] == ["availability-requests:acme"]
    assert breach["verdicts"]["availability-requests:acme"] == "BREACHED"
    assert breach["attempts"] == 20          # both batches are inside the window
    assert breach["failed"] == 6
    assert breach["errorRatePct"] == 30.0

    # ... and the tenant itself is ALERT, with the lane's own alarm fired
    status, payload = api.get("/api/ops/overview")
    assert payload["data"]["tenants"][0]["state"]["state"] == "ALERT"
    assert payload["data"]["totals"]["attention"] == 1
    assert [
        (alert["slo"], alert["reason"], alert["severity"])
        for alert in payload["data"]["firedAlerts"]
    ] == [("availability-requests:acme", "slo_target_breached", "critical")]

    # 3. recovery: a later window of good serves brings it back to OK
    _healthy(spans, n=10, ts=RECOVERED)
    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    recovered = _agents(payload)[AGENT]
    assert recovered["state"]["state"] == "OK"
    assert recovered["state"]["previousState"] == "ALERT"
    assert recovered["state"]["transitions"] == 3
    assert recovered["attempts"] == 10       # the breach window is behind us
    assert recovered["errorRatePct"] == 0.0

    # the recovery is on the record, in both directions
    status, payload = api.get("/api/ops/alerts")
    assert status == 200
    history = payload["data"]["history"][f"acme/{AGENT}"]
    assert [
        (move["fromState"], move["toState"], move["reason"]) for move in history
    ] == [
        (None, "OK", "first_observation"),
        ("OK", "ALERT", "slo_target_breached"),
        ("ALERT", "OK", "recovered"),
    ]
    # and nothing is firing any more
    assert payload["data"]["fired"] == []


# --------------------------------------------------------------------------- #
# The honesty states over HTTP
# --------------------------------------------------------------------------- #
def test_an_absent_feed_is_stated_not_read_as_health(tmp_path: Path):
    """No feed at all: nothing to report, and the pane says exactly that."""
    spans = tmp_path / "spans.jsonl"          # never written
    api = login_as(_app(spans), "root@platform.example.com", "acme")

    status, payload = api.get("/api/ops/overview")
    assert status == 200
    data = payload["data"]
    assert data["feed"]["present"] is False
    assert data["feed"]["spans"] == 0
    assert data["feed"]["lastSpanAt"] is None
    assert "nothing is measured" in data["feed"]["note"]
    assert data["tenants"] == []
    assert data["agents"] == []
    assert data["totals"]["attention"] == 0
    # an unknown tenant is refused rather than served an empty (healthy-looking) pane
    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 404


def test_no_data_is_never_ok(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    # a trace root and a guard decision: telemetry, but no serve attempt
    _feed(
        spans,
        [
            _span(kind=KIND_TRACE, agent=AGENT),
            _span(kind=KIND_GUARD, agent=AGENT, n=1),
        ],
    )
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    row = _agents(payload)[AGENT]
    assert row["state"]["state"] == "NO_DATA"
    assert row["state"]["severity"] == "critical"
    assert row["state"]["noData"] == [
        "availability-requests:acme",
        "latency-p95:acme",
    ]
    # nothing was measured, so nothing is reported as a number
    assert row["calls"] == 0
    assert row["attempts"] == 0
    assert row["errorRatePct"] is None
    assert row["latencyMs"] == {"samples": 0, "p50": None, "p95": None, "max": None}
    assert row["tokens"] == {"input": 0, "output": 0, "total": 0}


def test_a_silent_tenant_falls_out_of_its_window_and_reads_no_data(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=4)                                   # acme, an hour behind
    _healthy(spans, tenant="globex", n=4, ts=RECOVERED)     # globex is current
    api = login_as(_app(spans), "root@platform.example.com", "acme")

    status, payload = api.get("/api/ops/overview")
    assert status == 200
    rows = {row["tenantId"]: row for row in payload["data"]["tenants"]}
    assert rows["globex"]["state"]["state"] == "OK"
    # acme's newest span is two hours old, so its 1h windows hold nothing
    assert rows["acme"]["state"]["state"] == "NO_DATA"
    assert rows["acme"]["state"]["severity"] == "critical"
    assert rows["acme"]["attempts"] == 0
    assert rows["acme"]["latencyMs"]["p95"] is None


def test_the_pause_file_pauses_a_subject_without_hiding_the_breach(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    pauses = tmp_path / "ops-pauses.json"
    _healthy(spans, n=10, failures=6)                       # breaching
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    assert _agents(payload)[AGENT]["state"]["state"] == "ALERT"

    # the operator pauses the agent, recording why
    pauses.write_text(
        '{"pauses": [{"subject": "acme/coder-1", "reason": "maintenance",'
        ' "actor": "ops@acme.example.com"}]}',
        encoding="utf-8",
    )
    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    paused = _agents(payload)[AGENT]["state"]
    assert paused["state"] == "PAUSED"
    assert paused["severity"] == "paused"
    assert paused["paused"]["reason"] == "maintenance"
    assert paused["paused"]["actor"] == "ops@acme.example.com"
    # the pause is honest: the breach underneath it is still reported
    assert paused["observedState"] == "ALERT"
    assert paused["previousState"] == "ALERT"

    # removing the pause resumes evaluation onto the *true* state
    pauses.unlink()
    status, payload = api.get("/api/ops/agents", query={"tenant": "acme"})
    assert status == 200
    resumed = _agents(payload)[AGENT]["state"]
    assert resumed["state"] == "ALERT"
    assert resumed["paused"] is None

    status, payload = api.get("/api/ops/alerts")
    assert [
        move["reason"] for move in payload["data"]["history"][f"acme/{AGENT}"]
    ][-2:] == ["paused_by_operator", "resumed_by_operator"]


# --------------------------------------------------------------------------- #
# Failure and authorization behaviour
# --------------------------------------------------------------------------- #
def test_a_corrupt_feed_fails_closed_instead_of_reporting_health(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=4)
    with spans.open("a", encoding="utf-8") as handle:
        handle.write('{"traceId": "truncated"\n')
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/overview")
    assert status == 500
    assert payload["ok"] is False


def test_routes_require_a_session_and_agent_read(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=4)
    app = _app(spans)

    anonymous = login_as(app, "alice@acme.example.com", "acme")
    anonymous.cookies.clear()
    status, payload = anonymous.get("/api/ops/overview")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"

    # a principal scoped elsewhere cannot probe acme
    outsider = login_as(app, "bob@globex.example.com", "globex")
    status, payload = outsider.get("/api/ops/agents", query={"tenant": "acme"})
    assert status in (403, 404)
    assert payload["ok"] is False


def test_missing_unknown_tenant_and_non_get_methods(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=4)
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/agents")
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"

    status, payload = api.get("/api/ops/agents", query={"tenant": "nope"})
    assert status == 404
    assert payload["error"]["code"] == "unknown_tenant"

    status, payload = api.post("/api/ops/overview")
    assert status == 405

    status, payload = api.get("/api/ops/nope")
    assert status == 404
    assert payload["error"]["code"] == "not_found"


def test_every_read_is_scoped_to_the_principal(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=4)
    _healthy(spans, tenant="globex", n=4)
    app = _app(spans)

    scoped = login_as(app, "alice@acme.example.com", "acme")
    for surface in ("overview", "alerts", "dashboard"):
        status, payload = scoped.get(f"/api/ops/{surface}")
        assert status == 200, surface
        data = payload["data"]
        if surface == "alerts":
            # the alert feed scopes by SLO subject, not by a tenant row
            assert {row["subject"] for row in data["tenants"]} == {"acme"}, surface
            continue
        assert {row["tenantId"] for row in data["tenants"]} == {"acme"}, surface
        if surface == "dashboard":
            assert data["scope"] == ["acme"]

    root = login_as(app, "root@platform.example.com", "acme")
    status, payload = root.get("/api/ops/overview")
    assert {row["tenantId"] for row in payload["data"]["tenants"]} == {
        "acme", "globex"
    }


# --------------------------------------------------------------------------- #
# The lane's own projections, served
# --------------------------------------------------------------------------- #
def test_slos_route_serves_the_lane_verdicts_and_burn_rate(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=10, failures=6)
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/slos", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]
    assert {row["slo"] for row in data["results"]} == {
        "availability-requests:acme", "latency-p95:acme", "cost-budget:acme"
    }
    breach = next(
        row for row in data["results"]
        if row["slo"] == "availability-requests:acme"
    )
    assert breach["verdict"] == "BREACHED"
    assert breach["measuredRatio"] == 0.4
    assert breach["target"] == 0.95
    assert breach["windowStart"] == "2026-09-12T23:00:10Z"
    assert data["state"]["state"] == "ALERT"
    assert data["figureWindowSeconds"] == 3600
    assert {row["name"] for row in data["templates"]} == {
        "availability-requests", "latency-p95", "cost-budget"
    }
    # the lane's burn-rate view, worst first
    assert data["burnRate"][0]["slo"] == "availability-requests:acme"
    assert data["burnRate"][0]["verdict"] == "BREACHED"


def test_dashboard_route_serves_the_lane_projection(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=6)
    _healthy(spans, agent=OTHER_AGENT, n=4, ts=BREACH_AT)
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/ops/dashboard", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]
    assert data["schemaVersion"] == 1
    assert [row["tenantId"] for row in data["tenants"]] == ["acme"]
    assert data["tenants"][0]["tokens"] == 1500
    # both agents appear in the lane's usage roll-up, at agent granularity
    assert {row["agentId"] for row in data["usage"]} == {AGENT, OTHER_AGENT}
    assert "stateVocabulary" in data


def test_reads_never_write_the_feed_or_the_repo(tmp_path: Path):
    spans = tmp_path / "spans.jsonl"
    _healthy(spans, n=4)
    before = spans.read_bytes()
    mtime = spans.stat().st_mtime
    api = login_as(_app(spans), "alice@acme.example.com", "acme")

    for path in ("/api/ops/overview", "/api/ops/alerts", "/api/ops/dashboard"):
        status, _ = api.get(path)
        assert status == 200
    for path, query in (
        ("/api/ops/slos", {"tenant": "acme"}),
        ("/api/ops/agents", {"tenant": "acme"}),
    ):
        status, _ = api.get(path, query=query)
        assert status == 200

    assert spans.read_bytes() == before
    assert spans.stat().st_mtime == mtime
    # nothing appeared beside the feed: the pane is read-only
    assert sorted(child.name for child in tmp_path.iterdir()) == ["spans.jsonl"]
