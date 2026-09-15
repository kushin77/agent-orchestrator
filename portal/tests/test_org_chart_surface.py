"""portal org-chart view tests (issue #642, workbook-11, server half).

Proves, against the served API, the acceptance criterion the serving half can
prove end to end:

* the view renders the **workbook-1 declaration** — every node's id, title,
  reports-to edge, declared tier and declared monthly cap are the declaration's
  own values (the adapter restates none of them), and the single root is marked;
* the view joins the **workbook-6 role-health feed** — a role with metered
  spend reports the feed's own burn, and a role the feed never metered reports
  ``null`` spend (never a fabricated ``0`` that would read as "no spend, all
  good");
* an unreadable or invalid declaration is served as an explicit ``unresolved``
  document, never invented around;
* the surface is **feature-flag-gated OFF** until promoted, and the refusal
  happens *before* authentication (an unpromoted view is invisible, not merely
  protected).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server.app import ConsoleApplication, build_app
from portal.server.config_flags import ORG_CHART_SURFACE, surface_enabled
from portal.server.org_chart import ORG_CHART_RELATIVE, SCHEMA, OrgChartView

#: The workbook-1 declaration this repo ships.
COMMITTED_CHART = REPO_ROOT / ORG_CHART_RELATIVE

#: The C-suite rows the declaration carries (issue #632 workbook-1, five roles).
EXPECTED_ROLES = ["ceo", "cto", "coo", "cfo", "cmo"]


def _view(**kwargs) -> OrgChartView:
    return OrgChartView(repo_root=REPO_ROOT, **kwargs)


def _app(view: OrgChartView) -> ConsoleApplication:
    return build_app(sso=console_sso(), org_chart_view=view)


def _authed(app: ConsoleApplication):
    return login_as(app, "root@platform.example.com", "acme")


# --------------------------------------------------------------------------- #
# The flag gate (GR-5: a new surface ships OFF)
# --------------------------------------------------------------------------- #
def test_config_declares_the_view_off():
    """The portal's own config declares the view, and declares it OFF."""
    document = yaml.safe_load(
        (REPO_ROOT / "portal" / "config" / "feature-flags.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert document["default_policy"] in (False, "off")
    entry = document["surfaces"][ORG_CHART_SURFACE]
    # PyYAML reads the bare YAML 1.1 scalar `off` as boolean False — both
    # spellings mean OFF (same acceptance as scripts/check-feature-flags.py).
    assert entry["default"] in (False, "off"), (
        "the org-chart view must ship OFF (GR-5)"
    )
    assert surface_enabled(REPO_ROOT, surface=ORG_CHART_SURFACE) is False


def test_surface_is_refused_while_the_flag_is_off():
    """The default app (config decides) refuses the whole family — before authN."""
    app = build_app(sso=console_sso())
    api = _authed(app)
    status, payload = api.get("/api/orgchart/chart")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    assert "portal/config/feature-flags.yaml" in payload["error"]["message"]
    # an *unauthenticated* probe sees the same 404: the view is invisible, not
    # merely protected.
    anonymous = _authed(app)
    anonymous.cookies.clear()
    status, _ = anonymous.get("/api/orgchart/health")
    assert status == 404


def test_the_view_renders_once_its_flag_is_flipped_on():
    """Promotion is the only thing between OFF and a rendered view."""
    app = _app(_view(enabled=True))
    status, payload = _authed(app).get("/api/orgchart/chart")
    assert status == 200
    assert payload["data"]["state"] == "resolved"
    assert payload["data"]["nodes"]


# --------------------------------------------------------------------------- #
# The workbook-1 declaration is what is rendered (no second copy)
# --------------------------------------------------------------------------- #
def test_nodes_are_the_declaration_rows_verbatim():
    app = _app(_view(enabled=True))
    status, payload = _authed(app).get("/api/orgchart/chart")
    served = payload["data"]

    declaration = yaml.safe_load(COMMITTED_CHART.read_text(encoding="utf-8"))
    assert served["schema"] == SCHEMA
    assert served["id"] == declaration["id"]
    assert served["tenant"] == declaration["tenant"]
    assert served["version"] == declaration["version"]
    assert served["root"] == declaration["root"]
    assert served["principal"] == declaration["principal"]

    rows = {role["id"]: role for role in declaration["roles"]}
    assert list(rows) == EXPECTED_ROLES
    served_nodes = {node["id"]: node for node in served["nodes"]}
    assert list(served_nodes) == EXPECTED_ROLES

    for role_id, role in rows.items():
        node = served_nodes[role_id]
        # every governance field is the declaration's own value — the adapter
        # restates no cap, tier or cadence.
        assert node["title"] == role["title"]
        assert node["reportsTo"] == role["reportsTo"]
        assert node["defaultModelTier"] == role["defaultModelTier"]
        assert node["monthlyBudgetCapUsd"] == role["monthlyBudgetCapUsd"]
        assert node["heartbeatSchedule"] == role["heartbeatSchedule"]


def test_the_chart_has_one_marked_root_and_every_edge_resolves():
    app = _app(_view(enabled=True))
    _status, payload = _authed(app).get("/api/orgchart/chart")
    served = payload["data"]

    roots = [node["id"] for node in served["nodes"] if node["isRoot"]]
    assert roots == [served["root"]] == ["ceo"], "exactly one root, declared"

    ids = {node["id"] for node in served["nodes"]}
    assert len(served["edges"]) == len(served["nodes"])
    for edge in served["edges"]:
        assert edge["from"] in ids
        # the root's edge leaves the agent org for the board principal, which is
        # deliberately not a persona — every other edge is an internal node.
        assert edge["to"] in ids or edge["to"] == served["principal"]


def test_an_unreadable_declaration_is_unresolved_not_invented(tmp_path: Path):
    """A missing chart is served empty *and honest*, never fabricated."""
    app = _app(_view(enabled=True, chart_path=tmp_path / "absent.yaml"))
    _status, payload = _authed(app).get("/api/orgchart/chart")
    served = payload["data"]
    assert served["state"] == "unresolved"
    assert served["nodes"] == []
    assert served["note"]


def test_an_invalid_declaration_is_refused_by_the_registry_validator(
    tmp_path: Path,
):
    """The chart's own validator decides — a doubled root is refused."""
    raw = yaml.safe_load(COMMITTED_CHART.read_text(encoding="utf-8"))
    for role in raw["roles"]:
        if role["id"] == "cto":
            role["reportsTo"] = "board"  # a second root: workbook-1 forbids it
    broken = tmp_path / "org-chart.yaml"
    broken.write_text(yaml.safe_dump(raw), encoding="utf-8")

    app = _app(_view(enabled=True, chart_path=broken))
    _status, payload = _authed(app).get("/api/orgchart/chart")
    served = payload["data"]
    assert served["state"] == "unresolved"
    assert served["nodes"] == []
    assert "validation" in served["note"]


# --------------------------------------------------------------------------- #
# The workbook-6 join: burn + heartbeat, honest about no-data
# --------------------------------------------------------------------------- #
class _Feed:
    """A stand-in for ``RoleHealthReport`` carrying a fixed snapshot.

    Deliberately a shape double, not a mock of the lane: the join reads only
    the lane's own ``snapshot()`` keys, so pinning that shape here is what
    proves the adapter consumes the feed rather than re-deriving it.
    """

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> dict:
        return self._snapshot


def _snapshot(*, spend_for_ceo: float | None) -> dict:
    metered = spend_for_ceo is not None
    return {
        "month": "2026-09",
        "capSource": "registry/personas/org-chart.yaml + gateway/finops/budgets.yaml",
        "warnAtPct": 100.0,
        "roleBudgetBurn": {
            "rows": [
                {
                    "roleId": "ceo",
                    "costUsd": spend_for_ceo or 0.0,
                    "burnPct": (spend_for_ceo or 0.0) / 300.0 * 100.0,
                    "position": "ok",
                    "hasMeteredCalls": metered,
                }
            ]
        },
        "roleHeartbeat": {
            "statuses": [
                {"roleId": "ceo", "status": "ok", "schedule": "hourly"},
            ]
        },
        "roleAlerts": [],
    }


def test_a_metered_role_reports_the_feeds_own_burn():
    view = _view(enabled=True, health_report=_Feed(_snapshot(spend_for_ceo=75.0)))
    app = _app(view)
    status, payload = _authed(app).get("/api/orgchart/health")
    assert status == 200
    served = payload["data"]
    assert served["state"] == "resolved"

    rows = {row["roleId"]: row for row in served["rows"]}
    assert len(rows) == len(EXPECTED_ROLES), "every declared node gets a row"
    ceo = rows["ceo"]
    assert ceo["spentUsd"] == 75.0
    assert ceo["burnPct"] == pytest.approx(25.0)
    assert ceo["position"] == "ok"
    assert ceo["hasMeteredCalls"] is True
    assert ceo["heartbeatStatus"] == "ok"
    # the cap shown is the declaration's, not a number the adapter computed
    assert ceo["monthlyCapUsd"] == 300


def test_an_unmetered_role_reports_null_not_zero():
    """No data is *unknown*: ``null`` spend, never a confident ``0``."""
    view = _view(enabled=True, health_report=_Feed(_snapshot(spend_for_ceo=None)))
    app = _app(view)
    _status, payload = _authed(app).get("/api/orgchart/health")
    rows = {row["roleId"]: row for row in payload["data"]["rows"]}

    assert rows["ceo"]["spentUsd"] is None
    assert rows["ceo"]["burnPct"] is None
    assert rows["ceo"]["position"] == "unknown"
    assert rows["ceo"]["hasMeteredCalls"] is False
    # a node the feed never covered is still listed (a missing seat is visible)
    assert rows["cmo"]["spentUsd"] is None
    assert rows["cmo"]["position"] == "unknown"


def test_an_unavailable_feed_is_stated_never_zeroed():
    view = _view(enabled=True, health_report=None, health_factory=None)
    app = _app(view)
    _status, payload = _authed(app).get("/api/orgchart/health")
    served = payload["data"]
    assert served["state"] == "unresolved"
    assert served["rows"] == []
    assert "unavailable" in served["note"]


def test_a_broken_feed_factory_fails_closed():
    def explode():
        raise RuntimeError("metering store is corrupt")

    view = _view(enabled=True, health_factory=explode)
    app = _app(view)
    _status, payload = _authed(app).get("/api/orgchart/health")
    served = payload["data"]
    assert served["state"] == "unresolved"
    assert served["rows"] == []


# --------------------------------------------------------------------------- #
# Transport contract
# --------------------------------------------------------------------------- #
def test_routes_are_get_only_and_unknown_subviews_404():
    app = _app(_view(enabled=True))
    api = _authed(app)

    status, payload = api.post("/api/orgchart/chart")
    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"

    status, _ = api.get("/api/orgchart/nope")
    assert status == 404


def test_the_view_requires_a_console_session():
    app = _app(_view(enabled=True))
    api = login_as(app, "nobody@example.com", "acme")
    api.cookies.clear()
    status, payload = api.get("/api/orgchart/chart")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"
