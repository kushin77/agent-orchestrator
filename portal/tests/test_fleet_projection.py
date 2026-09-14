"""Fleet-state projection server + streaming API (issue #331).

The web single-pane-of-glass is the HTTP face of the same projection the terminal
dashboard draws: ``fleet/console.py snapshot()``. These tests pin the four
acceptance criteria of issue #331 against a *redirected* fleet runtime — the
heartbeat/slog/mailbox paths are pointed at a per-test tmp tree, so no test reads
or writes the live fleet's state and none needs a tmux, a TTY or the network.

Cannibalization is asserted, not assumed: the snapshot test compares the served
keys against the keys of the very ``console.snapshot()`` the server delegates to,
so a key added on one side and not the other fails.
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
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
from portal.server.app import StreamResponse, build_app  # noqa: E402
from portal.server.fleet import (  # noqa: E402
    FleetProjection,
    load_fleet_console,
    surface_enabled,
)
from portal.server.httpd import ConsoleServer  # noqa: E402
from portal.server.sso import SESSION_COOKIE  # noqa: E402

#: The exact key set ``fleet/console.py snapshot()`` publishes (issue #331 AC1).
EXPECTED_KEYS = {
    "repo",
    "head",
    "now",
    "uptime",
    "rungs",
    "orders",
    "dispatches",
    "claims",
    "waves",
    "closed",
    "events",
    "watchdog",
}


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
    # Snapshot's only non-file reads: HEAD (git), claims (a CLI), closed issues
    # (gh). Neutralized so the suite is offline and deterministic.
    monkeypatch.setattr(channel, "head_commit", lambda: "testsha")
    monkeypatch.setattr(console, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(console, "claims_snapshot", lambda: [])
    monkeypatch.setattr(console, "closed_issues", lambda *args, **kwargs: set())
    return console


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A projection backed by a per-test runtime tree, flag resolved OFF."""
    console = _redirect_runtime(tmp_path, monkeypatch)
    return FleetProjection(repo_root=REPO_ROOT, console=console)


def _app(fleet_projection):
    return build_app(sso=console_sso(), fleet_projection=fleet_projection)


def _authed_client(app):
    api = ApiClient(app)
    api.authenticate("root@platform.example.com", "acme")
    return api


def _frame_payload(frame: str) -> dict:
    assert frame.startswith("event: snapshot\n"), frame
    _, _, data = frame.partition("data: ")
    return json.loads(data.rsplit("\n\n", 1)[0])


# --- AC1: snapshot returns the console projection's exact keys ---------------


def test_snapshot_returns_exactly_the_console_projection(fleet):
    fleet.enabled = True
    app = _app(fleet)
    api = _authed_client(app)

    status, payload = api.get("/api/fleet/snapshot")
    assert status == 200
    served = payload["data"]

    # (a) the documented contract, and (b) no drift from the delegated source.
    assert set(served) == EXPECTED_KEYS
    assert set(served) == set(fleet.console.snapshot())
    assert set(served["rungs"]) == {"brain", "sister", "monitor"}


def test_snapshot_projects_a_live_heartbeat(fleet):
    fleet.enabled = True
    channel.HEARTBEAT.write_text(
        json.dumps({"state": "working", "commit": "cafe123", "started_at": "2026-09-13T00:00:00Z"}),
        encoding="utf-8",
    )
    app = _app(fleet)
    api = _authed_client(app)

    status, payload = api.get("/api/fleet/snapshot")
    assert status == 200
    sister = payload["data"]["rungs"]["sister"]
    assert sister["state"] == "working"
    assert sister["commit"] == "cafe123"


# --- AC3: events returns the last N parsed slog.jsonl records ----------------


def test_events_endpoint_returns_the_last_n_slog_records(fleet):
    fleet.enabled = True
    records = [{"n": i, "ts": f"2026-09-13T00:00:0{i}Z"} for i in range(5)]
    channel.SLOG.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    app = _app(fleet)
    api = _authed_client(app)

    status, payload = api.get("/api/fleet/events", query={"limit": "3"})
    assert status == 200
    assert payload["data"] == records[-3:]


def test_events_endpoint_skips_unparsable_records(fleet):
    fleet.enabled = True
    channel.SLOG.write_text(
        json.dumps({"n": 1}) + "\n" + "{not json\n" + json.dumps({"n": 2}) + "\n",
        encoding="utf-8",
    )
    app = _app(fleet)
    api = _authed_client(app)

    status, payload = api.get("/api/fleet/events", query={"limit": "10"})
    assert status == 200
    assert [record["n"] for record in payload["data"]] == [1, 2]


def test_events_limit_must_be_an_integer(fleet):
    fleet.enabled = True
    app = _app(fleet)
    api = _authed_client(app)

    status, payload = api.get("/api/fleet/events", query={"limit": "lots"})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


# --- AC2: the push channel emits a frame on a heartbeat change ---------------


def test_stream_pushes_a_frame_when_a_rung_heartbeat_changes(tmp_path, monkeypatch):
    console = _redirect_runtime(tmp_path, monkeypatch)

    def write_beat(commit: str) -> None:
        channel.HEARTBEAT.write_text(
            json.dumps(
                {"state": "working", "commit": commit, "started_at": "2026-09-13T00:00:00Z"}
            ),
            encoding="utf-8",
        )

    write_beat("aaaa")
    mutated = []

    def mutate_once(_seconds: float) -> None:
        if not mutated:
            mutated.append(True)
            write_beat("bbbb")

    fleet = FleetProjection(
        repo_root=REPO_ROOT,
        console=console,
        enabled=True,
        poll_interval=0.0,
        max_frames=2,
        sleep=mutate_once,
    )
    app = _app(fleet)
    api = _authed_client(app)

    response = app.handle("GET", "/api/fleet/stream", cookies=api.cookies)
    assert isinstance(response, StreamResponse)
    assert response.content_type.startswith("text/event-stream")

    frames = list(response.frames)
    assert len(frames) == 2, "one frame on connect, one on the heartbeat change"
    first, second = (_frame_payload(frame) for frame in frames)
    assert first["rungs"]["sister"]["commit"] == "aaaa"
    assert second["rungs"]["sister"]["commit"] == "bbbb"


def test_stream_is_quiet_while_the_heartbeat_is_unchanged(tmp_path, monkeypatch):
    """A stable heartbeat must not push a frame per poll (the noise guard)."""
    console = _redirect_runtime(tmp_path, monkeypatch)
    channel.HEARTBEAT.write_text(
        json.dumps({"state": "idle", "commit": "same", "started_at": "2026-09-13T00:00:00Z"}),
        encoding="utf-8",
    )
    fleet = FleetProjection(
        repo_root=REPO_ROOT,
        console=console,
        enabled=True,
        poll_interval=0.0,
        max_frames=5,
        max_polls=3,
        sleep=lambda _seconds: None,
    )
    app = _app(fleet)
    api = _authed_client(app)

    response = app.handle("GET", "/api/fleet/stream", cookies=api.cookies)
    frames = list(response.frames)
    assert len(frames) == 1, "the initial frame only; nothing changed afterwards"


# --- AC4: boots with no tmux, no TTY and no network egress -------------------


def test_projection_needs_no_tmux_no_tty_and_no_network(fleet, monkeypatch):
    fleet.enabled = True
    monkeypatch.delenv("TMUX", raising=False)  # no tmux session
    assert not sys.stdout.isatty(), "pytest captures stdout: this path has no TTY"

    class _NetworkForbidden(socket.socket):
        def __init__(self, *args, **kwargs):
            raise AssertionError("the fleet projection must not open a socket")

    monkeypatch.setattr(socket, "socket", _NetworkForbidden)

    app = _app(fleet)
    api = _authed_client(app)
    status, payload = api.get("/api/fleet/snapshot")
    assert status == 200, "a poisoned network must not break the offline projection"
    assert set(payload["data"]) == EXPECTED_KEYS


def test_http_transport_boots_and_streams_sse_over_loopback(fleet, monkeypatch):
    """The real stdlib transport serves snapshot and flushes an SSE frame."""
    fleet.enabled = True
    fleet.poll_interval = 0.05
    fleet.max_frames = 1  # one frame, then the stream ends (a bounded test)
    monkeypatch.delenv("TMUX", raising=False)

    app = _app(fleet)
    server = ConsoleServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = AUTH_GATE.mint("root@platform.example.com", "acme")
    cookie = f"{SESSION_COOKIE}={token}"
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request("GET", "/api/healthz")
        health = conn.getresponse()
        assert health.status == 200
        health.read()

        conn.request("GET", "/api/fleet/snapshot", headers={"Cookie": cookie})
        snapshot = conn.getresponse()
        body = json.loads(snapshot.read().decode("utf-8"))
        assert snapshot.status == 200
        assert set(body["data"]) == EXPECTED_KEYS

        conn.request("GET", "/api/fleet/stream", headers={"Cookie": cookie})
        stream = conn.getresponse()
        assert stream.getheader("Content-Type", "").startswith("text/event-stream")
        buffer = b""
        while b"\n\n" not in buffer:
            chunk = stream.read(1)
            if not chunk:
                break
            buffer += chunk
        assert b"event: snapshot" in buffer
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --- AC5: the surface ships feature-flag-gated OFF ---------------------------


def test_registry_declares_the_fleet_surface_off():
    import yaml

    document = yaml.safe_load(
        (REPO_ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(encoding="utf-8")
    )
    entry = document["surfaces"]["fleet_projection"]
    assert entry["default"] in (False, "off")
    assert entry["promoted"] is False


def test_flag_off_refuses_every_fleet_route(fleet):
    assert fleet.enabled is False, "the committed registry must leave the surface OFF"
    assert surface_enabled(REPO_ROOT) is False
    app = _app(fleet)
    api = _authed_client(app)

    for path in ("/api/fleet/snapshot", "/api/fleet/events", "/api/fleet/stream"):
        status, payload = api.get(path)
        assert status == 404, path
        assert payload["error"]["code"] == "feature_disabled", path


def test_flag_on_serves_the_surface(fleet):
    fleet.enabled = True
    app = _app(fleet)
    api = _authed_client(app)
    status, _ = api.get("/api/fleet/snapshot")
    assert status == 200


def test_surface_enabled_reads_the_registry_and_fails_closed(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("surfaces:\n  fleet_projection:\n    default: on\n", encoding="utf-8")
    assert surface_enabled(REPO_ROOT, registry_path=registry) is True
    registry.write_text("surfaces:\n  fleet_projection:\n    default: off\n", encoding="utf-8")
    assert surface_enabled(REPO_ROOT, registry_path=registry) is False
    assert surface_enabled(REPO_ROOT, registry_path=tmp_path / "absent.yaml") is False


def test_fleet_surface_is_get_only_and_unknown_surface_is_404(fleet):
    fleet.enabled = True
    app = _app(fleet)
    api = _authed_client(app)
    status, _ = api.post("/api/fleet/snapshot", body={})
    assert status == 405
    status, payload = api.get("/api/fleet/nonsense")
    assert status == 404
    assert payload["error"]["code"] == "not_found"
