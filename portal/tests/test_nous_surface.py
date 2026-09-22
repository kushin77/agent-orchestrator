"""portal console Nous provider surface tests (issue #1561).

The issue's three halves: the **view** (``nous.html``), its **nav registry
entry** (``console.js``), and its **gateway-proxied data adapter**
(``portal.server.nous.NousSurface``) behind ``GET /api/nous/overview``.

What this file proves, and why each arm exists:

* the view exists, is served, wires the surface, and carries the account
  link-out the issue requires (an action the console does not replicate);
* the surface is **feature-flag-gated OFF** by default and the gate runs
  **before authN**, so an unpromoted surface is invisible rather than merely
  unauthorised (the negative control);
* the four sections render real, declared/metered data — and where the
  provider's contract cannot answer (no balance endpoint), the payload says so
  instead of inventing a number;
* the cost discipline: a burst of reloads makes **one** upstream call, the
  probe never bills (it is the free, unauthenticated catalog read), and a
  failed probe serves the last reading **labelled stale with its age** — never
  as a current value, and never as a blank page.

No browser is needed: the view is vanilla ES5 DOM shipped as-is (no bundler, no
build step), so the assertions read the shipped markup and the served endpoint
directly — the same method ``test_finops_ui.py`` uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import REPO_ROOT, ApiClient, console_sso, login_as

from portal.server.app import ConsoleApplication, build_app
from portal.server.config_flags import NOUS_SURFACE, DECLARED_SURFACES, surface_enabled
from portal.server.nous import (
    NousSurface,
    STATE_LIVE,
    STATE_NOT_CONFIGURED,
    STATE_STALE,
    STATE_UNREACHABLE,
)
from telemetry.metering.model import UsageRecord
from telemetry.metering.store import append_records

STATIC = REPO_ROOT / "portal" / "static"
VIEW = STATIC / "views" / "nous.html"
CONSOLE_JS = STATIC / "js" / "console.js"
FLAGS = REPO_ROOT / "portal" / "config" / "feature-flags.yaml"

CREDENTIALS = REPO_ROOT / "infra" / "terraform" / "provider-credentials.json"
REGISTRY = REPO_ROOT / "infra" / "feature-flags" / "registry.yaml"

SET = "2026-09-22T12:00:00Z"


class Clock:
    """A settable clock, so the TTL/age arms are exact rather than flaky."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class CountingProbe:
    """A probe double that counts calls — the thing the caching arms assert on."""

    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.calls = 0
        self._result = result if result is not None else {
            "endpoint": "https://inference-api.nousresearch.com/v1/models",
            "modelCount": 402,
        }
        self._error = error

    def __call__(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


def _surface(**kwargs) -> NousSurface:
    kwargs.setdefault("repo_root", REPO_ROOT)
    kwargs.setdefault("enabled", True)
    return NousSurface(**kwargs)


def _app(surface: NousSurface) -> ConsoleApplication:
    return ConsoleApplication(
        repo_root=REPO_ROOT, sso=console_sso(), nous_surface=surface
    )


def _record(*, model: str = "inclusionai/ling-3.0-flash", n: int = 1, cost: float = 0.05):
    return UsageRecord(
        tenant_id="acme",
        agent_id="coder-1",
        provider="nous",
        model=model,
        route=None,
        outcome="ok",
        input_tokens=1_000,
        output_tokens=500,
        billable=True,
        metered=True,
        ts=SET,
        source_type="call_record",
        source_key=f"nous-{model}-{n}",
        cost_usd=cost,
        cost_source="provider_credits",
        cache_hit=False,
    )


# -- the view exists, is served, and wires the surface ----------------------
def test_nous_view_document_is_served(app):
    status, payload = ApiClient(app).get("/views/nous.html")
    assert status == 200
    assert isinstance(payload, bytes)
    assert b"/api/nous/overview" in payload


def test_nous_view_wires_the_overview_surface():
    html = VIEW.read_text(encoding="utf-8")
    assert "/api/nous/overview" in html


def test_nous_view_carries_the_manage_account_link_out():
    """The issue requires a link-out for actions the console does not replicate.

    The URL is **not** baked into the view: the console is self-hosted end to
    end and ``portal/tests/test_assets.py`` forbids any external reference in a
    static asset. So the view renders the link-out from the payload's
    ``links.manageAccount`` — the constant is asserted here, and the served
    payload is asserted to carry it.
    """
    from portal.server.nous import MANAGE_ACCOUNT_URL

    assert MANAGE_ACCOUNT_URL == "https://portal.nousresearch.com"
    html = VIEW.read_text(encoding="utf-8")
    # the view binds the payload's link rather than hardcoding an external URL
    assert "data.links" in html
    assert "manageAccount" in html
    assert "manageAccountSlot" in html
    # and no external reference is baked into the static asset
    for marker in ("http://", "https://"):
        assert marker not in html, f"nous.html must not reference {marker!r}"


def test_the_served_payload_carries_the_manage_account_link():
    api = login_as(_app(_surface(env={})), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    assert status == 200
    assert payload["data"]["links"]["manageAccount"] == "https://portal.nousresearch.com"


def test_nous_view_names_the_flag_when_the_surface_is_off():
    html = VIEW.read_text(encoding="utf-8")
    assert "surfaces.nous" in html


# -- the nav registry entry -------------------------------------------------
def test_nav_registers_nous_as_a_gated_global_view():
    js = CONSOLE_JS.read_text(encoding="utf-8")
    assert 'id: "nous"' in js
    assert "/api/nous/overview" in js
    # gated: the entry is offered only while the surface is reachable …
    assert "GATED_GLOBAL_VIEWS" in js
    # … and it is org-wide, so it takes no ?tenant= and names its own crumb.
    assert "allGlobalViews" in js


def test_nav_does_not_add_a_second_always_present_entry():
    """The provider entry must be gated, not a permanent NAV_GLOBAL row."""
    js = CONSOLE_JS.read_text(encoding="utf-8")
    nav_global = js.split("var NAV_GLOBAL = [", 1)[1].split("];", 1)[0]
    assert '"nous"' not in nav_global
    # the comment above the gated registry must be inside the gated list, so the
    # registry itself — not the docs — carries the membership.
    gated = js.split("var GATED_GLOBAL_VIEWS = [", 1)[1].split("];", 1)[0]
    assert '"nous"' in gated


# -- the negative control: the surface ships gated OFF, before authN --------
def test_surface_key_is_declared_and_ships_off():
    assert NOUS_SURFACE in DECLARED_SURFACES
    assert surface_enabled(REPO_ROOT, surface=NOUS_SURFACE) is False
    assert _surface(enabled=None).enabled is False


def test_shipped_default_in_the_portal_config_is_off():
    import yaml

    document = yaml.safe_load(FLAGS.read_text(encoding="utf-8"))
    entry = document["surfaces"][NOUS_SURFACE]
    assert entry["default"] in (False, "off")


def test_route_is_gated_before_authn():
    """An unpromoted surface is INVISIBLE, not merely unauthorised.

    The client here presents no session cookie at all: if the flag gate ran
    after authN this would answer 401 and leak the surface's existence.
    """
    api = ApiClient(build_app(sso=console_sso()))
    status, payload = api.get("/api/nous/overview")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    assert "portal/config/feature-flags.yaml surfaces.nous" in payload["error"]["message"]


# -- the four sections, when the surface is promoted ------------------------
def test_overview_serves_the_four_sections():
    api = login_as(_app(_surface(env={})), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    assert status == 200
    data = payload["data"]
    assert data["schema"] == "ao.portal-nous/v1"
    for section in ("status", "catalog", "credits", "usage"):
        assert section in data, section
    # the declared catalog and its per-model cost are real, from the declaration
    assert data["catalog"]["models"], "the declared priced model set must not be empty"
    assert data["catalog"]["plans"], "the declared plan ceilings must not be empty"
    assert data["links"]["manageAccount"] == "https://portal.nousresearch.com"


def test_unknown_nous_read_is_not_found():
    api = login_as(_app(_surface(env={})), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/nope")
    assert status == 404
    assert payload["error"]["code"] == "not_found"


# -- the credential boundary: no key means no reading, and no probe ---------
def test_no_credential_renders_not_configured_and_never_probes():
    probe = CountingProbe()
    surface = _surface(env={}, probe=probe)
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    assert status == 200
    data = payload["data"]
    assert data["status"]["state"] == STATE_NOT_CONFIGURED
    # nothing is presented as a live reading …
    assert data["status"]["valueAt"] is None
    assert data["status"]["ageSeconds"] is None
    # … and the billed provider was not touched at all.
    assert probe.calls == 0
    assert data["status"]["probe"]["callsMade"] == 0
    # the declaration it read is named, and the operator step is named too.
    assert data["status"]["credential"]["secretId"] == "ao-nous-api-key"
    assert data["status"]["credential"]["env"] == "NOUS_API_KEY"
    assert "NOUS_API_KEY" in data["status"]["detail"]


def test_configured_but_unreachable_renders_an_explicit_state():
    """The issue's negative control: a bad key must not blank the page."""
    probe = CountingProbe(error=RuntimeError("connection refused"))
    surface = _surface(env={"NOUS_API_KEY": "bad"}, probe=probe)
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    assert status == 200
    data = payload["data"]
    assert data["status"]["state"] == STATE_UNREACHABLE
    assert data["status"]["valueAt"] is None
    assert "could not be reached" in data["status"]["detail"]
    assert data["status"]["probe"]["callsMade"] == 1


def test_live_then_failure_serves_stale_labelled_with_its_age():
    """A stale reading is labelled stale — never presented as current."""
    clock = Clock()
    surface = _surface(env={"NOUS_API_KEY": "k"}, probe=CountingProbe(), clock=clock)
    api = login_as(_app(surface), "root@platform.example.com", "acme")

    status, payload = api.get("/api/nous/overview")
    assert status == 200
    assert payload["data"]["status"]["state"] == STATE_LIVE
    live_at = payload["data"]["status"]["valueAt"]

    # the provider starts failing, and the TTL has expired
    surface._probe = CountingProbe(error=RuntimeError("boom"))
    clock.t += 500.0
    status, payload = api.get("/api/nous/overview")
    assert status == 200
    data = payload["data"]
    assert data["status"]["state"] == STATE_STALE
    assert data["status"]["ageSeconds"] >= 500
    assert data["status"]["valueAt"] == live_at
    assert "not a current value" in data["status"]["detail"]


# -- the cost discipline: reloads must not reach a billed provider ----------
def test_reload_burst_makes_one_upstream_call():
    clock = Clock()
    probe = CountingProbe()
    api = login_as(
        _app(_surface(env={"NOUS_API_KEY": "k"}, probe=probe, clock=clock)),
        "root@platform.example.com",
        "acme",
    )
    first = api.get("/api/nous/overview")
    assert first[0] == 200
    assert probe.calls == 1
    # a burst of reloads inside the TTL: no further upstream call, and the
    # served reading is reported as cached.
    for _ in range(20):
        clock.t += 1.0
        status, payload = api.get("/api/nous/overview")
        assert status == 200
    assert probe.calls == 1, "a reload burst must not reach the provider"
    assert payload["data"]["status"]["cached"] is True
    assert payload["data"]["status"]["state"] == STATE_LIVE


def test_the_probe_interval_floor_bounds_retries_even_when_nothing_is_cached():
    """With no cached value, a failing provider must still not be hammered."""
    clock = Clock()
    probe = CountingProbe(error=RuntimeError("boom"))
    api = login_as(
        _app(_surface(env={"NOUS_API_KEY": "k"}, probe=probe, clock=clock)),
        "root@platform.example.com",
        "acme",
    )
    api.get("/api/nous/overview")
    assert probe.calls == 1
    for _ in range(5):
        clock.t += 2.0
        status, payload = api.get("/api/nous/overview")
        assert status == 200
    assert probe.calls == 1, "the floor must bound retries, not just the TTL"
    assert payload["data"]["status"]["state"] == STATE_UNREACHABLE


def test_the_live_probe_is_the_free_unauthenticated_catalog_read(monkeypatch):
    """The probe cannot bill and cannot leak the key.

    Measured at the wire, not by grepping the source: the request that would go
    out is captured and asserted to be the free ``/models`` catalog GET with no
    ``Authorization`` header — so a probe can neither debit the account nor
    carry the credential.
    """
    from portal.server import nous as nous_module

    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"data": [{"id": "a"}, {"id": "b"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(getattr(request, "headers", {}) or {})
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(nous_module.urllib.request, "urlopen", fake_urlopen)
    reading = nous_module._default_probe(
        "https://inference-api.nousresearch.com/v1", timeout=5.0
    )
    assert captured["url"] == "https://inference-api.nousresearch.com/v1/models"
    assert captured["method"] == "GET"
    assert captured["timeout"] == 5.0
    assert not any(
        name.lower() == "authorization" for name in captured["headers"]
    ), "the catalog probe must send no credential at all"
    assert reading["modelCount"] == 2


def test_a_probe_that_refuses_is_reported_rather_than_swallowed(monkeypatch):
    """A probe failure is a named state, never a silent empty success."""
    from portal.server import nous as nous_module

    def boom(request, timeout=None):
        raise nous_module.urllib.error.URLError("refused")

    monkeypatch.setattr(nous_module.urllib.request, "urlopen", boom)
    surface = _surface(env={"NOUS_API_KEY": "k"}, probe=None)
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    assert status == 200
    data = payload["data"]
    assert data["status"]["state"] == STATE_UNREACHABLE
    assert "the catalog probe failed" in data["status"]["detail"]


# -- credits: the declared meter, never a fabricated balance ----------------
def test_credits_never_fabricate_a_balance():
    api = login_as(_app(_surface(env={})), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    credits = payload["data"]["credits"]
    # the provider's own declaration says there is no balance endpoint …
    assert credits["balanceEndpoint"] is None
    assert credits["balanceTracking"] == "manual-top-ups"
    # … so no plan pinned means no number, and the reason is carried.
    assert credits["plan"] is None
    assert credits["remainingUsd"] is None
    assert "no endpoint to read it back from" in credits["remainingReason"]


def test_a_pinned_plan_turns_the_metered_debits_into_real_arithmetic(tmp_path):
    store = tmp_path / "metering.jsonl"
    append_records(store, [_record(n=1, cost=2.0), _record(n=2, cost=3.0)])
    surface = _surface(
        env={}, usage_store_path=store, plan="plus", top_ups_usd=[20.0]
    )
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    credits = payload["data"]["credits"]
    # plus grants 22 USD, plus a declared 20 USD top-up, minus the 5 USD metered
    assert credits["debitedUsd"] == 5.0
    assert credits["remainingUsd"] == 37.0


def test_an_undeclared_top_up_is_refused_not_silently_invented():
    surface = _surface(env={}, plan="plus", top_ups_usd=[7.0])
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    credits = payload["data"]["credits"]
    assert credits["remainingUsd"] is None
    assert "could not be resolved" in credits["remainingReason"]


# -- usage / burn rate: real metered records --------------------------------
def test_metered_nous_usage_is_reported_per_model(tmp_path):
    store = tmp_path / "metering.jsonl"
    append_records(
        store,
        [
            _record(model="inclusionai/ling-3.0-flash", n=1, cost=0.05),
            _record(model="inclusionai/ling-3.0-flash", n=2, cost=0.05),
            _record(model="qwen/qwen3.7-flash", n=3, cost=0.10),
        ],
    )
    surface = _surface(env={}, usage_store_path=store)
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    usage = payload["data"]["usage"]
    assert usage["calls"] == 3
    assert usage["totalTokens"] == 3 * 1_500
    assert usage["costUsd"] == pytest.approx(0.2)
    by_model = {row["model"]: row for row in usage["byModel"]}
    assert by_model["inclusionai/ling-3.0-flash"]["calls"] == 2
    assert by_model["qwen/qwen3.7-flash"]["calls"] == 1


def test_another_providers_records_are_not_counted_as_nous(tmp_path):
    store = tmp_path / "metering.jsonl"
    foreign = _record(n=1)
    foreign = type(foreign)(
        tenant_id=foreign.tenant_id,
        agent_id=foreign.agent_id,
        provider="anthropic",
        model="claude-sonnet-5",
        route=None,
        outcome="ok",
        input_tokens=1_000,
        output_tokens=500,
        billable=True,
        metered=True,
        ts=SET,
        source_type="call_record",
        source_key="anthropic-1",
        cost_usd=1.0,
        cost_source="rate_card",
        cache_hit=False,
    )
    append_records(store, [foreign, _record(n=2, cost=0.05)])
    surface = _surface(env={}, usage_store_path=store)
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    assert payload["data"]["usage"]["calls"] == 1
    assert payload["data"]["usage"]["costUsd"] == pytest.approx(0.05)


def test_a_missing_usage_store_is_named_not_silently_zero(tmp_path):
    surface = _surface(env={}, usage_store_path=tmp_path / "absent.jsonl")
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    data = payload["data"]
    assert data["usage"]["calls"] == 0
    # the path actually checked is named, not the constant it defaulted from
    assert (tmp_path / "absent.jsonl").as_posix() in data["missing"]


# -- declaration readers fail closed ---------------------------------------
def test_a_missing_declaration_is_named_rather_than_guessed(tmp_path):
    surface = _surface(
        env={},
        credentials_path=tmp_path / "absent.json",
        usage_store_path=tmp_path / "absent.jsonl",
    )
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    data = payload["data"]
    assert data["status"]["state"] == STATE_NOT_CONFIGURED
    assert "infra/terraform/provider-credentials.json" in data["missing"]
    assert "nothing to probe with" in data["status"]["detail"]


def test_the_gate_state_is_read_from_the_registry_entry():
    """``enable_hermes`` is spelled ``tf_flag`` on the ``hermes`` service row."""
    surface = _surface(env={}, credentials_path=CREDENTIALS, feature_flags_path=REGISTRY)
    api = login_as(_app(surface), "root@platform.example.com", "acme")
    status, payload = api.get("/api/nous/overview")
    gate = payload["data"]["status"]["gate"]
    assert gate["name"] == "enable_hermes"
    assert gate["state"] == "on"
    assert gate["promoted"] is False
