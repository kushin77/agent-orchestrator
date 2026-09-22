"""portal cross-engine Sessions view tests (issue #1563, spog v1).

Proves the acceptance criteria against fixture directories — no live data:

* the surface is feature-flag-gated OFF until promoted, before authN, like
  the other workbook-11 views;
* a root that exists is joined into rows (engine/issue/branch/state/agent/
  lane/age), each row naming the exact source file it came from;
* a root that does not exist is named in ``missing``, never silently dropped;
* a released (non-``claim``) event in ``.board/claims.jsonl`` does not surface
  as a live session.
"""

from __future__ import annotations

import json

import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server.app import build_app
from portal.server.config_flags import SESSIONS_SURFACE, surface_enabled
from portal.server.sessions import SessionsView

TENANT = "acme"


def _view(tmp_path, **overrides) -> SessionsView:
    roots = {
        "claude-fleet": tmp_path / "fleet",
        "claude-board": tmp_path / "board",
        "deepseek": tmp_path / "deepseek",
    }
    roots.update(overrides.pop("roots", {}))
    return SessionsView(repo_root=REPO_ROOT, enabled=True, roots=roots, **overrides)


def _app(view: SessionsView):
    return build_app(sso=console_sso(), sessions_view=view)


def _authed(app):
    return login_as(app, "root@platform.example.com", TENANT)


# --------------------------------------------------------------------------- #
# The flag gate (GR-5: a new surface ships OFF)
# --------------------------------------------------------------------------- #
def test_config_declares_the_view_on():
    document = yaml.safe_load(
        (REPO_ROOT / "portal" / "config" / "feature-flags.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = document["surfaces"][SESSIONS_SURFACE]
    assert entry["default"] in (True, "on"), "sessions must ship ON (GR-5 reversal)"
    assert surface_enabled(REPO_ROOT, surface=SESSIONS_SURFACE) is True


def test_the_default_app_requires_authn_not_hidden_by_flag():
    app = build_app(sso=console_sso())
    anonymous = _authed(app)
    anonymous.cookies.clear()
    status, payload = anonymous.get("/api/sessions/rows")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


# --------------------------------------------------------------------------- #
# Absent roots are named, never silently dropped
# --------------------------------------------------------------------------- #
def test_all_three_roots_absent_are_all_named_missing(tmp_path):
    app = _app(_view(tmp_path))
    status, payload = _authed(app).get("/api/sessions/rows")
    assert status == 200
    data = payload["data"]
    assert data["sessions"] == []
    assert sorted(data["missing"]) == ["claude-board", "claude-fleet", "deepseek"]


# --------------------------------------------------------------------------- #
# Each root, joined
# --------------------------------------------------------------------------- #
def test_fleet_claims_are_joined_and_named_by_source(tmp_path):
    fleet = tmp_path / "fleet"
    (fleet / "claims").mkdir(parents=True)
    (fleet / "claims" / "1603.json").write_text(
        json.dumps(
            {
                "agent": "lane-1603",
                "issue": 1603,
                "branch": "issue-1603-lanes-settled",
                "lane": "1603",
                "state": "claimed",
                "claimed_at": "2026-09-21T17:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    app = _app(_view(tmp_path))
    status, payload = _authed(app).get("/api/sessions/rows")
    assert status == 200
    data = payload["data"]
    assert "claude-fleet" not in data["missing"]
    rows = [r for r in data["sessions"] if r["engine"] == "claude-fleet"]
    assert len(rows) == 1
    row = rows[0]
    assert row["issue"] == 1603
    assert row["branch"] == "issue-1603-lanes-settled"
    assert row["agent"] == "lane-1603"
    assert row["source"] == ".fleet/claims/1603.json"
    assert row["age"] is not None


def test_board_claims_join_and_a_released_claim_does_not_surface(tmp_path):
    board = tmp_path / "board"
    board.mkdir()
    (board / "claims.jsonl").write_text(
        "\n".join(
            json.dumps(line)
            for line in [
                {"event": "claim", "issue": 232, "agent": "subagent-a", "at": "2026-09-21T10:00:00Z", "lane": "fleet"},
                {"event": "release", "issue": 232, "agent": "subagent-a", "at": "2026-09-21T10:05:00Z", "lane": ""},
                {"event": "claim", "issue": 500, "agent": "subagent-b", "at": "2026-09-21T11:00:00Z", "lane": "triage"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    app = _app(_view(tmp_path))
    _status, payload = _authed(app).get("/api/sessions/rows")
    data = payload["data"]
    board_rows = [r for r in data["sessions"] if r["engine"] == "claude-board"]
    issues = {r["issue"] for r in board_rows}
    assert 500 in issues, "the live (unreleased) claim surfaces"
    assert 232 not in issues, "a released claim is not a live session"


def test_deepseek_root_present_with_no_sessions_dir_is_not_missing_and_empty(tmp_path):
    """A readable root with nothing to join is a real empty answer, not 'not reporting'."""
    (tmp_path / "deepseek").mkdir()
    app = _app(_view(tmp_path))
    _status, payload = _authed(app).get("/api/sessions/rows")
    data = payload["data"]
    assert "deepseek" not in data["missing"]
    assert not [r for r in data["sessions"] if r["engine"] == "deepseek"]


def test_deepseek_sessions_are_joined_when_published(tmp_path):
    deepseek = tmp_path / "deepseek"
    (deepseek / "sessions").mkdir(parents=True)
    (deepseek / "sessions" / "s1.json").write_text(
        json.dumps({"agent": "ds-1", "issue": 42, "branch": "ds-42", "state": "running", "at": "2026-09-21T16:00:00Z"}),
        encoding="utf-8",
    )
    app = _app(_view(tmp_path))
    _status, payload = _authed(app).get("/api/sessions/rows")
    rows = [r for r in payload["data"]["sessions"] if r["engine"] == "deepseek"]
    assert len(rows) == 1
    assert rows[0]["issue"] == 42
    assert rows[0]["state"] == "running"


# --------------------------------------------------------------------------- #
# Hermes/Paperclip: optional roots, appear only when present, never "not reporting"
# --------------------------------------------------------------------------- #
def test_hermes_and_paperclip_are_absent_by_default_and_never_named_missing(tmp_path):
    app = _app(_view(tmp_path))
    _status, payload = _authed(app).get("/api/sessions/rows")
    data = payload["data"]
    assert not [r for r in data["sessions"] if r["engine"] in ("hermes", "paperclip")]
    assert "hermes" not in data["missing"]
    assert "paperclip" not in data["missing"]


def test_hermes_rows_appear_once_its_root_exists(tmp_path):
    hermes = tmp_path / "hermes"
    (hermes / "sessions").mkdir(parents=True)
    (hermes / "sessions" / "h1.json").write_text(
        json.dumps({"agent": "hermes-1", "issue": 7, "branch": "hermes-7", "state": "running", "at": "2026-09-21T16:30:00Z"}),
        encoding="utf-8",
    )
    app = _app(_view(tmp_path, roots={"hermes": hermes}))
    _status, payload = _authed(app).get("/api/sessions/rows")
    rows = [r for r in payload["data"]["sessions"] if r["engine"] == "hermes"]
    assert len(rows) == 1
    assert rows[0]["issue"] == 7


# --------------------------------------------------------------------------- #
# Transport contract
# --------------------------------------------------------------------------- #
def test_routes_are_get_only_and_unknown_subviews_404(tmp_path):
    app = _app(_view(tmp_path))
    api = _authed(app)
    status, payload = api.post("/api/sessions/rows")
    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"
    status, _ = api.get("/api/sessions/nope")
    assert status == 404


def test_the_view_requires_a_console_session(tmp_path):
    app = _app(_view(tmp_path))
    api = login_as(app, "nobody@example.com", TENANT)
    api.cookies.clear()
    status, payload = api.get("/api/sessions/rows")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"
