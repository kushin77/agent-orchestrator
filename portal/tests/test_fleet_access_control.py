"""Access control + tenant scoping for the streamed fleet dashboard (issue #333).

The fleet projection (issue #331) is the HTTP face of the terminal dashboard. A
browser surface has a threat model a local tmux pane does not, so every read on
``/api/fleet/*`` must be scoped to the caller and the cross-org view must be an
explicit administrative capability. These tests pin the issue's four acceptance
criteria against a *redirected* fleet runtime — the heartbeat/slog/mailbox paths
are pointed at a per-test tmp tree, so no test reads or writes the live fleet's
state and none needs a tmux, a TTY or the network.

The tests deliberately exercise the two-gate doctrine rather than a status code
alone: a tenant principal is refused a foreign row by the **scope** gate even
when its role holds the read permission, and the roll-up is refused to an org
owner that holds ``fleet:rollup`` because its scope does not reach the platform
org. A test asserting only "403" would pass with a single-gate implementation
that could still leak across tenants under a different principal.

Row ownership is derived from live sources rather than invented fixtures: an
agent named by a row is resolved through the console's own org directory
(``portal/server/state.py``), and a lane through the registry's persona cards.
The two seeded tenants (``acme``, ``globex``) exist in that directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
FLEET_PKG = REPO_ROOT / "fleet"
if str(FLEET_PKG) not in sys.path:
    sys.path.insert(0, str(FLEET_PKG))

import channel  # noqa: E402  (fleet/channel.py — the runtime path source)
from conftest import AUTH_GATE, ApiClient, console_sso  # noqa: E402
from entitlements.errors import UnknownPlanError  # noqa: E402
from entitlements.model import EntitlementProfile  # noqa: E402
from portal.server.app import StreamResponse, build_app  # noqa: E402
from portal.server.authz import Principal  # noqa: E402
from portal.server.fleet import FleetProjection, load_fleet_console  # noqa: E402
from portal.server.fleet_authz import (  # noqa: E402
    PLATFORM_ORG,
    PLATFORM_PLAN,
    READ_PERMISSION,
    ROLLUP_FEATURE,
    ROLLUP_PERMISSION,
    ROW_SPECS,
    SNAPSHOT_SECTIONS,
    FleetAuthorizer,
    FleetDenied,
    OrgIndex,
    RowOwner,
)
from portal.server.sso import SESSION_COOKIE  # noqa: E402

#: The two tenants the cross-read assertion is written over. Both are in the
#: console's own org directory, and each owns agents named by the seeded rows.
TENANT_A = "acme"
TENANT_B = "globex"
#: An agent of TENANT_A and of TENANT_B, per the console org directory.
AGENT_A = "coder-1"
AGENT_B = "docbot"
#: A lane no console org owns (the persona card declares `tenant: platform`),
#: so a row naming it stays platform-owned and is invisible to every tenant.
PLATFORM_LANE = "hermes"

SUPER_ADMIN = "root@platform.example.com"
OWNER_A = "alice@acme.example.com"
ADMIN_A = "bob@acme.example.com"
OPERATOR_A = "erin@acme.example.com"
OWNER_B = "carol@globex.example.com"


# --- a redirected, offline fleet runtime ------------------------------------


def _redirect_runtime(tmp_path, monkeypatch):
    """Point every fleet runtime path the console reads at ``tmp_path``."""
    console = load_fleet_console(REPO_ROOT)
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    monkeypatch.setattr(channel, "HEARTBEAT", tmp_path / "sister.heartbeat.json")
    monkeypatch.setattr(channel, "BRAIN_HEARTBEAT", tmp_path / "brain.heartbeat.json")
    monkeypatch.setattr(channel, "BRAIN_INBOX", tmp_path / "brain" / "inbox")
    monkeypatch.setattr(channel, "BRAIN_SENT", tmp_path / "brain" / "sent")
    monkeypatch.setattr(channel, "BRAIN_OUTBOX", tmp_path / "brain" / "outbox")
    monkeypatch.setattr(console, "FLEET_DIR", tmp_path)
    monkeypatch.setattr(channel, "head_commit", lambda: "testsha")
    monkeypatch.setattr(console, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(console, "claims_snapshot", lambda: [])
    monkeypatch.setattr(console, "closed_issues", lambda *args, **kwargs: set())
    return console


def _message(message_id: str, *, agent: str, lane: str, ts: str) -> dict:
    """One steering message naming the agent (and lane) it is about.

    The shape follows ``fleet/schema/message.schema.json``: ``task`` carries the
    lane, and the agent is the roster id the console's org directory declares.
    """
    return {
        "id": message_id,
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "ts": ts,
        "agent": agent,
        "task": {"kind": "work", "issue": 1, "lane": lane},
    }


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A projection over a per-test runtime tree carrying a two-tenant board."""
    console = _redirect_runtime(tmp_path, monkeypatch)
    outbox = tmp_path / "brain" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "m1.json").write_text(
        json.dumps(
            _message("m1", agent=AGENT_A, lane="code-authoring", ts="2026-09-13T00:00:01Z")
        ),
        encoding="utf-8",
    )
    (outbox / "m2.json").write_text(
        json.dumps(
            _message("m2", agent=AGENT_B, lane="knowledge", ts="2026-09-13T00:00:02Z")
        ),
        encoding="utf-8",
    )
    channel.SLOG.write_text(
        "".join(
            json.dumps(record) + "\n"
            for record in (
                {"ts": "2026-09-13T00:00:03Z", "event": "dispatch", "agent": AGENT_A},
                {"ts": "2026-09-13T00:00:04Z", "event": "dispatch", "agent": AGENT_B},
                {"ts": "2026-09-13T00:00:05Z", "event": "rung", "lane": PLATFORM_LANE},
                {"ts": "2026-09-13T00:00:06Z", "event": "boot"},
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        console,
        "claims_snapshot",
        lambda: [f"issue-1 held by copilot (lane {PLATFORM_LANE})"],
    )
    projection = FleetProjection(repo_root=REPO_ROOT, console=console)
    projection.enabled = True
    return projection


@pytest.fixture
def app(fleet):
    return build_app(sso=console_sso(), fleet_projection=fleet)


def _client(app, email: str, tenant_id: str) -> ApiClient:
    api = ApiClient(app)
    api.authenticate(email, tenant_id)
    return api


def _principal(app, email: str) -> Principal:
    """The console principal the app builds for a verified session.

    Mirrors ``ConsoleApplication._require_session``: the identity is the token
    subject's email, the role comes from the local allowlist (never the token's
    own claim), and the org bindings come from the console's org directory.
    """
    super_admin = email == SUPER_ADMIN
    return Principal(
        email=email,
        role="root_admin" if super_admin else "user",
        super_admin=super_admin,
        bindings=[
            (binding.tenant_id, binding.role)
            for binding in app.state.roles_for(email)
        ],
    )


def _snapshot(api: ApiClient) -> dict:
    status, payload = api.get("/api/fleet/snapshot")
    assert status == 200, payload
    return payload["data"]


# ============================================================================
# AC1 — an unauthenticated GET /api/fleet/snapshot returns 401
# ============================================================================


def test_unauthenticated_snapshot_is_refused_with_401(app):
    api = ApiClient(app)
    status, payload = api.get("/api/fleet/snapshot")
    assert status == 401
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unauthorized"
    assert payload["data"] is None


def test_every_fleet_route_needs_a_session(app):
    """Not just snapshot: the read, the history and the push channel too."""
    api = ApiClient(app)
    for path in (
        "/api/fleet/snapshot",
        "/api/fleet/events",
        "/api/fleet/stream",
        "/api/fleet/rollup",
    ):
        status, payload = api.get(path)
        assert status == 401, path
        assert payload["error"]["code"] == "unauthorized", path


def test_a_forged_session_is_refused(monkeypatch, app):
    """A token this console's JWKS mirror does not vouch for is not a session."""
    api = ApiClient(app)
    api.present("not.a.jwt")
    status, payload = api.get("/api/fleet/snapshot")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_a_session_from_another_signing_key_is_refused(app):
    """A well-formed token signed by a different key is still no session."""
    from conftest import FakeAuthGate

    foreign = FakeAuthGate()
    api = ApiClient(app)
    api.present(foreign.mint(SUPER_ADMIN, TENANT_A))
    status, _ = api.get("/api/fleet/snapshot")
    assert status == 401


def test_flag_off_hides_the_surface_from_everyone(fleet):
    """The flag gate runs BEFORE authN, so an unpromoted surface is invisible
    rather than distinguishable by an authentication probe."""
    fleet.enabled = False
    app = build_app(sso=console_sso(), fleet_projection=fleet)
    unauthenticated = ApiClient(app)
    authenticated = _client(app, SUPER_ADMIN, TENANT_A)
    for api in (unauthenticated, authenticated):
        status, payload = api.get("/api/fleet/snapshot")
        assert status == 404
        assert payload["error"]["code"] == "feature_disabled"


# ============================================================================
# AC2 — a tenant principal receives only its own org's rows
# ============================================================================


def test_two_tenants_each_receive_only_their_own_rows(app):
    owner_a = _snapshot(_client(app, OWNER_A, TENANT_A))
    owner_b = _snapshot(_client(app, OWNER_B, TENANT_B))

    # Each tenant receives its own rows...
    assert [row["agent"] for row in owner_a["dispatches"]] == [AGENT_A]
    assert [row["agent"] for row in owner_b["dispatches"]] == [AGENT_B]
    assert [row["agent"] for row in owner_a["events"]] == [AGENT_A]
    assert [row["agent"] for row in owner_b["events"]] == [AGENT_B]

    # ...and never the other tenant's.
    assert AGENT_B not in json.dumps(owner_a)
    assert AGENT_A not in json.dumps(owner_b)


def test_the_cross_read_is_refused_at_the_scope_gate(app):
    """The refusal is the scope gate, not a filter that happened to drop rows.

    A tenant owner holds ``fleet:read``, so a single-gate implementation that
    checked the permission alone would allow the foreign read. The decision for
    the other tenant's node must therefore name ``scope`` as its reason.
    """
    authorizer = app.fleet_authz
    owner_a = _principal(app, OWNER_A)
    decision = authorizer.row_decision(owner_a, authorizer.index.owner_of_agent(AGENT_B))
    assert decision.denied
    assert decision.reason == "scope", decision
    assert decision.allowed is False


def test_platform_rows_are_invisible_to_a_tenant(app):
    """The runtime's own rows (rungs, steering queue, watchdog, ledger) are the
    platform org's; a tenant sees the dashboard's shape and none of its data."""
    owner_a = _snapshot(_client(app, OWNER_A, TENANT_A))
    assert owner_a["uptime"] is None
    assert owner_a["rungs"] == {}
    assert owner_a["orders"] == {}
    assert owner_a["closed"] == []
    assert owner_a["watchdog"] == []
    # the platform-owned lane the claim line names stays invisible too
    assert owner_a["claims"] == []


def test_a_team_scoped_principal_reaches_its_team_and_not_the_org(app):
    """``agent-operator`` is a team-level role: it reaches its team's rows, never
    an org-level row (rbac.resolve defines exactly this coverage)."""
    authorizer = app.fleet_authz
    operator = _principal(app, OPERATOR_A)
    team_node = authorizer.index.owner_of_agent(AGENT_A)
    assert team_node is not None and team_node.team_id is not None
    assert authorizer.may_read(operator, team_node) is True
    assert authorizer.may_read(operator, RowOwner(TENANT_A)) is False


def test_the_platform_operator_sees_the_whole_board(app):
    """The scoped view is not a special case: the operator's own scope is the
    platform org, which owns the runtime's rows, so its scoped view is complete."""
    operator = _snapshot(_client(app, SUPER_ADMIN, TENANT_A))
    assert sorted(row["agent"] for row in operator["dispatches"]) == sorted(
        [AGENT_A, AGENT_B]
    )
    assert operator["rungs"] and set(operator["rungs"]) == {"brain", "sister", "monitor"}
    assert SNAPSHOT_SECTIONS == tuple(spec.name for spec in ROW_SPECS)
    assert set(operator) == set(SNAPSHOT_SECTIONS)


def test_the_scoped_view_keeps_the_dashboards_shape(app):
    """A tenant's view carries every documented section (empty where filtered),
    so the dashboard's contract does not vary with the caller."""
    full = app.fleet.console.snapshot()
    scoped = _snapshot(_client(app, OWNER_A, TENANT_A))
    assert set(scoped) == set(full) == set(SNAPSHOT_SECTIONS)


def test_events_limit_applies_to_the_callers_own_records(app):
    """``?limit=N`` must return the caller's last N, not N rows of which some
    belong to another org."""
    api = _client(app, OWNER_B, TENANT_B)
    status, payload = api.get("/api/fleet/events", query={"limit": "10"})
    assert status == 200
    records = payload["data"]
    assert [record["agent"] for record in records] == [AGENT_B]
    assert len(records) == 1


def test_the_push_channel_carries_only_the_callers_own_rows(app):
    app.fleet.max_frames = 1
    api = _client(app, OWNER_A, TENANT_A)
    response = app.handle("GET", "/api/fleet/stream", cookies=api.cookies)
    assert isinstance(response, StreamResponse)
    frames = list(response.frames)
    assert len(frames) == 1
    _, _, data = frames[0].partition("data: ")
    pushed = json.loads(data.rsplit("\n\n", 1)[0])
    assert [row["agent"] for row in pushed["dispatches"]] == [AGENT_A]
    assert AGENT_B not in frames[0]


def test_a_refused_stream_is_a_json_error_not_an_open_stream(app):
    """The surface gate runs eagerly, so a refusal is the control plane's error
    envelope rather than a 200 stream that never carries a frame."""
    api = ApiClient(app)  # no session
    response = app.handle("GET", "/api/fleet/stream", cookies=api.cookies)
    assert not isinstance(response, StreamResponse)
    assert response.status == 401


def test_row_ownership_is_derived_from_the_live_sources(app):
    """The attribution is not a fixture: the agent ids come from the console's
    own org directory and the lane from the registry's persona card."""
    index = app.fleet_authz.index
    assert index.owner_of_agent(AGENT_A) == RowOwner(TENANT_A, f"{TENANT_A}/platform")
    assert index.owner_of_agent(AGENT_B) == RowOwner(TENANT_B, f"{TENANT_B}/platform")
    assert index.owner_of_lane(PLATFORM_LANE) == RowOwner(PLATFORM_ORG)
    assert index.owner_of_agent("no-such-agent") is None


def test_an_unattributed_row_belongs_to_the_platform_never_to_a_tenant():
    """An unknown reference must not be guessed into an org that resembles it."""
    index = OrgIndex(known_orgs=[TENANT_A, PLATFORM_ORG])
    assert index.attribute({"tenant": "not-a-tenant"}) is None
    assert index.attribute({"agent": "ghost"}) is None
    assert index.attribute({"lane": "ghost"}) is None
    assert index.attribute({"org": TENANT_A}) == RowOwner(TENANT_A)
    assert index.attribute({"task": {"lane": "ghost"}}) is None


def test_a_declared_entity_cannot_be_reassigned_to_another_org():
    index = OrgIndex(known_orgs=[TENANT_A, TENANT_B])
    index.declare(TENANT_A, lanes=["shared-lane"])
    with pytest.raises(ValueError, match="already owned"):
        index.declare(TENANT_B, lanes=["shared-lane"])


def test_the_specific_agent_outranks_the_lane_it_shares(app):
    """A row naming both an agent and a lane belongs to the agent's org."""
    authorizer = app.fleet_authz
    row = {"agent": AGENT_B, "task": {"lane": PLATFORM_LANE}}
    assert authorizer.index.attribute(row) == authorizer.index.owner_of_agent(AGENT_B)


# ============================================================================
# AC3 — the admin roll-up endpoint is refused for a non-admin principal
# ============================================================================


def test_the_rollup_is_served_to_the_platform_operator(app):
    api = _client(app, SUPER_ADMIN, TENANT_A)
    status, payload = api.get("/api/fleet/rollup")
    assert status == 200
    data = payload["data"]
    assert set(data) == {"snapshot", "orgs", "totals"}
    # the roll-up is the cross-org accounting, so both tenants appear
    counted = {row["orgId"] for row in data["orgs"]}
    assert {TENANT_A, TENANT_B, PLATFORM_ORG} <= counted
    assert data["totals"]["rows"] == sum(row["rows"] for row in data["orgs"])
    assert set(data["snapshot"]) == set(SNAPSHOT_SECTIONS)


@pytest.mark.parametrize(
    ("email", "tenant_id", "role"),
    (
        (OWNER_A, TENANT_A, "owner"),
        (ADMIN_A, TENANT_A, "admin"),
        (OPERATOR_A, TENANT_A, "agent-operator"),
        (OWNER_B, TENANT_B, "owner"),
    ),
)
def test_the_rollup_is_refused_for_every_non_platform_principal(
    app, email, tenant_id, role
):
    """The role matrix: no org role reaches the cross-org view."""
    authorizer = app.fleet_authz
    decision = authorizer.rollup_decision(_principal(app, email))
    assert decision.denied, f"{role} must not reach the roll-up"
    assert decision.reason == "scope", decision
    status, payload = _client(app, email, tenant_id).get("/api/fleet/rollup")
    assert status == 403
    assert payload["error"]["code"] == "scope_denied"


def test_an_org_owner_holds_the_permission_but_not_the_scope(app):
    """The two-gate doctrine, asserted directly: the org owner's role carries
    ``fleet:rollup``, so only the scope gate can be what refuses it."""
    authorizer = app.fleet_authz
    owner = _principal(app, OWNER_A)
    held = authorizer.store.find_role_by_key(TENANT_A, "owner")
    assert ROLLUP_PERMISSION in held.permissions
    assert READ_PERMISSION in held.permissions
    # ...and it is still refused, because a permission is never usable outside
    # a resolved scope.
    assert authorizer.rollup_decision(owner).denied
    assert authorizer.row_decision(owner, RowOwner(PLATFORM_ORG)).reason == "scope"


def test_the_rollup_is_refused_when_the_platform_plan_does_not_entitle_it(app):
    """The entitlement gate: the same platform operator, on a plan whose
    entitlements do not unlock the surface, is refused - and the refusal names
    the entitlement gate rather than scope or permission."""
    unentitled = FleetAuthorizer(
        state=app.state, repo_root=REPO_ROOT, platform_plan="free"
    )
    decision = unentitled.rollup_decision(_principal(app, SUPER_ADMIN))
    assert decision.denied
    assert decision.reason == "entitlement", decision
    assert decision.code == "not_entitled"


def test_an_unknown_platform_plan_is_refused_at_boot(app):
    """The plan is assigned through the entitlements lane's audited API, which
    refuses a plan the catalog does not define - so this surface can never come
    up claiming an entitlement nobody granted."""
    with pytest.raises(UnknownPlanError):
        FleetAuthorizer(
            state=app.state, repo_root=REPO_ROOT, platform_plan="no-such-plan"
        )


def test_a_plan_key_that_leaves_the_catalog_is_refused_fail_closed(app):
    """Drift: the assignment stands but the catalog no longer defines its plan,
    which denies rather than degrading into a grant (entitlements gate 1)."""
    store = app.fleet_authz.entitlements
    current = store.profile(PLATFORM_ORG)
    store.save_profile(
        EntitlementProfile(
            org_id=PLATFORM_ORG,
            plan_key="retired-plan",
            subscription_status=current.subscription_status,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )
    )
    decision = app.fleet_authz.rollup_decision(_principal(app, SUPER_ADMIN))
    assert decision.denied
    assert decision.reason == "subscription"
    assert decision.code == "unknown_plan"


def test_a_principal_in_no_org_is_refused_the_surface(app):
    """Out of scope and short a permission are reported separately: a principal
    that can reach no node is a scope refusal, not a permission one."""
    authorizer = app.fleet_authz
    stranger = Principal(email="stranger@nowhere.example.com", role="user", bindings=[])
    refusal = authorizer.surface_refusal(stranger)
    assert refusal is not None
    assert refusal.status == 403
    assert refusal.code == "scope_denied"


def test_the_rollup_feature_is_declared_and_enabled_on_the_platform_plan(app):
    """The entitlement the roll-up consumes is real catalog data, not a name
    invented at the call site."""
    plan = app.fleet_authz.catalog.plan(PLATFORM_PLAN)
    assert plan is not None
    entry = plan.entitlement(ROLLUP_FEATURE)
    assert entry is not None and entry.enabled
    assert set(app.fleet_authz.catalog.feature(ROLLUP_FEATURE).grants) == {
        READ_PERMISSION,
        ROLLUP_PERMISSION,
    }


def test_the_rollup_refusal_is_a_mapped_transport_error(app):
    """``FleetDenied`` is the module's transport-free refusal; the app maps it
    onto its own envelope with the control plane's code."""
    authorizer = app.fleet_authz
    with pytest.raises(FleetDenied) as excinfo:
        authorizer.rollup(_principal(app, OWNER_A), app.fleet)
    assert excinfo.value.status == 403
    assert excinfo.value.code == "scope_denied"


# ============================================================================
# AC4 — no secret or token is ever logged or rendered
# ============================================================================


def _leaks(text: str, token: str) -> bool:
    """Whether ``token`` (or any of its segments) reached ``text``.

    A JWT is three dot-separated segments; a leak of any one of them is a leak
    of material that must never leave the cookie jar, so each is checked
    separately rather than only the whole string.
    """
    if not token:
        return False
    if token in text:
        return True
    return any(part and part in text for part in token.split("."))


def test_the_token_leak_detector_is_not_vacuous():
    """A negative control is only evidence if it can fail: provoke the detector
    with a deliberate leak and require it to find one."""
    sample = ".".join(("header", "payload", "signature"))
    assert _leaks(f"cookie: {sample}", sample) is True
    assert _leaks("nothing to see", sample) is False
    # a single segment leaking is still a leak
    assert _leaks("saw payload", sample) is True


def test_a_resolved_request_renders_and_logs_no_token(app, capfd):
    api = _client(app, OWNER_A, TENANT_A)
    token = api.cookies[SESSION_COOKIE]
    status, payload = api.get("/api/fleet/snapshot")
    assert status == 200
    captured = capfd.readouterr()
    assert not _leaks(json.dumps(payload), token)
    assert not _leaks(captured.out + captured.err, token)
    # and the token is never echoed back as a cookie or a header of its own
    assert not any("token" in name.lower() for name, _ in api.last_headers)


def test_a_refused_request_renders_and_logs_no_token(app, capfd):
    """The refusal path is where a credential leaks by accident: the error text
    names the problem, never the material."""
    api = ApiClient(app)
    forged = AUTH_GATE.sign({"sub": "x", "purpose": "wrong-purpose"})
    api.present(forged)
    status, payload = api.get("/api/fleet/snapshot")
    assert status == 401
    captured = capfd.readouterr()
    assert not _leaks(json.dumps(payload), forged)
    assert not _leaks(captured.out + captured.err, forged)


def test_the_cookie_is_not_rendered_on_any_fleet_route(app):
    api = _client(app, SUPER_ADMIN, TENANT_A)
    token = api.cookies[SESSION_COOKIE]
    for path in ("/api/fleet/snapshot", "/api/fleet/events", "/api/fleet/rollup"):
        status, payload = api.get(path)
        assert status in (200, 403), path
        assert not _leaks(json.dumps(payload), token), path


def test_the_stream_frames_carry_no_token(app):
    app.fleet.max_frames = 1
    api = _client(app, OWNER_A, TENANT_A)
    token = api.cookies[SESSION_COOKIE]
    response = app.handle("GET", "/api/fleet/stream", cookies=api.cookies)
    frames = list(response.frames)
    assert not _leaks("".join(frames), token)
