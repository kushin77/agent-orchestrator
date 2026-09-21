"""portal Settings view tests (issue #1757).

Proves the acceptance criteria:

* the surface is feature-flag-gated OFF until promoted, before authN, like
  the other workbook-11 views;
* GET /api/settings/rows returns every domain
  ``portal.server.settings.SettingsAggregator.aggregate()`` joins, grouped;
* a domain whose source file is absent renders its "not reporting" row
  (schema id, ``key="status"``) rather than being dropped.
"""

from __future__ import annotations

import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server.app import build_app
from portal.server.config_flags import SETTINGS_SURFACE, surface_enabled
from portal.server.settings import SettingsView

TENANT = "acme"


def _app(view: SettingsView):
    return build_app(sso=console_sso(), settings_view=view)


def _authed(app):
    return login_as(app, "root@platform.example.com", TENANT)


def test_config_declares_the_view_off():
    document = yaml.safe_load(
        (REPO_ROOT / "portal" / "config" / "feature-flags.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = document["surfaces"][SETTINGS_SURFACE]
    assert entry["default"] in (False, "off"), "settings must ship OFF (GR-5)"
    assert surface_enabled(REPO_ROOT, surface=SETTINGS_SURFACE) is False


def test_surface_is_refused_while_the_flag_is_off():
    app = build_app(sso=console_sso())
    api = _authed(app)
    status, payload = api.get("/api/settings/rows")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    assert "portal/config/feature-flags.yaml" in payload["error"]["message"]


def test_rows_are_grouped_by_domain_and_cover_every_domain(tmp_path):
    view = SettingsView(repo_root=tmp_path, enabled=True)
    app = _app(view)
    status, payload = _authed(app).get("/api/settings/rows")
    assert status == 200
    domains = payload["data"]["domains"]
    assert sorted(domains) == [
        "dispatch_tier_policy",
        "fleet_jobs",
        "gate_skip_budget",
        "nous_secret",
        "portal_surfaces",
        "provider_flags",
    ]


def test_a_domain_with_source_absent_shows_its_not_reporting_row(tmp_path):
    view = SettingsView(repo_root=tmp_path, enabled=True)
    app = _app(view)
    status, payload = _authed(app).get("/api/settings/rows")
    assert status == 200
    fleet_jobs_rows = payload["data"]["domains"]["fleet_jobs"]
    assert len(fleet_jobs_rows) == 1
    row = fleet_jobs_rows[0]
    assert row["key"] == "status"
    assert row["editable"] is False
    assert "not reporting" in row["value"]
    assert row["source_file"] == "config/fleet-jobs.json"


def test_every_row_is_non_editable():
    view = SettingsView(repo_root=REPO_ROOT, enabled=True)
    app = _app(view)
    status, payload = _authed(app).get("/api/settings/rows")
    assert status == 200
    for rows in payload["data"]["domains"].values():
        for row in rows:
            assert row["editable"] is False
