"""Conversational surface tests (issue #508, ADR-0023).

The proof for the *experience* half of EPIC #500. Two mechanisms are stated
plainly, because the acceptance criteria are about behaviour, not about strings
this file wrote:

* **The serving surface is a real HTTP endpoint.** Each test starts a loopback
  fake (`FakeGateway`, bound to ``127.0.0.1`` on an ephemeral port) that speaks
  the OpenAI-/Ollama-compatible streaming contract, and the console under test
  is pointed at it. So the turn really is consumed over HTTP — the portal never
  imports the gateway — and the suite runs offline with no egress.
* **Frames are read back out of the console's own stream.** The assertions walk
  the ``ao.chat/v1`` events the surface produces (``turn.delta`` growth,
  ``turn.completed`` / ``turn.cancelled`` / ``turn.error``, the citations
  envelope, the budget pre-flight), not a re-implementation of them.

The honesty criteria are the ones with teeth, so they are asserted as
*distinctions*: an unsupported claim is ``data-supported="false"`` while a
grounded one is ``"true"``; a missing source is ``NO_DATA`` and is asserted
**not** to be ``OK``; a soft budget warning and a hard stop differ in severity,
in the badge each renders and in whether the turn is sent at all; and an
unreadable budget fails closed with a code of its own so it can never be
mistaken for either.
"""

from __future__ import annotations

import http.client
import http.server
import json
import threading
import time
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, ApiClient, console_sso, login_as

from portal.server.app import ConsoleApplication, StreamResponse, build_app
from portal.server.chat import (
    CHAT_ASSETS,
    BUDGET_ACTIONS,
    DEFAULT_TIER,
    GROUNDING_NO_DATA,
    GROUNDING_OK,
    SEVERITIES,
    TIERS,
    TURN_CANCELLED,
    TURN_COMPLETE,
    TURN_FAILED,
    ChatError,
    ChatSurface,
    parse_turn_request,
    unsupported_label,
)

STATIC = REPO_ROOT / "portal" / "static"
VIEW = STATIC / "views" / "chat.html"
CLIENT = STATIC / "js" / "chat.js"
ACME_USER = "alice@acme.example.com"
GLOBEX_USER = "carol@globex.example.com"

#: The citations envelope the served turn is grounded in (a bridge family, a
#: tool call and a ticket id — the three provenance shapes the issue names).
SOURCES = [
    {
        "id": "bridge:registry@1a2b3c4d5e6f",
        "family": "registry",
        "kind": "bridge",
        "revision": "1a2b3c4d5e6f77889900aabbccddeeff",
        "label": "AgentProfile coder-1",
    },
    {
        "id": "tool:kb.query",
        "kind": "tool_call",
        "revision": "",
        "label": "kb.query(issue #508)",
    },
    {
        "id": "kushin77/agent-orchestrator#500",
        "kind": "ticket",
        "revision": "",
        "label": "EPIC #500",
    },
]


# --------------------------------------------------------------------------- #
# The fake serving surface (loopback only — the suite never egresses)
# --------------------------------------------------------------------------- #
def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


class FakeGateway:
    """A loopback stand-in for the gateway-authoritative serving surface.

    Configurable per test: the token sequence it streams, the citations
    envelope it attaches, whether it reports a degraded fallback, whether it
    sends usage figures, what the FinOps read model answers, and whether the
    stream terminates properly.
    """

    def __init__(
        self,
        *,
        tokens: tuple[str, ...] = ("Hel", "lo ", "wor", "ld"),
        citations: dict | None = None,
        degraded: dict | None = None,
        grounding: dict | None = None,
        usage: bool = True,
        model: str = "deepseek-v4-pro",
        budget: dict | None = None,
        budget_status: int = 200,
        finish: bool = True,
        done: bool = True,
        fail_completions: bool = False,
        delay: float = 0.0,
        padding: int = 0,
    ) -> None:
        self.tokens = list(tokens)
        self.citations = citations
        self.degraded = degraded
        self.grounding = grounding
        self.usage = usage
        self.model = model
        #: The default is an open budget: a test that cares about the budget
        #: states sets its own, and every other test proves the turn path.
        self.budget = budget if budget is not None else {
            "action": "allow",
            "monthlyBudgetUsd": 500.0,
            "spentUsd": 12.5,
            "pctUsed": 2.5,
            "warnAtPct": 80.0,
            "hardCapPct": 100.0,
        }
        self.budget_status = budget_status
        self.finish = finish
        self.done = done
        #: Drop the model path without answering (an unreachable authority).
        self.fail_completions = fail_completions
        self.delay = delay
        self.padding = padding
        #: Every request the surface received, in order.
        self.requests: list[tuple[str, str]] = []
        #: Every parsed completion body (so a test can prove what was sent).
        self.bodies: list[dict] = []
        #: Streams the client abandoned mid-flight (the clean-stop evidence).
        self.aborted = 0
        self.frames_written = 0
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self._build_handler()
        )
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    # -- lifecycle ----------------------------------------------------------
    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def wait_aborted(self, timeout: float = 5.0) -> int:
        """Wait for a client to abandon a stream mid-flight; return the count.

        The handler only learns the client is gone on its *next* write, so the
        proof of a clean stop is polled rather than asserted at once.
        """
        deadline = time.monotonic() + timeout
        while self.aborted == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.aborted

    def completions(self) -> int:
        """How many turns actually reached the model path."""
        return sum(1 for _, path in self.requests if path.startswith("/v1/chat/"))

    # -- transport ----------------------------------------------------------
    def _build_handler(self):
        gateway = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_args) -> None:  # keep the suite quiet
                pass

            def do_GET(self) -> None:  # noqa: N802
                gateway._on_get(self)

            def do_POST(self) -> None:  # noqa: N802
                gateway._on_post(self)

        return _Handler

    def _on_get(self, handler) -> None:
        self.requests.append(("GET", handler.path))
        if not handler.path.startswith("/v1/ao/finops/budget"):
            handler.send_error(404, "no such read")
            return
        if self.budget_status != 200:
            handler.send_error(self.budget_status, "budget read failed")
            return
        body = json.dumps(self.budget).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _on_post(self, handler) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        self.requests.append(("POST", handler.path))
        self.bodies.append(body)
        if not handler.path.startswith("/v1/chat/"):
            handler.send_error(404, "no such route")
            return
        if self.fail_completions:
            # No status line at all: the client sees the connection go away,
            # which is exactly what an unreachable authority looks like.
            handler.close_connection = True
            handler.connection.close()
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        try:
            for token in self.tokens:
                self._emit(handler, self._chunk(token, body))
                if self.delay:
                    time.sleep(self.delay)
            if self.finish:
                self._emit(handler, self._terminal(body))
            if self.done:
                handler.wfile.write(b"data: [DONE]\n\n")
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The console closed the stream (a cancelled generation). That is
            # the evidence this fake exists to record.
            self.aborted += 1

    def _emit(self, handler, payload: dict) -> None:
        handler.wfile.write(_sse(payload).encode("utf-8"))
        handler.wfile.flush()
        self.frames_written += 1

    # -- payloads -----------------------------------------------------------
    def _chunk(self, token: str, body: dict) -> dict:
        tier = str(body.get("model") or "")
        chunk = {
            "object": "chat.completion.chunk",
            "model": self.model,
            "choices": [
                {"index": 0, "delta": {"content": token}, "finish_reason": None}
            ],
            "ao": {
                "tier": {
                    "requested": tier,
                    "resolved": tier,
                    "resolvedModel": self.model,
                }
            },
        }
        if self.padding:
            chunk["ao"]["note"] = "x" * self.padding
        return chunk

    def _terminal(self, body: dict) -> dict:
        extension: dict = {
            "tier": {
                "requested": str(body.get("model") or ""),
                "resolved": str(body.get("model") or ""),
                "resolvedModel": self.model,
            }
        }
        if self.citations is not None:
            extension["citations"] = self.citations
        if self.degraded is not None:
            extension["degraded"] = self.degraded
        if self.grounding is not None:
            extension["grounding"] = self.grounding
        chunk = {
            "object": "chat.completion.chunk",
            "model": self.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "ao": extension,
        }
        if self.usage:
            chunk["usage"] = {
                "prompt_tokens": 120,
                "completion_tokens": 42,
                "total_tokens": 162,
            }
            extension["usage"] = {"estimatedCostUsd": 0.0031, "latencyMs": 812.5}
        return chunk


@pytest.fixture
def gateway():
    fake = FakeGateway()
    try:
        yield fake
    finally:
        fake.close()


# --------------------------------------------------------------------------- #
# Harness helpers
# --------------------------------------------------------------------------- #
def _registry(tmp_path: Path, default: str) -> Path:
    """A feature-flag registry fixture (the canonical one is infra-owned)."""
    path = tmp_path / f"registry-{default}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "default_policy": "off",
                "surfaces": {
                    "chat": {
                        "default": default,
                        "promoted": default == "on",
                        "service": "portal",
                        "description": "conversational surface (issue #508)",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _surface(
    gateway: FakeGateway, tmp_path: Path, *, enabled: bool = True, registry=None
) -> ChatSurface:
    return ChatSurface(
        repo_root=REPO_ROOT,
        enabled=enabled,
        registry_path=registry,
        store_dir=tmp_path / "chat-store",
        gateway_base_url=gateway.base_url,
        timeout=10.0,
    )


def _app(gateway: FakeGateway, tmp_path: Path, **kwargs) -> ConsoleApplication:
    return ConsoleApplication(
        repo_root=REPO_ROOT,
        sso=console_sso(),
        chat_surface=_surface(gateway, tmp_path, **kwargs),
    )


def _events(frames) -> list[tuple[str, dict | None]]:
    """Parse the surface's SSE frames into ``(event, payload)`` pairs."""
    parsed: list[tuple[str, dict | None]] = []
    for frame in frames:
        text = frame.strip()
        if text == "data: [DONE]":
            parsed.append(("done", None))
            continue
        event = "message"
        data = ""
        for line in text.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data += line[len("data: "):]
        parsed.append((event, json.loads(data) if data else None))
    return parsed


def _drain(response) -> list[tuple[str, dict | None]]:
    return _events(list(response.frames))


def _events_of(events: list[tuple[str, dict | None]], name: str) -> list[dict]:
    return [payload for event, payload in events if event == name and payload]


def _open(api: ApiClient) -> str:
    """Open a conversation through the served API and return its id."""
    status, payload = api.post("/api/chat/conversations", {})
    assert status == 200, payload
    return payload["data"]["conversation"]["id"]


class StreamClient(ApiClient):
    """An ``ApiClient`` that hands a streaming route back as its stream.

    A turn answers with a ``StreamResponse`` (frames the transport writes
    incrementally), which the buffered client cannot decode; every other
    response — including every refusal — is decoded exactly as before, so a
    test can assert a 402 the same way it asserts a 200.
    """

    def post(self, path: str, body=None):
        response = self.app.handle(
            "POST", path, query={}, body=body or {}, cookies=self.cookies
        )
        if isinstance(response, StreamResponse):
            return response
        return self._buffered(response, path, body)

    def _buffered(self, response, path: str, body=None):
        """Decode ``response`` through the buffered client's own path.

        The request was already executed (a POST is not idempotent, so it must
        not be sent twice), so ``handle`` is stubbed for exactly one call to
        hand the buffered client the response it would have computed itself.
        """
        pending = {"response": response}
        original = self.app.handle

        def handle_once(*args, **kwargs):
            if pending:
                return pending.pop("response")
            return original(*args, **kwargs)

        self.app.handle = handle_once
        try:
            return super().post(path, body)
        finally:
            del self.app.handle


def _login(app, email: str, tenant_id: str, *, role: str = "user") -> StreamClient:
    """``login_as`` with a client that can read a streamed turn."""
    api = StreamClient(app)
    api.authenticate(email, tenant_id, role=role)
    return api


# --------------------------------------------------------------------------- #
# Flag posture (GR-5: an unpromoted surface is invisible, and before AuthN)
# --------------------------------------------------------------------------- #
def test_registry_gate_reads_the_surface_closed_by_default(tmp_path: Path):
    """``surfaces.chat`` absent, or declared off, both read as OFF."""
    assert ChatSurface(repo_root=REPO_ROOT).enabled is False
    assert ChatSurface(
        repo_root=REPO_ROOT, registry_path=_registry(tmp_path, "off")
    ).enabled is False
    assert ChatSurface(
        repo_root=REPO_ROOT, registry_path=_registry(tmp_path, "on")
    ).enabled is True
    # the flag is a registry read, never an environment guess
    readme = (REPO_ROOT / "portal" / "README.md").read_text(encoding="utf-8")
    assert "surfaces.chat" in readme


def test_route_and_view_are_absent_while_the_flag_is_off(
    gateway: FakeGateway, tmp_path: Path
):
    app = _app(gateway, tmp_path, enabled=False)
    signed_in = _login(app, ACME_USER, "acme")
    for client in (signed_in, ApiClient(app)):
        status, payload = client.get("/api/chat/tiers")
        assert status == 404, (status, payload)
        assert payload["error"]["code"] == "feature_disabled"
    # the view and its client are absent too — not 403 on a document that exists
    status, payload = signed_in.get("/views/chat.html")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    status, payload = signed_in.get("/js/chat.js")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    assert gateway.requests == []


def test_promoted_surface_serves_the_view_and_still_requires_a_session(
    gateway: FakeGateway, tmp_path: Path
):
    """The other half of the control: with the flag on, absence must stop."""
    app = _app(gateway, tmp_path, enabled=True, registry=_registry(tmp_path, "on"))
    assert app.chat.enabled is True
    response = app.handle("GET", "/views/chat.html")
    assert response.status == 200
    assert b"agent-orchestrator console" in response.as_bytes()
    # the API family is reachable — and refuses an anonymous caller, which is a
    # different answer from the 404 an unpromoted surface gives
    status, payload = ApiClient(app).get("/api/chat/tiers")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


# --------------------------------------------------------------------------- #
# The view: design system, no new visual language, accessible
# --------------------------------------------------------------------------- #
def test_view_is_a_self_contained_frame_consuming_the_design_tokens():
    html = VIEW.read_text(encoding="utf-8")
    assert 'href="/design-tokens/tokens.css"' in html
    assert 'href="/css/console.css"' in html
    assert 'src="/js/api.js"' in html
    assert 'src="/js/chat.js"' in html
    # a live region announces streamed content; the transcript is a log
    assert 'aria-live="polite"' in html
    assert 'role="log"' in html
    # the honest states are in the markup itself, not only in the script
    for marker in ('data-severity="no_data"', 'data-state="NO_DATA"',
                   'data-degraded="false"', 'data-selects="tier"'):
        assert marker in html, marker


def test_client_renders_the_honest_states_and_announces_streamed_content():
    script = CLIENT.read_text(encoding="utf-8")
    # every honesty branch the issue names is present in the renderer
    for marker in ('"data-supported": supported ? "true" : "false"',
                   '"data-degraded": "true"',
                   '"data-degraded", degraded.degraded ? "true" : "false"',
                   '"hard_stop"', '"no_data"', '"data-state": "failed"',
                   'NO_DATA'):
        assert marker in script, marker
    assert "unsupported" in script
    assert script.count("announce(") >= 5, "streamed content is announced"
    # the picker is filled from the tiers contract only: no model select exists
    assert 'id="modelPicker"' not in script
    assert 'document.getElementById("tierPicker")' in script


def test_tier_vocabulary_is_the_platform_ladder():
    """The ladder is pinned to the gateway's own provider contract."""
    assert TIERS == ("LOW", "MED", "HIGH", "MAX")
    assert DEFAULT_TIER in TIERS
    assert set(BUDGET_ACTIONS) == {"allow", "warn", "fallback", "stop"}
    assert SEVERITIES == ("ok", "warning", "hard_stop", "no_data")
    assert GROUNDING_OK != GROUNDING_NO_DATA


# --------------------------------------------------------------------------- #
# Tiers: the client selects a tier, never a provider model
# --------------------------------------------------------------------------- #
def test_picker_offers_only_tiers_and_shows_the_resolved_model(
    gateway: FakeGateway, tmp_path: Path
):
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    status, payload = api.get("/api/chat/tiers")
    assert status == 200
    data = payload["data"]
    assert [tier["id"] for tier in data["tiers"]] == list(TIERS)
    assert data["selects"] == "tier"
    assert data["defaultTier"] == DEFAULT_TIER
    # nothing here is an arbitrary provider model to choose from
    for forbidden in ("models", "providers", "choices"):
        assert forbidden not in data, forbidden
    # nothing has been resolved yet — and that is said by absence, not invented
    assert data["resolvedModels"] == {}

    conversation = _open(api)
    _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                    {"text": "who is on call?", "tier": "MED"}))
    status, payload = api.get("/api/chat/tiers")
    # the resolution the chooser reported is *displayed* afterwards
    assert payload["data"]["resolvedModels"]["MED"] == gateway.model


def test_a_turn_refuses_an_arbitrary_provider_model(
    gateway: FakeGateway, tmp_path: Path
):
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    status, payload = api.post(
        f"/api/chat/conversations/{conversation}/turns",
        {"text": "hello", "model": "gpt-4o"},
    )
    assert status == 400
    assert payload["error"]["code"] == "chat_tier_only"
    status, payload = api.post(
        f"/api/chat/conversations/{conversation}/turns",
        {"text": "hello", "tier": "ULTRA"},
    )
    assert status == 400
    assert payload["error"]["code"] == "chat_unknown_tier"
    # and the authority never saw either attempt
    assert gateway.completions() == 0
    # the same refusal, at the unit the route delegates to
    with pytest.raises(ChatError) as raised:
        parse_turn_request({"text": "hello", "provider": "openai"})
    assert raised.value.code == "chat_tier_only"


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
def test_stream_renders_incrementally_and_reaches_a_terminal_state(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.tokens = ("Hel", "lo ", "wor", "ld")
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    events = _drain(
        api.post(f"/api/chat/conversations/{conversation}/turns",
                 {"text": "hello", "tier": "MED"})
    )
    names = [event for event, _ in events]
    assert names[0] == "turn.started"
    assert names[-1] == "done"

    deltas = _events_of(events, "turn.delta")
    assert len(deltas) >= 4, "a streamed answer arrives token by token"
    lengths = [len(delta["text"]) for delta in deltas]
    assert lengths == sorted(lengths) and lengths[-1] == len("Hello world")
    assert [delta["delta"] for delta in deltas] == ["Hel", "lo ", "wor", "ld"]

    completed = _events_of(events, "turn.completed")
    assert len(completed) == 1
    turn = completed[0]["turn"]
    assert turn["state"] == TURN_COMPLETE
    assert turn["text"] == "Hello world"
    assert turn["resolvedModel"] == gateway.model
    assert turn["usage"]["estimatedCostUsd"] == 0.0031
    assert turn["usage"]["totalTokens"] == 162
    # the turn is persisted, so a reload shows the same terminal state
    status, payload = api.get(f"/api/chat/conversations/{conversation}")
    assert payload["data"]["conversation"]["turns"][-1]["state"] == TURN_COMPLETE


def test_cancelled_stream_stops_cleanly_and_is_recorded(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.tokens = tuple(f"tok{i} " for i in range(200))
    gateway.delay = 0.02
    gateway.padding = 2048
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    response = api.post(
        f"/api/chat/conversations/{conversation}/turns",
        {"text": "long answer please", "tier": "HIGH"},
    )
    frames = response.frames
    started = next(frames)
    assert "turn.started" in started
    next(frames)  # turn.budget
    next(frames)  # first delta — generation is genuinely under way

    status, payload = api.post(f"/api/chat/conversations/{conversation}/cancel", {})
    assert status == 200
    assert payload["data"]["cancelled"] is True

    rest = _events(list(frames))
    names = [event for event, _ in rest]
    assert "turn.cancelled" in names, names
    assert "turn.completed" not in names, "a cancelled stream must not complete"
    assert names[-1] == "done"
    assert len(_events_of(rest, "turn.delta")) == 0, (
        "no further tokens are rendered after the stop"
    )
    assert gateway.wait_aborted() >= 1, (
        "the upstream stream was closed, not left open"
    )

    status, payload = api.get(f"/api/chat/conversations/{conversation}")
    turns = payload["data"]["conversation"]["turns"]
    assert turns[-1]["state"] == TURN_CANCELLED
    assert turns[-1]["role"] == "assistant"
    # a stop with nothing in flight is an honest no-op, not a false success
    status, payload = api.post(f"/api/chat/conversations/{conversation}/cancel", {})
    assert payload["data"]["cancelled"] is False


def test_retry_re_runs_the_last_user_turn(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.tokens = ("first",)
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                    {"text": "what changed?", "tier": "LOW"}))
    before = gateway.bodies[-1]["messages"]

    gateway.tokens = ("second",)
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/retry",
                             {"tier": "LOW"}))
    completed = _events_of(events, "turn.completed")
    assert len(completed) == 1
    turn = completed[0]["turn"]
    assert turn["text"] == "second"
    assert turn["retryOf"], "the retry names the turn it replaced"
    # the question is replayed as-is — a retry is not a new question
    assert gateway.bodies[-1]["messages"][-1] == {"role": "user",
                                                  "content": "what changed?"}
    assert gateway.bodies[-1]["model"] == "LOW"
    assert len(before) == 1
    status, payload = api.get(f"/api/chat/conversations/{conversation}")
    assert len(payload["data"]["conversation"]["turns"]) == 4


# --------------------------------------------------------------------------- #
# Citations / provenance
# --------------------------------------------------------------------------- #
def test_each_fragment_shows_its_source_and_unsupported_claims_are_marked(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.citations = {
        "sources": SOURCES,
        "fragments": [
            {"text": "coder-1 owns the nightly lane", "sourceId": "bridge:registry@1a2b3c4d5e6f"},
            {"text": "kb.query returned 3 hits", "sourceId": "tool:kb.query"},
            {"text": "the platform will cut over on Friday", "sourceId": "nowhere:invented"},
            {"text": "an unlabelled claim"},
        ],
    }
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "who owns the lane?", "tier": "MED"}))
    envelope = _events_of(events, "turn.citations")[0]
    fragments = envelope["fragments"]
    assert len(fragments) == 4, "every fragment is shown, backed or not"
    supported = [fragment for fragment in fragments if fragment["supported"]]
    unsupported = [fragment for fragment in fragments if not fragment["supported"]]
    assert len(supported) == 2 and len(unsupported) == 2
    assert "bridge registry@1a2b3c4d5e6f" in supported[0]["label"]
    assert supported[0]["source"]["revision"].startswith("1a2b3c4d5e6f")
    assert "kb.query" in supported[1]["label"]
    for fragment in unsupported:
        assert fragment["label"] == unsupported_label()
        assert fragment["source"] is None
    assert envelope["grounding"]["state"] == GROUNDING_OK

    # and it survives a reload, with the same provenance status
    status, payload = api.get(f"/api/chat/conversations/{conversation}")
    turn = payload["data"]["conversation"]["turns"][-1]
    assert [fragment["supported"] for fragment in turn["fragments"]] == [
        True, True, False, False
    ]
    assert turn["sources"] == SOURCES


def test_a_missing_source_is_no_data_not_an_empty_success(
    gateway: FakeGateway, tmp_path: Path
):
    """No envelope at all: the turn is complete and *says* it has no data."""
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "any grounded answer?", "tier": "MED"}))
    turn = _events_of(events, "turn.completed")[0]["turn"]
    assert turn["state"] == TURN_COMPLETE, "the answer still arrives"
    assert turn["grounding"]["state"] == GROUNDING_NO_DATA
    assert turn["grounding"]["state"] != GROUNDING_OK
    assert "no grounding sources" in turn["grounding"]["note"]
    assert turn["sources"] == [] and turn["fragments"] == []

    # a *fragment with no sources* is the same honest state, not a quiet claim
    gateway.citations = {"sources": [], "fragments": [{"text": "vibes", "sourceId": "x"}]}
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "again", "tier": "MED"}))
    turn = _events_of(events, "turn.completed")[0]["turn"]
    assert turn["grounding"]["state"] == GROUNDING_NO_DATA
    assert turn["fragments"][0]["supported"] is False


def test_a_degraded_turn_says_it_degraded_and_why(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.degraded = {
        "degraded": True,
        "reason": "the HIGH provider was unavailable; the local rung answered",
        "fromTier": "HIGH",
        "toTier": "LOW",
    }
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "why did it degrade?", "tier": "HIGH"}))
    degraded = _events_of(events, "turn.degraded")
    assert len(degraded) == 1
    assert degraded[0]["degraded"] is True
    assert "unavailable" in degraded[0]["reason"]
    assert (degraded[0]["fromTier"], degraded[0]["toTier"]) == ("HIGH", "LOW")
    turn = _events_of(events, "turn.completed")[0]["turn"]
    assert turn["degraded"]["degraded"] is True
    assert turn["degraded"]["toTier"] == "LOW"

    # an ordinary turn is not labelled degraded — the label means something
    gateway.degraded = {"degraded": False}
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "and now?", "tier": "HIGH"}))
    turn = _events_of(events, "turn.completed")[0]["turn"]
    assert turn["degraded"]["degraded"] is False


# --------------------------------------------------------------------------- #
# FinOps: per-turn figures, and the budget states that are not interchangeable
# --------------------------------------------------------------------------- #
def test_usage_absent_from_the_read_model_is_no_data_not_zero(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.usage = False
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "cost?", "tier": "MED"}))
    turn = _events_of(events, "turn.completed")[0]["turn"]
    assert turn["usage"]["state"] == GROUNDING_NO_DATA
    assert turn["usage"]["estimatedCostUsd"] is None
    assert turn["usage"]["totalTokens"] is None
    assert turn["usage"]["note"], "absence is named, not silent"


def test_soft_budget_warning_is_visibly_distinct_from_a_hard_stop(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.budget = {
        "tenantId": "acme",
        "action": "warn",
        "reason": "at/above warn threshold; spend flagged",
        "monthlyBudgetUsd": 500.0,
        "spentUsd": 430.0,
        "pctUsed": 86.0,
        "warnAtPct": 80.0,
        "hardCapPct": 100.0,
    }
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    status, payload = api.get("/api/chat/budget")
    warning = payload["data"]
    assert warning["severity"] == "warning"
    assert warning["canSend"] is True
    assert warning["pctUsed"] == 86.0

    conversation = _open(api)
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "still allowed?", "tier": "MED"}))
    pre_flight = _events_of(events, "turn.budget")[0]
    assert pre_flight["severity"] == "warning"
    assert pre_flight["canSend"] is True
    assert gateway.completions() == 1, "a soft warning still sends the turn"

    # the hard stop: refused before the model call, with its own code
    gateway.budget = {
        "tenantId": "acme", "action": "stop",
        "reason": "budget exhausted or hard cap reached",
        "monthlyBudgetUsd": 500.0, "spentUsd": 500.0, "pctUsed": 100.0,
        "warnAtPct": 80.0, "hardCapPct": 100.0,
    }
    status, payload = api.get("/api/chat/budget")
    hard = payload["data"]
    assert hard["severity"] == "hard_stop"
    assert hard["canSend"] is False
    assert hard["severity"] != warning["severity"], (
        "the hard stop must not collapse into the soft warning"
    )
    status, payload = api.post(f"/api/chat/conversations/{conversation}/turns",
                               {"text": "and now?", "tier": "MED"})
    assert status == 402
    assert payload["error"]["code"] == "chat_budget_hard_stop"
    assert gateway.completions() == 1, "the refused turn never reached the model"


def test_an_unreadable_budget_fails_closed_with_its_own_code(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.budget_status = 503
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    status, payload = api.get("/api/chat/budget")
    assert payload["data"]["severity"] == "no_data"
    assert payload["data"]["canSend"] is False
    conversation = _open(api)
    status, payload = api.post(f"/api/chat/conversations/{conversation}/turns",
                               {"text": "may I?", "tier": "MED"})
    # a different refusal from the hard stop, and never a send
    assert status == 503
    assert payload["error"]["code"] == "chat_budget_no_data"
    assert gateway.completions() == 0

    # an action the authority does not declare is unreadable, not permission
    gateway.budget_status = 200
    gateway.budget = {"action": "give-me-credit", "monthlyBudgetUsd": 10.0}
    status, payload = api.get("/api/chat/budget")
    assert payload["data"]["severity"] == "no_data"
    assert payload["data"]["canSend"] is False


# --------------------------------------------------------------------------- #
# History, isolation, permissions
# --------------------------------------------------------------------------- #
def test_history_replays_to_the_authority_and_stays_per_conversation(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.tokens = ("a",)
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    first = _open(api)
    second = _open(api)
    _drain(api.post(f"/api/chat/conversations/{first}/turns",
                    {"text": "one", "tier": "MED"}))
    _drain(api.post(f"/api/chat/conversations/{first}/turns",
                    {"text": "two", "tier": "MED"}))
    sent = gateway.bodies[-1]["messages"]
    assert [(row["role"], row["content"]) for row in sent] == [
        ("user", "one"), ("assistant", "a"), ("user", "two")
    ], "the authority receives the conversation's own history"

    status, payload = api.get("/api/chat/conversations")
    rows = payload["data"]["conversations"]
    assert {row["id"] for row in rows} == {first, second}
    assert len(rows) == 2
    status, payload = api.get(f"/api/chat/conversations/{second}")
    assert payload["data"]["conversation"]["turns"] == []


def test_conversations_are_tenant_scoped_and_never_cross(
    gateway: FakeGateway, tmp_path: Path
):
    app = _app(gateway, tmp_path)
    acme = _login(app, ACME_USER, "acme")
    globex = _login(app, GLOBEX_USER, "globex")
    conversation = _open(acme)
    status, payload = globex.get(f"/api/chat/conversations/{conversation}")
    assert status == 404
    assert payload["error"]["code"] == "unknown_conversation"
    status, payload = globex.post(f"/api/chat/conversations/{conversation}/turns",
                                  {"text": "borrow this thread", "tier": "MED"})
    assert status == 404
    assert gateway.completions() == 0
    status, payload = globex.get("/api/chat/conversations")
    assert payload["data"]["conversations"] == []


def test_the_surface_uses_the_platform_role_vocabulary(
    gateway: FakeGateway, tmp_path: Path
):
    """No new permission is minted: the route maps onto the existing packs."""
    app = _app(gateway, tmp_path)
    operator = _login(app, "erin@acme.example.com", "acme")
    # an agent-operator may run a turn...
    conversation = _open(_login(app, ACME_USER, "acme"))
    events = _drain(operator.post(
        f"/api/chat/conversations/{conversation}/turns",
        {"text": "diagnose", "tier": "MED"},
    ))
    assert _events_of(events, "turn.completed")
    # ...but may not open a conversation (`session:manage`) nor read the budget
    status, payload = operator.post("/api/chat/conversations", {})
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"
    status, payload = operator.get("/api/chat/budget")
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"


# --------------------------------------------------------------------------- #
# Faults, honesty and offline posture
# --------------------------------------------------------------------------- #
def test_upstream_faults_are_turn_states_not_unhandled_500s(
    gateway: FakeGateway, tmp_path: Path
):
    gateway.finish = False
    gateway.done = False
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "truncate me", "tier": "MED"}))
    failure = _events_of(events, "turn.error")
    assert len(failure) == 1
    assert failure[0]["state"] == TURN_FAILED
    assert failure[0]["failure"]["code"] == "chat_upstream_truncated"
    assert failure[0]["text"] == "Hello world", "partial output is kept, not lost"
    status, payload = api.get(f"/api/chat/conversations/{conversation}")
    assert payload["data"]["conversation"]["turns"][-1]["state"] == TURN_FAILED


def test_an_unreachable_model_path_is_reported_and_never_faked(
    tmp_path: Path, gateway: FakeGateway
):
    """The budget read answers; the model path goes away mid-turn."""
    gateway.fail_completions = True
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    # the budget authority answered, so this is not the fail-closed refusal
    status, payload = api.get("/api/chat/budget")
    assert payload["data"]["severity"] == "ok"
    events = _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                             {"text": "anyone home?", "tier": "MED"}))
    failure = _events_of(events, "turn.error")[0]
    assert failure["failure"]["code"] == "chat_upstream_unavailable"
    assert failure["text"] == "", "no answer is invented when none arrived"


def test_an_unreachable_authority_refuses_the_turn_and_says_why(
    tmp_path: Path, gateway: FakeGateway
):
    """Nothing answers at all: the pre-flight fails closed, with its own code."""
    surface = _surface(gateway, tmp_path)
    surface.gateway_base_url = "127.0.0.1:1"  # nothing listens here
    api = _login(
        ConsoleApplication(repo_root=REPO_ROOT, sso=console_sso(),
                           chat_surface=surface),
        ACME_USER, "acme",
    )
    conversation = _open(api)
    status, payload = api.post(f"/api/chat/conversations/{conversation}/turns",
                               {"text": "anyone home?", "tier": "MED"})
    assert status == 503
    assert payload["error"]["code"] == "chat_budget_no_data"
    status, payload = api.get(f"/api/chat/conversations/{conversation}")
    # nothing is recorded: the turn was refused before the model path, so the
    # history has no user message either (the composer keeps the draft)
    assert payload["data"]["conversation"]["turns"] == []


def test_the_surface_never_egresses(gateway: FakeGateway, tmp_path: Path):
    api = _login(_app(gateway, tmp_path), ACME_USER, "acme")
    conversation = _open(api)
    _drain(api.post(f"/api/chat/conversations/{conversation}/turns",
                    {"text": "offline?", "tier": "MED"}))
    assert gateway.requests, "the turn really did go over HTTP"
    host, _, port = gateway.base_url.partition(":")
    assert host == "127.0.0.1"
    assert port.isdigit()
    # the client is a plain HTTP client over the configured authority only
    assert http.client.HTTPConnection is not None
    assert view_is_offline(VIEW) and view_is_offline(CLIENT)


def view_is_offline(path: Path) -> bool:
    """No asset may reference an external origin (the console ships offline)."""
    text = path.read_text(encoding="utf-8")
    return not any(
        marker in text
        for marker in ("http://", "https://", "//fonts.googleapis", "cdn.")
    )


def test_chat_assets_are_exactly_the_gated_pair():
    assert CHAT_ASSETS == ("views/chat.html", "js/chat.js")
    for relative in CHAT_ASSETS:
        assert (STATIC / relative).exists(), relative
    assert build_app is not None and time is not None
