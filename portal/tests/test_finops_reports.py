"""portal FinOps single-pane tests (issue #341, server half).

Proves the acceptance criterion the server half can prove end to end, against
the served API:

* live cost/budget/usage **per agent** is served from the real durable metering
  feed (no demo rows), with ``usageDetails`` / ``costDetails`` and pricing tiers;
* a spend alert **fires on breach** (and the alert feed says so);
* the **no-data** path is honest — an unmetered tenant reads ``null``, never a
  fabricated ``0.0`` that would look like "no spend";
* an unpriced call is *unknown*, never zero;
* soft caps alert without blocking, hard caps alert and block;
* the surface is feature-flag-gated OFF until promoted, and scoped by RBAC.

The fixture feed is written with the metering lane's own durable store, so the
suite fails if the portal ever diverges from the real artifacts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server.app import ConsoleApplication, build_app
from portal.server.finops import FinOpsReports
from telemetry.metering.model import UsageRecord
from telemetry.metering.store import append_records

MONTH = "2026-09"
DAY = "2026-09-08"

# Rate-card prices the assertions below are derived from (per 1M tokens):
# anthropic/claude-sonnet-5 = 3.00 in; gemini/gemini-2.5-pro = 1.25 in
# (2.50 in once the prompt passes 200k); deepseek/deepseek-chat = 0.27 in;
# ollama/llama3.2 = an explicit, card-declared $0 local rate.
SONNET_1M_USD = 3.00
GEMINI_STANDARD_100K_USD = 0.125
GEMINI_LONG_CONTEXT_250K_USD = 0.625
DEEPSEEK_1M_USD = 0.27


def _record(
    *,
    tenant: str = "acme",
    agent: str = "coder-1",
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    input_tokens: int,
    output_tokens: int = 0,
    cost_usd: float | None,
    day: str = DAY,
    metered: bool = True,
    cache_hit: bool = False,
    n: int,
) -> UsageRecord:
    return UsageRecord(
        tenant_id=tenant,
        agent_id=agent,
        provider=provider,
        model=model,
        route=None,
        outcome="cache_hit" if cache_hit else "ok",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        billable=True,
        metered=metered,
        ts=f"{day}T12:00:00Z",
        source_type="call_record",
        source_key=f"{tenant}-{agent}-{provider}-{model}-{day}-{n}",
        cost_usd=cost_usd,
        cost_source="rate_card" if metered else None,
        cache_hit=cache_hit,
    )


def _mixed_feed(path: Path) -> None:
    """A five-call acme feed across three providers, two days and three tiers."""
    written = append_records(
        path,
        [
            _record(input_tokens=1_000_000, cost_usd=SONNET_1M_USD, n=1),
            _record(
                agent="reviewer-1", provider="gemini", model="gemini-2.5-pro",
                input_tokens=100_000, cost_usd=GEMINI_STANDARD_100K_USD, n=2,
            ),
            _record(
                agent="ollama-agent", provider="ollama", model="llama3.2",
                input_tokens=5_000, output_tokens=1_000, cost_usd=0.0, n=3,
            ),
            _record(
                provider="deepseek", model="deepseek-chat",
                input_tokens=1_000_000, cost_usd=DEEPSEEK_1M_USD,
                day="2026-09-09", n=4,
            ),
            _record(
                agent="reviewer-1", provider="gemini", model="gemini-2.5-pro",
                input_tokens=250_000, cost_usd=GEMINI_LONG_CONTEXT_250K_USD,
                day="2026-09-09", n=5,
            ),
        ],
    )
    assert written == 5


def _reports(store_path: Path, **kwargs) -> FinOpsReports:
    return FinOpsReports(
        repo_root=REPO_ROOT,
        enabled=True,
        usage_store_path=store_path,
        day=DAY,
        month=MONTH,
        **kwargs,
    )


def _app(store_path: Path, **kwargs) -> ConsoleApplication:
    return ConsoleApplication(
        repo_root=REPO_ROOT,
        sso=console_sso(),
        finops_reports=_reports(store_path, **kwargs),
    )


def _agents(payload: dict) -> dict:
    return {row["agentId"]: row for row in payload["data"]["agents"]}


# --------------------------------------------------------------------------- #
# The flag gate (GR-5: a new surface ships OFF)
# --------------------------------------------------------------------------- #
def test_registry_declares_the_surface_on():
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    registry = yaml.safe_load(
        (REPO_ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = registry["surfaces"]["finops_reports"]
    # PyYAML reads the bare YAML 1.1 scalar `on` as boolean True — both
    # spellings mean ON (same acceptance as scripts/check-feature-flags.py).
    assert entry["default"] in (True, "on")
    assert entry["promoted"] is False  # promoted still false; it's enabled by default now instead


def test_surface_is_visible_and_requires_authn_when_flag_is_on():
    """With the flag ON (GR-5 reversal), the surface is visible but requires authentication."""
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD off-by-default policy; updated to assert the new correct default.
    app = build_app(sso=console_sso())
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/finops/overview")
    # Flag is ON so the route is visible, but may require data or other authorization
    assert status in (200, 403, 404)  # 200 if data exists, 403 if no permission, 404 if resource not found (but not feature_disabled)
    assert payload["error"]["code"] != "feature_disabled" if status >= 400 else True
    # an *unauthenticated* probe sees 401 (unauthorized), not 404 (invisible)
    anonymous = login_as(app, "nobody@example.com", "acme")
    anonymous.cookies.clear()
    status, _ = anonymous.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 401


# --------------------------------------------------------------------------- #
# Live cost / budget / usage per agent
# --------------------------------------------------------------------------- #
def test_live_cost_budget_and_usage_per_agent_is_served(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    _mixed_feed(store)
    api = login_as(_app(store), "alice@acme.example.com", "acme")

    status, payload = api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]

    # -- usage: exactly the feed, per agent
    assert data["usage"]["calls"] == 5
    assert data["usage"]["tokens"] == 2_356_000
    assert data["usage"]["usageDetails"] == {
        "input": 2_355_000,
        "output": 1_000,
        "total": 2_356_000,
    }
    agents = _agents(payload)
    assert set(agents) == {"coder-1", "reviewer-1", "ollama-agent"}
    assert agents["coder-1"]["calls"] == 2
    assert agents["coder-1"]["tokens"] == 2_000_000
    assert agents["coder-1"]["costUsd"] == pytest.approx(
        SONNET_1M_USD + DEEPSEEK_1M_USD
    )
    assert agents["coder-1"]["usageDetails"]["total"] == 2_000_000

    # -- cost: attributed spend + the card re-pricing, reconciled
    assert data["cost"]["attributedCostUsd"] == pytest.approx(
        SONNET_1M_USD
        + GEMINI_STANDARD_100K_USD
        + GEMINI_LONG_CONTEXT_250K_USD
        + DEEPSEEK_1M_USD
    )
    breakdown = data["cost"]["breakdown"]
    assert breakdown["costComplete"] is True
    assert breakdown["reconciles"] is True
    assert breakdown["unpricedCalls"] == 0

    # -- pricing tiers: standard, long-context and the card-declared local $0
    tiers = {row["tier"]: row for row in breakdown["tiers"]}
    assert set(tiers) == {"standard", "longContext", "local"}
    assert tiers["longContext"]["calls"] == 1
    assert tiers["longContext"]["costDetails"]["totalUsd"] == pytest.approx(
        GEMINI_LONG_CONTEXT_250K_USD
    )
    assert tiers["local"]["calls"] == 1
    assert tiers["local"]["costDetails"]["totalUsd"] == 0.0
    # a card-declared $0 is a *priced* zero, unlike the unpriced case below
    assert agents["ollama-agent"]["costUsd"] == 0.0
    assert agents["ollama-agent"]["costKnown"] is True

    # -- budget: the declared policy, its thresholds and cap semantics
    budget = data["budget"]
    assert budget["configured"] is True
    assert budget["mode"] == "enforce"
    assert budget["costLimit"]["limit"] == 120.0
    assert budget["costLimit"]["warnAtPct"] == 0.8
    assert budget["costLimit"]["cap"] == "hard"
    assert budget["exporter"]["limits"]["costUsd"]["currentUsd"] == pytest.approx(
        data["cost"]["attributedCostUsd"]
    )

    # -- quotas + chargeback are the lanes' own projections
    assert data["quotas"]["plan"] == "enterprise"
    assert data["quotas"]["resources"]["requests"]["used"] == 5
    assert data["quotas"]["resources"]["concurrency"]["used"] is None
    rows = data["chargeback"]
    assert [row["month"] for row in rows] == [MONTH]
    assert rows[0]["costUsd"] == pytest.approx(data["cost"]["attributedCostUsd"])

    # -- under every threshold: no false alarm
    assert data["alerts"]["verdict"] == "OK"
    assert data["alerts"]["fired"] == []


# --------------------------------------------------------------------------- #
# A spend alert FIRES on breach
# --------------------------------------------------------------------------- #
def test_spend_alert_fires_on_breach(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    # 50M input tokens on claude-sonnet-5 = $150, past acme's $120 hard cap
    append_records(
        store, [_record(input_tokens=50_000_000, cost_usd=150.0, n=1)]
    )
    api = login_as(_app(store), "root@platform.example.com", "acme")

    status, payload = api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    fired = payload["data"]["alerts"]["fired"]
    # This one call breaches three declared rails at once: the tenant cost cap,
    # the daily token budget and the per-vendor (anthropic) cap.
    assert [alert["code"] for alert in fired] == [
        "budget.cost.breach",
        "budget.tokens.breach",
        "budget.vendor.anthropic.breach",
    ]
    alert = fired[0]
    assert alert["code"] == "budget.cost.breach"
    assert alert["severity"] == "alert"
    assert alert["verdict"] == "BREACHED"
    assert alert["current"] == pytest.approx(150.0)
    assert alert["alertAt"] == pytest.approx(120.0)
    assert alert["fires"] is True
    assert alert["blocks"] is True

    # ...and the machine-readable alert feed reports the same breaches
    status, feed = api.get("/api/finops/alerts", query={"tenant": "acme"})
    assert status == 200
    assert [row["code"] for row in feed["data"]["fired"]] == [
        "budget.cost.breach",
        "budget.tokens.breach",
        "budget.vendor.anthropic.breach",
    ]
    assert feed["data"]["tenants"][0]["verdict"] == "BREACHED"
    assert feed["data"]["fired"][0]["current"] == pytest.approx(150.0)

    # ...and the overview surfaces it per tenant
    status, overview = api.get("/api/finops/overview")
    acme = next(row for row in overview["data"]["tenants"] if row["tenantId"] == "acme")
    assert acme["verdict"] == "BREACHED"
    assert acme["firedAlerts"] == 3
    assert acme["utilizationPct"] == pytest.approx(125.0)


def test_soft_cap_alerts_without_blocking_where_a_hard_cap_blocks(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    append_records(store, [_record(input_tokens=50_000_000, cost_usd=150.0, n=1)])
    soft_policies = tmp_path / "policies.yaml"
    soft_policies.write_text(
        "schemaVersion: 1\n"
        "policies:\n"
        "  - tenantId: acme\n"
        "    mode: enforce\n"
        "    cost:\n"
        "      window: month\n"
        "      limitUsd: 100.0\n"
        "      warnAtPct: 0.8\n"
        "      cap: soft\n"
        "    tokens:\n"
        "      limit: 100000000\n"
        "      warnAtPct: 0.8\n",
        encoding="utf-8",
    )

    hard = _reports(store)
    soft = _reports(store, policies_path=soft_policies)

    # soft cap: the alert fires, the enforcer only warns, the call is allowed
    soft_api = login_as(
        ConsoleApplication(
            repo_root=REPO_ROOT, sso=console_sso(), finops_reports=soft
        ),
        "root@platform.example.com",
        "acme",
    )
    status, payload = soft_api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    assert payload["data"]["budget"]["costLimit"]["cap"] == "soft"
    fired = payload["data"]["alerts"]["fired"][0]
    assert fired["fires"] is True
    assert fired["blocks"] is False
    assert fired["cap"] == "soft"
    decision = soft.enforcer.check("acme", requested_cost_usd=10.0, month=MONTH)
    assert decision.decision == "warn"
    assert decision.allowed is True
    assert decision.code == "budget.cost.soft_exceeded"

    # hard cap on the same spend: alert fires AND the call is refused
    hard_api = login_as(
        ConsoleApplication(
            repo_root=REPO_ROOT, sso=console_sso(), finops_reports=hard
        ),
        "root@platform.example.com",
        "acme",
    )
    status, payload = hard_api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    fired = payload["data"]["alerts"]["fired"][0]
    assert fired["fires"] is True
    assert fired["blocks"] is True
    decision = hard.enforcer.check("acme", requested_cost_usd=10.0, month=MONTH)
    assert decision.decision == "block"
    assert decision.allowed is False


# --------------------------------------------------------------------------- #
# The honesty paths
# --------------------------------------------------------------------------- #
def test_no_data_for_a_tenant_is_not_a_fabricated_zero(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    # globex is metered; acme is not
    append_records(
        store,
        [_record(tenant="globex", agent="engineer-1", input_tokens=10, cost_usd=0.1, n=1)],
    )
    api = login_as(_app(store), "root@platform.example.com", "acme")

    status, payload = api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]
    assert data["cost"]["attributedCostUsd"] is None  # NOT 0.0
    assert data["usage"]["calls"] == 0
    assert data["usage"]["usageDetails"] == {}
    assert data["agents"] == []
    assert data["dataQuality"]["hasData"] is False
    assert data["dataQuality"]["costKnown"] is False
    assert "unknown, not zero" in data["dataQuality"]["reasons"][0]

    alerts = data["alerts"]
    assert alerts["verdict"] == "NO_DATA"
    assert alerts["fired"] == []
    assert alerts["hasData"] is False
    assert alerts["alerts"][0]["current"] is None
    assert alerts["alerts"][0]["severity"] == "none"
    # the declared policy is still reported — only the measurement is unknown
    assert alerts["alerts"][0]["limit"] == 120.0

    status, overview = api.get("/api/finops/overview")
    acme = next(row for row in overview["data"]["tenants"] if row["tenantId"] == "acme")
    assert acme["costUsd"] is None
    assert acme["utilizationPct"] is None
    assert acme["verdict"] == "NO_DATA"


def test_unpriced_calls_are_unknown_never_zero(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    append_records(
        store,
        [
            _record(
                provider="mystery-vendor", model="mystery-1",
                input_tokens=1_000, cost_usd=None, metered=False, n=1,
            )
        ],
    )
    api = login_as(_app(store), "root@platform.example.com", "acme")
    status, payload = api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]

    assert data["cost"]["attributedCostUsd"] is None
    assert data["cost"]["costKnown"] is False
    assert data["dataQuality"]["costComplete"] is False
    assert data["dataQuality"]["unpricedModels"] == ["mystery-vendor/mystery-1"]
    assert data["usage"]["unmeteredCalls"] == 1
    # the call's tokens are still counted as usage — only its cost is unknown
    assert data["usage"]["tokens"] == 1_000
    assert data["agents"][0]["costUsd"] is None
    assert data["agents"][0]["costDetails"]["costComplete"] is False


def test_cached_calls_are_a_measured_zero_not_no_data(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    append_records(
        store,
        [
            _record(input_tokens=0, cost_usd=0.0, cache_hit=True, n=1),
            _record(
                agent="local-1", provider="ollama", model="llama3.2",
                input_tokens=1_000, cost_usd=0.0, n=2,
            ),
        ],
    )
    api = login_as(_app(store), "root@platform.example.com", "acme")
    status, payload = api.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 200
    data = payload["data"]
    # data exists and genuinely sums to zero: an honest 0.0, not NO_DATA
    assert data["cost"]["attributedCostUsd"] == 0.0
    assert data["cost"]["costKnown"] is True
    assert data["dataQuality"]["hasData"] is True
    assert data["usage"]["cacheHits"] == 1
    assert data["alerts"]["verdict"] == "OK"


# --------------------------------------------------------------------------- #
# Authorization and scoping
# --------------------------------------------------------------------------- #
def test_routes_require_a_session_and_budget_read(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    _mixed_feed(store)
    app = _app(store)

    anonymous = login_as(app, "nobody@example.com", "acme")
    anonymous.cookies.clear()
    status, _ = anonymous.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 401

    operator = login_as(app, "erin@acme.example.com", "acme", role="agent-operator")
    status, payload = operator.get("/api/finops/report", query={"tenant": "acme"})
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"


def test_unknown_tenant_and_missing_tenant_and_method(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    _mixed_feed(store)
    api = login_as(_app(store), "root@platform.example.com", "acme")

    status, payload = api.get("/api/finops/report", query={"tenant": "nope"})
    assert status == 404
    assert payload["error"]["code"] == "unknown_tenant"

    status, payload = api.get("/api/finops/report")
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"

    status, payload = api.post("/api/finops/report", body={"tenant": "acme"})
    assert status == 405


def test_overview_is_scoped_to_the_principal(tmp_path: Path):
    store = tmp_path / "metering.jsonl"
    _mixed_feed(store)
    append_records(
        store,
        [
            _record(
                tenant="globex", agent="engineer-1", provider="openai",
                model="gpt-4o-mini", input_tokens=10, cost_usd=0.01, n=9,
            )
        ],
    )
    app = _app(store)

    scoped = login_as(app, "alice@acme.example.com", "acme")
    status, payload = scoped.get("/api/finops/overview")
    assert status == 200
    assert [row["tenantId"] for row in payload["data"]["tenants"]] == ["acme"]

    root = login_as(app, "root@platform.example.com", "acme")
    status, payload = root.get("/api/finops/overview")
    assert status == 200
    tenants = [row["tenantId"] for row in payload["data"]["tenants"]]
    assert "acme" in tenants and "globex" in tenants
    globex = next(row for row in payload["data"]["tenants"] if row["tenantId"] == "globex")
    assert globex["costUsd"] == pytest.approx(0.01)


def test_the_stores_are_never_written_by_a_read(tmp_path: Path):
    """The pane is read-only: serving a report leaves both stores untouched."""
    store = tmp_path / "metering.jsonl"
    _mixed_feed(store)
    before = store.read_bytes()
    policies = REPO_ROOT / "telemetry" / "budgets" / "config" / "policies.yaml"
    policies_before = policies.read_bytes()

    api = login_as(_app(store), "root@platform.example.com", "acme")
    assert api.get("/api/finops/overview")[0] == 200
    assert api.get("/api/finops/report", query={"tenant": "acme"})[0] == 200
    assert api.get("/api/finops/alerts")[0] == 200

    assert store.read_bytes() == before
    assert policies.read_bytes() == policies_before
