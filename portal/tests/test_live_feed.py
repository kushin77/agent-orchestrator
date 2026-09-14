"""Live telemetry event feed — server half (issue #345).

The feed is the HTTP face of the telemetry the platform already records: the
gateway proxy's per-dispatch call records and the DLP lane's guardrail
verdicts, pushed over SSE as they land. These tests pin the issue's acceptance
against **real writers and real readers** — a call is recorded through the
gateway's own ``JsonlCallRecordSink``, a verdict through the DLP lane's own
``SecurityTelemetry``, and the frame that reaches the client is read back off
the SSE stream (never a hand-built dict):

* a routed call reaches a connected client within one poll of its landing,
  carrying provider/model/status/tokens/latency/cost;
* a guardrail verdict is carried by its call **only** on an exact identifier
  match, and stands alone otherwise (no invented correlation);
* an absent or empty store is never reported as "no traffic" (``NO_STORE`` and
  ``EMPTY`` are distinct states, each with an explicit note), and a malformed
  line is counted rather than dropped;
* the surface ships feature-flag-gated OFF, refused before authN, and a
  connection is scoped to the tenants the principal may read.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# The console imports its own lanes; the real writers below live in the
# gateway/guardrails packages, which are imported the way their own suites do.
for _package in (REPO_ROOT, REPO_ROOT / "gateway", REPO_ROOT / "guardrails"):
    if str(_package) not in sys.path:
        sys.path.insert(0, str(_package))

from conftest import AUTH_GATE, ApiClient, console_sso, login_as  # noqa: E402
from dlp.telemetry import SecurityTelemetry  # noqa: E402
from portal.server.app import StreamResponse, build_app  # noqa: E402
from portal.server.httpd import ConsoleServer  # noqa: E402
from portal.server.live_feed import (  # noqa: E402
    KIND_CALL,
    KIND_FEED,
    KIND_VERDICT,
    NOTE_EMPTY,
    NOTE_NO_STORE,
    SSE_EVENT,
    STATE_EMPTY,
    STATE_LIVE,
    STATE_NO_STORE,
    LiveFeed,
)
from portal.server.sso import SESSION_COOKIE  # noqa: E402
from proxy.model import GatewayCallRecord  # noqa: E402
from proxy.sinks import JsonlCallRecordSink  # noqa: E402

CALLS = "calls"
VERDICTS = "verdicts"


# --- real writers (the platform's own recording paths) -----------------------


def record_call(
    path: Path,
    *,
    request_id: str,
    tenant: str = "acme",
    agent: str = "agent-scout",
    provider: Optional[str] = "anthropic",
    model: Optional[str] = "claude-sonnet-4",
    outcome: str = "success",
    input_tokens: int = 120,
    output_tokens: int = 80,
    latency_ms: float = 812.5,
    cost_usd: float = 0.0031,
    ts: str = "2026-09-13T10:00:00Z",
) -> GatewayCallRecord:
    """Append one call record through the gateway proxy's own JSONL sink."""
    record = GatewayCallRecord(
        request_id=request_id,
        ts=ts,
        tenant_id=tenant,
        agent_id=agent,
        task_type="research",
        outcome=outcome,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        estimated_cost_usd=cost_usd,
        budget_action="allow",
        attempts=1,
    )
    JsonlCallRecordSink(path).record(record)
    return record


def record_verdict(
    path: Path,
    *,
    event_type: str,
    tenant: str = "acme",
    agent: str = "",
    severity: str = "info",
    verdict: Optional[str] = None,
    call_id: Optional[str] = None,
):
    """Emit one guardrail decision through the DLP lane's own telemetry sink."""
    detail = {}
    if verdict is not None:
        detail["verdict"] = verdict
    if call_id is not None:
        detail["call_id"] = call_id
    return SecurityTelemetry(path=str(path)).emit(
        event_type, tenant_id=tenant, agent_id=agent, severity=severity, **detail
    )


# --- fixtures / helpers -----------------------------------------------------


@pytest.fixture
def stores(tmp_path):
    """The two live telemetry stores, redirected into a per-test tree."""
    return {
        CALLS: tmp_path / "calls.jsonl",
        VERDICTS: tmp_path / "security.jsonl",
    }


def make_feed(stores, **kwargs) -> LiveFeed:
    return LiveFeed(
        repo_root=REPO_ROOT,
        enabled=True,
        calls_store_path=stores[CALLS],
        verdicts_store_path=stores[VERDICTS],
        poll_interval=0.0,
        **kwargs,
    )


def _app(feed: LiveFeed):
    return build_app(sso=console_sso(), live_feed=feed)


def _authed_client(app) -> ApiClient:
    api = ApiClient(app)
    api.authenticate("root@platform.example.com", "acme")
    return api


def _frame_payload(frame: str) -> dict:
    assert frame.startswith(f"event: {SSE_EVENT}\n"), frame
    _, _, data = frame.partition("data: ")
    return json.loads(data.rsplit("\n\n", 1)[0])


def _stream_frames(app, api, query=None):
    response = app.handle(
        "GET", "/api/telemetry/stream", query=query or {}, cookies=api.cookies
    )
    assert isinstance(response, StreamResponse)
    assert response.content_type.startswith("text/event-stream")
    return [_frame_payload(frame) for frame in response.frames]


# --- AC: a routed call reaches the client with its stamp ---------------------


def test_stream_pushes_a_routed_call_with_tokens_cost_status_and_latency(stores):
    """A call recorded mid-connection is pushed within a poll (issue #345 AC).

    The first call is recorded **before** the connection (the replay window),
    the second **during** it (via the sleep hook the stream calls between
    polls). Both frames are read back off the SSE channel and asserted on the
    values the gateway actually stamped.
    """
    record_call(
        stores[CALLS],
        request_id="req_backfill_1",
        ts="2026-09-13T10:00:00Z",
        cost_usd=0.0010,
    )
    writes = []

    def write_during_stream(_seconds: float) -> None:
        if writes:
            return
        writes.append(True)
        record_call(
            stores[CALLS],
            request_id="req_live_1",
            ts="2026-09-13T10:00:05Z",
            outcome="success",
            input_tokens=400,
            output_tokens=250,
            latency_ms=1234.5,
            cost_usd=0.0234,
        )

    feed = make_feed(stores, max_polls=3, sleep=write_during_stream)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    header = frames[0]
    assert header["kind"] == KIND_FEED
    assert header["state"] == STATE_LIVE
    assert header["records"] == 1, "the header counts what the store held on connect"

    backfilled = [f for f in frames if f["kind"] == KIND_CALL and f["backfill"]]
    live = [f for f in frames if f["kind"] == KIND_CALL and not f["backfill"]]
    assert [f["id"] for f in backfilled] == ["req_backfill_1"]
    assert [f["id"] for f in live] == ["req_live_1"], "the call mid-stream was pushed"

    frame = live[0]
    assert frame["tenantId"] == "acme"
    assert frame["agentId"] == "agent-scout"
    assert frame["provider"] == "anthropic"
    assert frame["model"] == "claude-sonnet-4"
    assert frame["status"] == "success"
    assert frame["tokens"] == {"input": 400, "output": 250, "total": 650}
    assert frame["latencyMs"] == 1234.5
    assert frame["cost"] == {"usd": 0.0234, "estimated": True}
    assert frame["budgetAction"] == "allow"
    assert frame["verdicts"] == []
    assert frame["source"]["store"] == CALLS
    assert frame["source"]["path"] == str(stores[CALLS])
    assert frame["source"]["line"] == 2, "the frame names the line it came from"
    assert [f["seq"] for f in frames] == list(range(len(frames)))


def test_a_non_call_status_is_carried_verbatim(stores):
    """A blocked/refused dispatch is a frame too: the feed never hides a failure."""
    record_call(stores[CALLS], request_id="req_blocked", outcome="blocked")
    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    call = next(f for f in frames if f["kind"] == KIND_CALL)
    assert call["status"] == "blocked"


# --- AC: verdicts, and only on an exact identifier match ---------------------


def test_a_verdict_is_attached_on_an_exact_match_and_stands_alone_otherwise(stores):
    """A guardrail verdict rides its call only when the identifiers match.

    ``req_paired`` has a verdict whose ``call_id`` equals its ``requestId``;
    the second verdict names a call that is not in the store. The first rides
    the call frame, the second is pushed as its own verdict frame — the feed
    does not guess an owner.
    """
    record_call(stores[CALLS], request_id="req_paired", ts="2026-09-13T10:00:01Z")
    record_verdict(
        stores[VERDICTS],
        event_type="injection_attempt",
        severity="critical",
        verdict="blocked",
        call_id="req_paired",
        agent="agent-scout",
    )
    record_verdict(
        stores[VERDICTS],
        event_type="egress_denied",
        severity="warning",
        call_id="req_never_recorded",
    )

    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    call_frames = [f for f in frames if f["kind"] == KIND_CALL]
    verdict_frames = [f for f in frames if f["kind"] == KIND_VERDICT]

    assert len(call_frames) == 1
    assert [v["verdict"] for v in call_frames[0]["verdicts"]] == ["blocked"]
    assert call_frames[0]["verdicts"][0]["severity"] == "critical"
    assert call_frames[0]["verdicts"][0]["source"]["store"] == VERDICTS

    assert [f["eventType"] for f in verdict_frames] == ["egress_denied"]
    assert verdict_frames[0]["id"] == "req_never_recorded"
    assert verdict_frames[0]["verdict"] == "egress_denied", "the event type is the verdict"
    assert verdict_frames[0]["backfill"] is True


def test_a_sent_call_verdict_uses_the_event_type_when_no_verdict_is_stamped(stores):
    """``call_sent`` carries no ``detail.verdict``: the type *is* the decision."""
    record_verdict(stores[VERDICTS], event_type="call_sent", call_id="req_sent")
    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    verdict = next(f for f in frames if f["kind"] == KIND_VERDICT)
    assert verdict["verdict"] == "call_sent"
    assert verdict["severity"] == "info"

    # A client that asks for no history still gets the header first.
    fresh = make_feed(stores, max_polls=1)
    fresh_app = _app(fresh)
    fresh_frames = _stream_frames(
        fresh_app, _authed_client(fresh_app), query={"replay": "0"}
    )
    assert fresh_frames[0]["kind"] == KIND_FEED
    assert fresh_frames[0]["replayed"] == 0
    assert [f["kind"] for f in fresh_frames[1:]] == []


# --- AC: an empty feed is not "no traffic" -----------------------------------


def test_absent_store_and_empty_store_are_different_states(stores):
    """``NO_STORE`` (nothing to read) is not ``EMPTY`` (a live store with none)."""
    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    header = frames[0]
    assert header["state"] == STATE_NO_STORE
    assert header["note"] == NOTE_NO_STORE
    assert header["records"] == 0
    assert [s["present"] for s in header["stores"]] == [False, False]

    stores[CALLS].parent.mkdir(parents=True, exist_ok=True)
    stores[CALLS].write_text("", encoding="utf-8")
    stores[VERDICTS].write_text("", encoding="utf-8")
    empty_feed = make_feed(stores, max_polls=1)
    empty_app = _app(empty_feed)
    frames = _stream_frames(empty_app, _authed_client(empty_app))
    header = frames[0]
    assert header["state"] == STATE_EMPTY
    assert header["note"] == NOTE_EMPTY
    assert header["records"] == 0
    assert [s["present"] for s in header["stores"]] == [True, True]


def test_a_malformed_line_is_counted_never_silently_dropped(stores):
    """A store-health signal a client can see, not a silent skip."""
    stores[CALLS].parent.mkdir(parents=True, exist_ok=True)
    record_call(stores[CALLS], request_id="req_ok")
    with open(stores[CALLS], "a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
        handle.write('["not", "an", "object"]\n')
    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    header = frames[0]
    assert header["state"] == STATE_LIVE
    assert header["records"] == 1
    assert header["malformed"] == 2, "both bad lines are reported"
    call_frames = [f for f in frames if f["kind"] == KIND_CALL]
    assert [f["id"] for f in call_frames] == ["req_ok"]


def test_a_partial_trailing_line_is_not_parsed_until_its_newline_lands(stores):
    """A write in flight is not a record (and is not counted malformed)."""
    stores[CALLS].parent.mkdir(parents=True, exist_ok=True)
    record_call(stores[CALLS], request_id="req_complete")
    with open(stores[CALLS], "a", encoding="utf-8") as handle:
        handle.write('{"kind": "usage", "tenantId": "acme"')  # torn write, no newline

    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    header = frames[0]
    assert header["records"] == 1, "the torn line is not a record"
    assert header["malformed"] == 0, "and it is not an unreadable one either"


# --- AC: a store that vanishes is announced ----------------------------------


def test_a_store_that_disappears_under_a_live_connection_is_announced(stores):
    """A dead feed must not keep looking live."""
    record_call(stores[CALLS], request_id="req_gone_soon")
    removed = []

    def remove_then_continue(_seconds: float) -> None:
        if removed:
            return
        removed.append(True)
        stores[CALLS].unlink(missing_ok=True)
        stores[VERDICTS].unlink(missing_ok=True)

    feed = make_feed(stores, max_polls=3, sleep=remove_then_continue)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api)
    headers = [f for f in frames if f["kind"] == KIND_FEED]
    assert len(headers) == 2, "the header is re-emitted when the stores go away"
    assert headers[0]["state"] == STATE_LIVE
    assert headers[1]["state"] == STATE_NO_STORE
    assert headers[1]["note"] == NOTE_NO_STORE


# --- AC: the surface ships flag-gated OFF, before authN ----------------------


def test_the_surface_is_refused_before_authN_while_the_flag_is_off(stores):
    """An unpromoted surface is invisible — not merely protected."""
    default_feed = LiveFeed(  # enabled=None: the registry decides
        repo_root=REPO_ROOT,
        calls_store_path=stores[CALLS],
        verdicts_store_path=stores[VERDICTS],
    )
    assert default_feed.enabled is False, "the registry declares the surface off"
    app = build_app(sso=console_sso(), live_feed=default_feed)

    response = app.handle("GET", "/api/telemetry/stream")  # no session cookie at all
    assert response.status == 404
    assert response.payload["error"]["code"] == "feature_disabled"

    authed = app.handle(
        "GET",
        "/api/telemetry/stream",
        cookies={SESSION_COOKIE: AUTH_GATE.mint("root@platform.example.com", "acme")},
    )
    assert authed.status == 404, "a valid session does not open a gated surface"
    assert authed.payload["error"]["code"] == "feature_disabled"


def test_registry_declares_the_live_feed_surface_off():
    import yaml

    document = yaml.safe_load(
        (REPO_ROOT / "infra" / "feature-flags" / "registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = document["surfaces"]["telemetry_live_feed"]
    assert entry["default"] in (False, "off")
    assert entry["promoted"] is False
    assert entry["service"] == "portal"


# --- AC: the connection is scoped to the tenants the principal may read ------


def test_a_super_admin_sees_every_tenant_and_a_scoped_owner_only_their_own(stores):
    """Frames and the header's counts both come from the in-scope subset."""
    record_call(stores[CALLS], request_id="req_acme", tenant="acme")
    record_call(stores[CALLS], request_id="req_globex", tenant="globex")
    feed = make_feed(stores, max_polls=1)
    app = _app(feed)

    root_frames = _stream_frames(app, _authed_client(app))
    root_header = root_frames[0]
    assert root_header["scope"] == {"all": True, "tenants": None}
    assert root_header["records"] == 2
    assert {f["id"] for f in root_frames if f["kind"] == KIND_CALL} == {
        "req_acme",
        "req_globex",
    }

    scoped_feed = make_feed(stores, max_polls=1)
    scoped_app = _app(scoped_feed)
    carol = login_as(scoped_app, "carol@globex.example.com", "globex")
    scoped_frames = _stream_frames(scoped_app, carol)
    scoped_header = scoped_frames[0]
    assert scoped_header["scope"] == {"all": False, "tenants": ["globex"]}
    assert scoped_header["records"] == 1, "another tenant's volume is not disclosed"
    assert [f["id"] for f in scoped_frames if f["kind"] == KIND_CALL] == ["req_globex"]

    unscoped_feed = make_feed(stores, max_polls=1)
    unscoped_app = _app(unscoped_feed)
    alice = login_as(unscoped_app, "alice@acme.example.com", "acme")  # acme only
    frames = _stream_frames(unscoped_app, alice)
    assert [f["id"] for f in frames if f["kind"] == KIND_CALL] == ["req_acme"]


def test_a_role_without_event_read_is_refused_rather_than_shown_nothing(stores):
    """A refusal, never an empty feed that would read as "no traffic"."""
    record_call(stores[CALLS], request_id="req_acme", tenant="acme")
    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    erin = login_as(app, "erin@acme.example.com", "acme")  # agent-operator: no event:read

    status, payload = erin.get("/api/telemetry/recent")
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"


# --- the hydration endpoint and the real transport ---------------------------


def test_recent_returns_the_same_window_a_stream_would_replay(stores):
    """The hydration call the shell makes before subscribing."""
    for index in range(3):
        record_call(
            stores[CALLS],
            request_id=f"req_{index}",
            ts=f"2026-09-13T10:00:0{index}Z",
        )
    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    status, payload = api.get("/api/telemetry/recent", query={"replay": "2"})
    assert status == 200
    data = payload["data"]
    assert data["feed"]["state"] == STATE_LIVE
    assert data["feed"]["replayed"] == 2
    assert [f["id"] for f in data["frames"]] == ["req_1", "req_2"], "the newest two"
    assert [f["seq"] for f in data["frames"]] == [0, 1]

    streamed = _stream_frames(app, api, query={"replay": "2"})
    assert [f["id"] for f in streamed if f["kind"] == KIND_CALL] == ["req_1", "req_2"]


def test_the_replay_window_is_a_count_of_events_across_both_stores(stores):
    """``replay=N`` is N events: the merged window is capped, not N per store."""
    for index in range(3):
        record_call(
            stores[CALLS],
            request_id=f"req_{index}",
            ts=f"2026-09-13T10:00:0{index}Z",
        )
    for index in range(3):
        record_verdict(stores[VERDICTS], event_type="call_sent", call_id=f"req_{index}")

    feed = make_feed(stores, max_polls=1)
    app = _app(feed)
    api = _authed_client(app)

    frames = _stream_frames(app, api, query={"replay": "2"})
    assert frames[0]["kind"] == KIND_FEED
    assert frames[0]["replayed"] == 2
    assert len(frames) == 3, "header + exactly two events (never two per store)"


def test_the_http_transport_streams_frames_over_loopback(stores):
    """The real stdlib transport flushes SSE frames as they are produced."""
    record_call(stores[CALLS], request_id="req_transport")
    feed = make_feed(stores, max_frames=2)  # header + the replayed call, then end
    app = _app(feed)
    server = ConsoleServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = AUTH_GATE.mint("root@platform.example.com", "acme")
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=5
        )
        conn.request(
            "GET", "/api/telemetry/stream", headers={"Cookie": f"{SESSION_COOKIE}={token}"}
        )
        stream = conn.getresponse()
        assert stream.status == 200
        assert stream.getheader("Content-Type", "").startswith("text/event-stream")
        buffer = b""
        while buffer.count(b"\n\n") < 2:
            chunk = stream.read(1)
            if not chunk:
                break
            buffer += chunk
        text = buffer.decode("utf-8")
        assert f"event: {SSE_EVENT}" in text
        frames = [
            json.loads(block.partition("data: ")[2])
            for block in text.split("\n\n")
            if block.startswith(f"event: {SSE_EVENT}")
        ]
        assert frames[0]["kind"] == KIND_FEED
        assert frames[1]["kind"] == KIND_CALL
        assert frames[1]["tokens"]["total"] == 200
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
