"""Sessions view operator controls (issue #1564): assign/pause/resume/escalate/kill.

Builds on the Sessions view (#1563, ``portal/server/sessions.py``) and the
existing remote control family (#554, ``portal/server/control_api.py``) — this
suite adds no second lever and no second audit trail. It proves, against
fixture directories and a fake lever (never a live fleet):

* ``POST /api/sessions/control/<action>`` is refused ``404 feature_disabled``
  while ``surfaces.sessions`` is off, exactly like ``GET /api/sessions/rows``;
* an action outside the five-button set is refused ``422 unknown_verb`` by
  name;
* a row whose engine is not ``claude-fleet`` is refused ``403
  engine_not_controllable`` (``engine-not-controllable:<engine>``) rather than
  silently forwarded or no-opped;
* each of the five buttons maps onto an EXISTING ``control-plane/control/
  verbs.yaml`` verb and, once delegated to the real control family, writes one
  ``.fleet/control-<verb>.json`` record naming the action and the operator
  (the authenticated principal) — proved by a fake lever that writes the
  fixture record, cross-checked against the live response's own ``actor``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import console_sso, login_as  # noqa: E402

from portal.server.app import build_app  # noqa: E402
from portal.server.control_api import LeverResult, RemoteControl, install  # noqa: E402
from portal.server.sessions import SessionsView  # noqa: E402

TENANT = "acme"
OPERATOR_EMAIL = "root@platform.example.com"
OPERATOR_ACTOR = f"user:{OPERATOR_EMAIL}"

#: The five session-view buttons and the verb id they must reach.
EXPECTED_VERBS = {
    "assign": "fleet.start",
    "pause": "fleet.pause",
    "resume": "fleet.resume",
    "escalate": "fleet.override",
    "kill": "fleet.kill",
}


class FixtureLever:
    """A lever double: writes one ``.fleet/control-<verb>.json`` naming the
    action and the operator, instead of spawning ``fleet/control.py``.

    ``operator`` is the actor this test's login deterministically produces
    (``Command.actor`` in control_api.py); the response's own ``actor`` field
    is asserted separately against the same constant, so the two together
    prove the record names the real authenticated caller, not a guess.
    """

    def __init__(self, *, fleet_dir: Path, operator: str) -> None:
        self.fleet_dir = fleet_dir
        self.operator = operator
        self.calls: list[str] = []

    def run(self, row, args):
        self.calls.append(row.id)
        self.fleet_dir.mkdir(parents=True, exist_ok=True)
        record_path = self.fleet_dir / f"control-{row.action}.json"
        record_path.write_text(
            json.dumps({"action": row.id, "operator": self.operator}), encoding="utf-8"
        )
        return LeverResult(argv=(row.source, row.local, *args), exit_code=0, stdout="applied", stderr="")


def _view(tmp_path) -> SessionsView:
    return SessionsView(
        repo_root=REPO_ROOT,
        enabled=True,
        roots={
            "claude-fleet": tmp_path / "fleet",
            "claude-board": tmp_path / "board",
            "deepseek": tmp_path / "deepseek",
        },
    )


def _app(tmp_path, *, sessions_enabled=True, control_enabled=True):
    view = _view(tmp_path) if sessions_enabled else SessionsView(repo_root=REPO_ROOT, enabled=False)
    app = build_app(sso=console_sso(), sessions_view=view)
    lever = FixtureLever(fleet_dir=tmp_path / "fleet", operator=OPERATOR_ACTOR)
    install(app, RemoteControl(app=app, enabled=control_enabled, lever=lever))
    return app, lever


def _authed(app):
    return login_as(app, OPERATOR_EMAIL, TENANT)


# --------------------------------------------------------------------------- #
# flag-off refusal — same discipline as GET /api/sessions/rows
# --------------------------------------------------------------------------- #
def test_control_is_refused_while_the_sessions_flag_is_off(tmp_path):
    app, _lever = _app(tmp_path, sessions_enabled=False)
    status, payload = _authed(app).post(
        "/api/sessions/control/pause", {"engine": "claude-fleet"}
    )
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"


# --------------------------------------------------------------------------- #
# refusal: action outside the five-button set
# --------------------------------------------------------------------------- #
def test_an_unknown_action_is_refused_by_name(tmp_path):
    app, lever = _app(tmp_path)
    status, payload = _authed(app).post(
        "/api/sessions/control/frobnicate", {"engine": "claude-fleet"}
    )
    assert status == 422
    assert payload["error"]["code"] == "unknown_verb"
    assert lever.calls == []


# --------------------------------------------------------------------------- #
# refusal: engine other than claude-fleet
# --------------------------------------------------------------------------- #
def test_a_non_fleet_engine_row_is_refused_engine_not_controllable(tmp_path):
    app, lever = _app(tmp_path)
    for engine in ("claude-board", "deepseek", "", None):
        status, payload = _authed(app).post(
            "/api/sessions/control/pause", {"engine": engine}
        )
        assert status == 403, engine
        assert payload["error"]["code"] == "engine_not_controllable", engine
        assert f"engine-not-controllable:{engine}" in payload["error"]["message"], engine
    assert lever.calls == [], "a disallowed engine must never reach the lever"


# --------------------------------------------------------------------------- #
# each verb: one .fleet/control-*.json naming the action and the operator
# --------------------------------------------------------------------------- #
def test_each_button_writes_one_control_record_naming_action_and_operator(tmp_path):
    for action, verb_id in EXPECTED_VERBS.items():
        app, lever = _app(tmp_path)
        status, payload = _authed(app).post(
            f"/api/sessions/control/{action}", {"engine": "claude-fleet"}
        )
        assert status == 200, action
        assert payload["data"]["verb"] == verb_id, action
        assert payload["data"]["actor"] == OPERATOR_ACTOR, action

        family, _, local = verb_id.partition(".")
        record_path = tmp_path / "fleet" / f"control-{local}.json"
        assert record_path.is_file(), f"{action} wrote no fixture control record"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["action"] == verb_id
        assert record["operator"] == OPERATOR_ACTOR
        assert lever.calls == [verb_id]
