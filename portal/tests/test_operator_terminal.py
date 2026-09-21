"""Operator terminal surface tests (issue #774).

The browser IT-terminal behind the SSO session. The acceptance criteria are
about *behaviour*, so the proof is driven, not asserted by inspection:

* the route + view are flag-gated BEFORE AuthN — while `surfaces.operator_terminal`
  is off, `/console` and `/views/console.html` answer ``404 feature_disabled`` so
  an unauthenticated probe cannot even tell the surface exists (the chat /
  live-bridge precedent);
* the one link reuses the existing session — unauthenticated `/console` reaches
  `/auth/login`, an authenticated one opens the view, and the view is a
  self-contained offline frame;
* the steer half is the closed vocabulary — the verbs the panel lists *are* the
  registry's exposed set (consumed, never restated), an out-of-vocabulary action
  is refused ``422``, a caller without the control capability is refused ``403``
  (with no audit), and an allowed steer lands on the audit rail.

Nothing here reads or writes the live fleet: the steer tests inject a
recording lever and a spy ledger, so no verb is actually delivered.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import ApiClient, console_sso  # noqa: E402

from portal.server import control_api  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.control_api import (  # noqa: E402
    PLATFORM_ORG,
    EffectRecord,
    LeverResult,
    RemoteControl,
    Vocabulary,
    install,
)
from portal.server.fleet_authz import SUBJECT_USER  # noqa: E402
from portal.server.sso import AUTH_GATE_LOGIN_PATH, SESSION_COOKIE  # noqa: E402

STATIC = REPO_ROOT / "portal" / "static"
VIEW = STATIC / "views" / "console.html"
CLIENT = STATIC / "js" / "operator.js"
REGISTRY = REPO_ROOT / "control-plane" / "control" / "verbs.yaml"

SUPER_ADMIN_EMAIL = "root@platform.example.com"
SCOPED_USER_EMAIL = "alice@acme.example.com"


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------
class RecordingLever:
    """A lever double: records the row it was handed, never spawns anything."""

    def __init__(self, *, exit_code: int = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, row, args):
        self.calls.append((row.lever, tuple(args)))
        return LeverResult(
            argv=(row.source, row.local, *args),
            exit_code=self.exit_code,
            stdout=self.output,
            stderr="",
        )


class SpyLedger:
    """Records every seam call so a route cannot bypass the record path."""

    def __init__(self) -> None:
        self.begun: list[str] = []
        self.finished: list[str] = []
        self.ended: list[str] = []

    def begin(self, command):
        self.begun.append(command.verb)
        return None

    def finish(self, command, record):
        self.finished.append(command.verb)

    def end(self, command):
        self.ended.append(command.verb)


def _terminal_app(*, operator_terminal: bool = True, **kwargs):
    return build_app(
        sso=console_sso(), operator_terminal_enabled=operator_terminal, **kwargs
    )


def _steer_app():
    """An app with the operator terminal AND the control family both ON."""
    app = _terminal_app()
    install(
        app,
        RemoteControl(
            app=app,
            enabled=True,
            lever=RecordingLever(),
            commands=SpyLedger(),
        ),
    )
    return app


def _authed(app, email=SUPER_ADMIN_EMAIL, tenant="acme"):
    client = ApiClient(app)
    client.authenticate(email, tenant)
    return client


# ---------------------------------------------------------------------------
# the flag gate (BEFORE AuthN)
# ---------------------------------------------------------------------------
#: The three probes below force the flag OFF rather than reading the committed
#: declaration. `surfaces.operator_terminal` is now `promoted: true`, so an app
#: built with the default flags serves the surface and a probe that read the
#: default would assert nothing — it was measured red on `origin/master` for
#: exactly that reason (issue #1523 recorded the measurement). The flag is
#: still a real probe when forced: a route that ignored it answers 200/302 here.
FLAG_OFF = {"operator_terminal": False}


def test_flag_off_console_route_is_invisible():
    app = _terminal_app(**FLAG_OFF)
    response = app.handle("GET", "/console", cookies={})
    assert response.status == 404
    assert response.payload["error"]["code"] == "feature_disabled"


def test_flag_off_view_is_invisible():
    app = _terminal_app(**FLAG_OFF)
    response = app.handle("GET", "/views/console.html", cookies={})
    assert response.status == 404
    assert response.payload["error"]["code"] == "feature_disabled"


def test_flag_off_script_is_invisible():
    app = _terminal_app(**FLAG_OFF)
    response = app.handle("GET", "/js/operator.js", cookies={})
    assert response.status == 404
    assert response.payload["error"]["code"] == "feature_disabled"


def test_flag_off_is_invisible_to_an_authenticated_caller_too():
    app = _terminal_app(operator_terminal=False)
    client = _authed(app)
    status, payload = client.get("/console")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"


# ---------------------------------------------------------------------------
# the one link reuses the session
# ---------------------------------------------------------------------------
def test_unauthenticated_console_reaches_the_login_gate():
    app = _terminal_app()
    client = ApiClient(app)
    status, _payload = client.get("/console")
    assert status == 302
    assert client.header("Location") == AUTH_GATE_LOGIN_PATH


def test_authenticated_console_opens_the_view():
    app = _terminal_app()
    client = _authed(app)
    status, _payload = client.get("/console")
    assert status == 302
    assert client.header("Location") == "/views/console.html"


def test_authenticated_view_is_served():
    app = _terminal_app()
    client = _authed(app)
    status, payload = client.get("/views/console.html")
    assert status == 200
    assert b"<html" in payload
    assert b"operator" in payload.lower()


# ---------------------------------------------------------------------------
# the view is a self-contained offline frame
# ---------------------------------------------------------------------------
def test_view_is_self_contained_and_offline():
    html = VIEW.read_text(encoding="utf-8")
    assert 'href="/design-tokens/tokens.css"' in html
    assert 'href="/css/console.css"' in html
    assert 'src="/js/api.js"' in html
    assert 'src="/js/operator.js"' in html
    assert "<html" in html and "<body" in html
    for marker in ("http://", "https://", "//fonts.googleapis", "cdn."):
        assert marker not in html


def test_script_references_the_real_endpoints():
    js = CLIENT.read_text(encoding="utf-8")
    # The read half is the fleet projection, the steer half the control family.
    assert "/api/fleet/snapshot" in js
    assert "/api/control/fleet/verbs" in js
    # The pure model is exposed for the offline gate and the test suite.
    assert "window.OT" in js
    for marker in ("http://", "https://", "//fonts.googleapis", "cdn."):
        assert marker not in js


# ---------------------------------------------------------------------------
# the steer half is the closed vocabulary
# ---------------------------------------------------------------------------
def test_steer_lists_the_closed_vocabulary():
    app = _steer_app()
    client = _authed(app)
    status, payload = client.post("/api/control/fleet/verbs", {})
    assert status == 200
    content = payload["data"]["content"]
    served = {row["id"] for row in content["verbs"]}
    registry = Vocabulary.load(REGISTRY)
    declared = set(registry.verbs.keys())
    # The panel renders exactly the registry's declared set — never a hard-coded
    # list, so a verb the registry withholds or adds cannot silently drift. The
    # exposed flag (per row) is what decides whether a steer button appears.
    assert served == declared
    assert "fleet.status" in served
    assert "fleet.pause" in served
    # Every row carries the fields the panel renders (effect class + capability
    # + exposure), so the panel cannot render a verb it cannot also name.
    for row in content["verbs"]:
        assert row["effectClass"]
        assert row["capability"]
        assert "exposed" in row


def test_steer_out_of_vocabulary_is_refused():
    app = _steer_app()
    client = _authed(app)
    status, payload = client.post("/api/control/fleet/frobnicate", {})
    assert status == 422
    assert payload["error"]["code"] == "unknown_verb"


def test_steer_refused_without_capability_and_not_audited():
    app = _steer_app()
    ledger = app.control_surface.commands
    client = _authed(app, email=SCOPED_USER_EMAIL, tenant="acme")
    status, payload = client.post("/api/control/fleet/pause", {"args": []})
    assert status == 403
    assert payload["error"]["code"] in ("permission_denied", "scope_denied")
    # A refusal writes nothing on the audit rail.
    assert ledger.finished == []


def test_allowed_steer_is_audited():
    app = _steer_app()
    ledger = app.control_surface.commands
    client = _authed(app)
    status, payload = client.post("/api/control/fleet/pause", {"args": []})
    assert status == 200
    assert "fleet.pause" in ledger.finished
    assert "fleet.pause" in ledger.begun
    assert "fleet.pause" in ledger.ended


# ---------------------------------------------------------------------------
# the panel is the CALLER's catalogue, not the registry's (issue #1523)
# ---------------------------------------------------------------------------
#
# The closed vocabulary is one declaration for every caller: it says which verbs
# exist, not which ones the operator reading it may run. Rendered unfiltered, the
# panel put a steer button for every exposed verb — irreversible ones included —
# in front of a caller whose capabilities reached none of them, so every click was
# a 403 the panel could have predicted. What is measured here is that the panel's
# "what may I run" answer and the dispatch path's answer are THE SAME answer:
# one decision, read twice, never a second implementation of the rule.

#: A platform preset role that grants a PARTIAL capability set (`fleet:read`):
#: enough to run the fleet/channel/recover reads, not enough for `board` or
#: `closure`. The middle regime is the one that catches a filter that is really
#: an all-or-nothing switch, which the two extremes would not.
PARTIAL_ROLE = "admin"


def _bind(app, email: str, role: str) -> None:
    """Bind ``email`` to a platform preset role — the seam the console's own
    super-admin rule uses (``FleetAuthorizer._bind_super_admin``), so the test
    builds its principals out of the authority's own store rather than a stub."""
    store = app.fleet_authz.store
    role_id = app.fleet_authz._role_id(store, PLATFORM_ORG, role)
    assert role_id is not None, f"{role} is not a role at {PLATFORM_ORG}"
    store.add_binding(PLATFORM_ORG, email.lower(), SUBJECT_USER, role_id)


def _permitted(client) -> list[str] | None:
    status, payload = client.get("/api/console/me")
    assert status == 200, payload
    return payload["data"]["controlVerbs"]


def test_me_reports_the_callers_own_control_verbs():
    """The panel's catalogue: this caller's verbs, spanning several families."""
    app = _steer_app()
    permitted = _permitted(_authed(app))
    assert isinstance(permitted, list)
    families = {verb.split(".", 1)[0] for verb in permitted}
    # The acceptance asks for at least two families BEYOND the fleet-steer one;
    # a super-admin reaches every exposed family, so this is the strongest form.
    assert families >= {"fleet", "board", "channel"}, sorted(families)
    assert "board.status" in permitted, "a non-fleet verb the panel must offer"


def test_the_permitted_set_and_the_dispatch_path_agree_verb_by_verb():
    """ONE decision, read twice — the property that makes the panel honest.

    For every exposed verb, membership in the caller-scoped catalogue is compared
    against what the plane actually answers when that verb is dispatched. A
    filter that withheld a verb the caller *could* run (a hidden capability) and a
    filter that offered one it could not (a predicted 403) both fail here, by
    verb id.
    """
    registry = Vocabulary.load(REGISTRY)
    exposed = [row for row in registry.verbs.values() if row.exposed]
    assert len(exposed) > 20, "the vocabulary has shrunk; re-measure this proof"

    for email, role in (("root@platform.example.com", None),
                        ("partial@acme.example.com", PARTIAL_ROLE)):
        app = _steer_app()
        if role is not None:
            _bind(app, email, role)
        client = _authed(app, email=email)
        permitted = set(_permitted(client) or [])
        mismatches = []
        for row in exposed:
            status, _payload = client.post(f"/api/control/{row.family}/{row.action}", {})
            ran = status == 200
            if ran != (row.id in permitted):
                mismatches.append((row.id, status, row.id in permitted))
        assert not mismatches, f"{email}: catalogue != dispatch for {mismatches}"


def test_a_caller_with_no_capability_sees_none_and_is_still_refused():
    """The negative control: what the panel withholds, the plane refuses."""
    app = _steer_app()
    client = _authed(app, email=SCOPED_USER_EMAIL, tenant="acme")
    assert _permitted(client) == []
    # The verb the panel would have offered before this change.
    status, payload = client.post("/api/control/board/status", {})
    assert status == 403
    assert payload["error"]["code"] in ("permission_denied", "scope_denied")


def test_a_partial_caller_sees_its_own_families_and_not_the_others():
    """`board`/`closure` are absent for a caller whose capabilities stop short."""
    app = _steer_app()
    _bind(app, "partial@acme.example.com", PARTIAL_ROLE)
    permitted = _permitted(_authed(app, email="partial@acme.example.com"))
    assert permitted, "the partial role must still reach some verbs"
    families = {verb.split(".", 1)[0] for verb in permitted}
    assert families & {"channel", "recover"}, sorted(families)
    assert "board" not in families, sorted(families)
    assert not [verb for verb in permitted if verb.startswith("board.")]
    # ...and the irreducible one it must never be offered is the irreversible
    # closure verb, which no partial role reaches.
    assert "closure.retire" not in permitted


def test_no_permitted_set_is_reported_while_the_control_surface_is_off():
    """`None` is "cannot be assessed", never `[]` — the client fails closed on it.

    The two are different claims: "this caller may run nothing" is a verdict, and
    an unpromoted surface is the absence of one. A client that conflated them
    would render an empty panel that reads as an idle vocabulary.
    """
    app = _terminal_app()
    install(app, RemoteControl(app=app, enabled=False, lever=RecordingLever(),
                               commands=SpyLedger()))
    assert _permitted(_authed(app)) is None

