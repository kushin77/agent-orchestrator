"""Fleet dashboard — browser acceptance tests (issue #332).

MECHANISM (stated plainly, as the issue requires): these are **real-browser**
tests. Each one starts the console's own stdlib HTTP server in-process (the
merged #331 surface, with the fleet runtime redirected into a pytest tmp tree),
mints a genuine auth-gate session cookie, then drives **headless Google Chrome
over the DevTools protocol** and asserts against the live DOM the page built.
Nothing here asserts on a string of HTML this file wrote: every claim is read
back out of Blink's DOM, and the push proof is a DOM mutation observed while
the document is *still the same document* (one page load, one navigation).

Why this harness and not jsdom: the console ships offline — no npm, no
node_modules, no CDN — and a hand-written DOM shim would be a mock of the thing
under test. Chrome is already available on the fleet's hosts
(``/usr/bin/google-chrome``); the page is vanilla ES5 DOM, so the only tooling
needed is a WebSocket to Chrome's own debug endpoint (``websocket-client``) —
no test framework, no bundler, no browser download.

Isolation: the projection is pointed at a per-test tmp tree, so these tests
never read or write the live fleet's ``.fleet/`` state and never need a tmux,
a TTY or the network. Every HTTP request the page makes is asserted to be
same-origin loopback, so the page cannot egress.

Acceptance criteria covered (issue #332):
  AC1  one live frame per push, without a full reload   -> test_..._push
  AC2  a filter on tenant/lane/rung changes visible rows -> test_..._filters
  AC3  the timeline renders more than the TUI's 8-event window and scrolls
                                                          -> test_..._timeline
  AC4  the roll-up shows per-org rows keyed to #151       -> test_..._rollup
  AC5  the page keeps working when a section's data is missing
                                                          -> test_..._missing
"""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
FLEET_PKG = REPO_ROOT / "fleet"
if str(FLEET_PKG) not in sys.path:
    sys.path.insert(0, str(FLEET_PKG))

import channel  # noqa: E402  (fleet/channel.py — the runtime path source)
from conftest import AUTH_GATE, console_sso  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.fleet import FleetProjection, load_fleet_console  # noqa: E402
from portal.server.httpd import ConsoleServer  # noqa: E402
from portal.server.sso import SESSION_COOKIE  # noqa: E402

PAGE_PATH = "/views/fleet.html"
HIERARCHY = REPO_ROOT / "portal" / "static" / "assets" / "fleet-hierarchy.json"
SUPER_ADMIN_EMAIL = "root@platform.example.com"
ORG_ELEVATEDIQ = "elevatediq"
ORG_INITECH = "initech-holdings"
#: The TUI's event window (fleet/console.py events_snapshot default).
TUI_EVENT_WINDOW = 8
#: Records seeded into the redirected slog.jsonl.
SEEDED_EVENTS = 100
#: Lane rows the seeded projection must produce before any filtering.
SEEDED_LANES = 7

CHROME_CANDIDATES = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/chrome",
)

try:  # the only third-party piece: a WebSocket to Chrome's own debug endpoint
    import websocket  # type: ignore[import-untyped]  # websocket-client
except ImportError:  # pragma: no cover - exercised on hosts without the client
    websocket = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# Chrome + DevTools transport
# --------------------------------------------------------------------------


def _chrome_binary() -> str:
    """The headless browser to drive, or a loud, actionable failure.

    Deliberately not a skip: these tests are the issue's proof, and a skipped
    proof is a green light nobody earned (no-false-green doctrine)."""
    candidates = list(CHROME_CANDIDATES)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    pytest.fail(
        "these browser tests require headless Chrome/Chromium; none of "
        f"{', '.join(CHROME_CANDIDATES)} exists on this host"
    )
    raise AssertionError("unreachable")  # pragma: no cover


class _DevTools:
    """A minimal Chrome DevTools protocol client (one WebSocket, JSON frames)."""

    def __init__(self, ws_url: str) -> None:
        try:
            self._ws = websocket.create_connection(
                ws_url, timeout=30, suppress_origin=True
            )
        except TypeError:  # pragma: no cover - older websocket-client
            self._ws = websocket.create_connection(ws_url, timeout=30)
        self._next_id = 0
        self._events: list[dict] = []

    def send(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        message: dict = {"id": request_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self._ws.send(json.dumps(message))
        while True:
            payload = json.loads(self._ws.recv())
            if payload.get("id") == request_id:
                if "error" in payload:
                    raise AssertionError(f"CDP {method} failed: {payload['error']}")
                return payload.get("result", {})
            self._events.append(payload)

    def pump(self, seconds: float = 0.1) -> None:
        """Collect any events Chrome has queued (non-blocking-ish)."""
        self._ws.settimeout(seconds)
        deadline = time.time() + seconds
        try:
            while time.time() < deadline:
                raw = self._ws.recv()
                if not raw:
                    return
                self._events.append(json.loads(raw))
        except Exception:  # noqa: BLE001 - a quiet socket is the normal case
            pass
        finally:
            self._ws.settimeout(30)

    def events(self, method: str, session: str | None = None) -> list[dict]:
        self.pump(0.05)
        return [
            event for event in self._events
            if event.get("method") == method
            and (session is None or event.get("sessionId") == session)
        ]

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass


class _Page:
    """One Chrome tab, driven over a flat DevTools session."""

    def __init__(self, devtools: _DevTools, session: str, origin: str, target_id: str) -> None:
        self._dt = devtools
        self.session = session
        self.origin = origin
        self.target_id = target_id

    # -- evaluation -----------------------------------------------------
    def evaluate(self, expression: str, await_promise: bool = False):
        result = self._dt.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            session=self.session,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise AssertionError(
                "the page raised while evaluating "
                f"{expression!r}: {details.get('text')} "
                f"{(details.get('exception') or {}).get('description', '')}"
            )
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 25.0, message: str = ""):
        """Poll until ``expression`` is truthy.

        Evaluation errors are tolerated while polling: a document that is still
        mid-navigation has no execution context yet, which is not a failure.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate(expression):
                    return True
            except AssertionError:
                pass
            time.sleep(0.05)
        raise AssertionError(
            f"timed out after {timeout}s waiting for: {expression}"
            + (f" ({message})" if message else "")
        )

    # -- DOM helpers ----------------------------------------------------
    def visible_rows(self, body_id: str) -> int:
        return self.evaluate(
            "(function(){return document.querySelectorAll('#%s [data-row]:not([hidden])')"
            ".length;})()" % body_id
        )

    def all_rows(self, body_id: str) -> int:
        return self.evaluate(
            "(function(){return document.querySelectorAll('#%s [data-row]').length;})()"
            % body_id
        )

    def facet_values(self, body_id: str, facet: str) -> list[str]:
        return self.evaluate(
            "(function(){var out=[];document.querySelectorAll('#%s [data-row]:not([hidden])')"
            ".forEach(function(row){out.push(row.getAttribute('data-%s'));});"
            "return out;})()" % (body_id, facet)
        ) or []

    def set_select(self, element_id: str, value: str):
        return self.evaluate(
            "(function(){var select=document.getElementById(%r);"
            "if(!select){return null;}"
            "select.value=%r;"
            "select.dispatchEvent(new Event('change',{bubbles:true}));"
            "return select.value;})()" % (element_id, value)
        )

    # -- observations ---------------------------------------------------
    def navigations(self) -> list[str]:
        """Every top-level document navigation this tab performed."""
        urls = []
        for event in self._dt.events("Page.frameNavigated", self.session):
            frame = event.get("params", {}).get("frame", {})
            if frame.get("parentId"):
                continue
            url = str(frame.get("url") or "")
            if url and url != "about:blank":
                urls.append(url)
        return urls

    def exceptions(self) -> list[str]:
        return [
            str(event.get("params", {}).get("exceptionDetails", {}).get("text"))
            for event in self._dt.events("Runtime.exceptionThrown", self.session)
        ]

    def request_urls(self) -> list[str]:
        return [
            str(event.get("params", {}).get("request", {}).get("url"))
            for event in self._dt.events("Network.requestWillBeSent", self.session)
        ]

    def close(self) -> None:
        try:
            self._dt.send("Target.closeTarget", {"targetId": self.target_id})
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass


class _Browser:
    """A headless Chrome process shared by the module's tests."""

    def __init__(self, devtools: _DevTools, process: subprocess.Popen, user_data: Path) -> None:
        self._dt = devtools
        self._process = process
        self._user_data = user_data

    def new_page(self, url: str, cookie: tuple[str, str] | None = None) -> _Page:
        target_id = self._dt.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self._dt.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )["sessionId"]
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        page = _Page(self._dt, session, origin, target_id)
        for domain in ("Page", "Runtime", "Network"):
            self._dt.send(f"{domain}.enable", {}, session=session)
        if cookie is not None:
            name, value = cookie
            self._dt.send(
                "Network.setCookie",
                {
                    "name": name,
                    "value": value,
                    "url": origin + "/",
                    "path": "/",
                    "httpOnly": True,
                    "sameSite": "Strict",
                },
                session=session,
            )
        self._dt.send("Page.navigate", {"url": url}, session=session)
        return page

    def stop(self) -> None:
        self._dt.close()
        try:
            self._process.terminate()
            self._process.wait(timeout=10)
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            try:
                self._process.kill()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(self._user_data, ignore_errors=True)


@pytest.fixture(scope="module")
def browser():
    """One headless Chrome for the module; one tab per test."""
    binary = _chrome_binary()
    if websocket is None:
        pytest.fail(
            "these browser tests drive Chrome over the DevTools protocol and need "
            "`websocket-client`; install it with `python3 -m pip install websocket-client`"
        )
    user_data = Path(tempfile.mkdtemp(prefix="ao332-chrome-"))
    process = subprocess.Popen(
        [
            binary,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--metrics-recording-only",
            "--remote-allow-origins=*",
            "--remote-debugging-port=0",
            f"--user-data-dir={user_data}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # immune to a sibling lane's Ctrl-C on the shared shell
    )
    port_file = user_data / "DevToolsActivePort"
    deadline = time.time() + 40
    while not port_file.exists() and time.time() < deadline and process.poll() is None:
        time.sleep(0.05)
    if not port_file.exists():  # pragma: no cover - host without a working Chrome
        process.kill()
        shutil.rmtree(user_data, ignore_errors=True)
        pytest.fail(f"headless Chrome ({binary}) never opened its DevTools port")
    port_line, _, ws_path = port_file.read_text(encoding="utf-8").partition("\n")
    devtools = _DevTools(f"ws://127.0.0.1:{int(port_line.strip())}{ws_path.strip()}")
    instance = _Browser(devtools, process, user_data)
    try:
        yield instance
    finally:
        instance.stop()


# --------------------------------------------------------------------------
# The console under test: real server, redirected fleet runtime, seeded state
# --------------------------------------------------------------------------


def _redirect_runtime(tmp_path: Path, monkeypatch) -> object:
    """Point every fleet runtime path the console reads at ``tmp_path``."""
    console = load_fleet_console(REPO_ROOT)
    monkeypatch.setattr(channel, "SLOG", tmp_path / "slog.jsonl")
    monkeypatch.setattr(channel, "HEARTBEAT", tmp_path / "sister.heartbeat.json")
    monkeypatch.setattr(channel, "BRAIN_HEARTBEAT", tmp_path / "brain.heartbeat.json")
    monkeypatch.setattr(channel, "BRAIN_INBOX", tmp_path / "brain" / "inbox")
    monkeypatch.setattr(channel, "BRAIN_SENT", tmp_path / "brain" / "sent")
    monkeypatch.setattr(channel, "BRAIN_OUTBOX", tmp_path / "brain" / "outbox")
    monkeypatch.setattr(console, "FLEET_DIR", tmp_path)
    monkeypatch.setattr(channel, "head_commit", lambda: "ao332sha")
    monkeypatch.setattr(console, "loop_pid", lambda pattern: 4242)
    monkeypatch.setattr(console, "closed_issues", lambda *args, **kwargs: set())
    return console


def _write_beat(path: Path, state: str, commit: str) -> None:
    path.write_text(
        json.dumps({"state": state, "commit": commit, "started_at": "2026-09-13T00:00:00Z"}),
        encoding="utf-8",
    )


def _seed(tmp_path: Path, console, monkeypatch) -> None:
    """A deterministic fleet: 3 rungs, 2 waves, 2 claims, 1 dispatch, 100 events."""
    _write_beat(tmp_path / "brain.heartbeat.json", "working", "brain0001")
    _write_beat(tmp_path / "sister.heartbeat.json", "working", "sister001")
    _write_beat(tmp_path / "monitor.heartbeat.json", "idle", "monitor01")

    waves = tmp_path / "waves"
    waves.mkdir(parents=True, exist_ok=True)
    (waves / "wave-219.json").write_text(
        json.dumps({"parent": 219, "children": [232, 233], "dispatched": [234]}),
        encoding="utf-8",
    )
    (waves / "wave-240.json").write_text(
        json.dumps({"parent": 240, "children": [], "dispatched": [241]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        console,
        "claims_snapshot",
        lambda: ["issue #232 held by ao-sister-1", "issue #240 held by ao-sister-2"],
    )

    outbox = tmp_path / "brain" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "msg-1.json").write_text(
        json.dumps(
            {
                "id": "msg-1", "from": "brain", "to": "sister", "type": "directive",
                "ts": "2026-09-13T00:00:10Z", "correlation_id": "#250",
                "task": {"issue": 250},
            }
        ),
        encoding="utf-8",
    )

    (tmp_path / "watchdog.log").write_text(
        "watchdog: brain missed 2 beats (#219)\n"
        "watchdog: sister recovered (#232)\n",
        encoding="utf-8",
    )

    channel.SLOG.write_text(
        "".join(
            json.dumps(
                {
                    "ts": "2026-09-13T00:%02d:%02dZ" % (index // 60, index % 60),
                    "n": index,
                    "action": "wave.dispatch",
                    "detail": "issue #%d" % (200 + index),
                }
            ) + "\n"
            for index in range(SEEDED_EVENTS)
        ),
        encoding="utf-8",
    )


class _Harness:
    """The live console (real HTTP) plus the handles the assertions need."""

    def __init__(self, page: _Page, origin: str, console, tmp_path: Path, token: str) -> None:
        self.page = page
        self.origin = origin
        self.console = console
        self.tmp_path = tmp_path
        self.token = token

    # -- the push that must repaint the DOM -----------------------------
    def push_frame(self, commit: str) -> None:
        """Change the sister's heartbeat: the projection notices, the stream pushes."""
        _write_beat(self.tmp_path / "sister.heartbeat.json", "working", commit)

    # -- a client-side read of the same API the page reads --------------
    def api(self, path: str) -> dict:
        connection = http.client.HTTPConnection(
            "127.0.0.1", int(self.origin.rsplit(":", 1)[1]), timeout=10
        )
        try:
            connection.request(
                "GET", path, headers={"Cookie": f"{SESSION_COOKIE}={self.token}"}
            )
            body = json.loads(connection.getresponse().read().decode("utf-8"))
        finally:
            connection.close()
        return body["data"]


@pytest.fixture
def dashboard(browser, tmp_path, monkeypatch):
    """The real console server + a real browser tab on the fleet dashboard."""
    console = _redirect_runtime(tmp_path, monkeypatch)
    _seed(tmp_path, console, monkeypatch)
    fleet = FleetProjection(
        repo_root=REPO_ROOT, console=console, enabled=True, poll_interval=0.05
    )
    app = build_app(sso=console_sso(), fleet_projection=fleet)
    server = ConsoleServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    token = AUTH_GATE.mint(SUPER_ADMIN_EMAIL, "acme")
    page = browser.new_page(origin + PAGE_PATH, cookie=(SESSION_COOKIE, token))
    try:
        page.wait_for(
            "!!(window.FleetDashboard && window.FleetDashboard.state.frames >= 1 "
            "&& document.querySelectorAll('#lanesBody [data-row]').length > 0)",
            message="the first pushed frame never painted",
        )
        yield _Harness(page, origin, console, tmp_path, token)
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------
# AC1 — one live frame per push, without a full reload
# --------------------------------------------------------------------------


def test_a_push_mutates_the_live_document_without_a_reload(dashboard):
    page = dashboard.page
    navigations_before = page.navigations()
    assert len(navigations_before) == 1, "the tab must have loaded the page exactly once"
    assert page.evaluate("document.getElementById('statusLoads').getAttribute('data-loads')") == "1"

    # A handle on the live DOM: the push must mutate *this* document, not
    # replace it. (A reload would leave this node detached.)
    assert page.evaluate(
        "(function(){window.__probe=document.querySelector('#lanesBody');return !!window.__probe;})()"
    )
    frames_before = page.evaluate("window.FleetDashboard.state.frames")
    assert frames_before >= 1, "the stream's first frame must have painted"
    detail_before = page.evaluate(
        "document.querySelector('[data-row=\"rung:sister\"] td:nth-child(3)').textContent"
    )

    dashboard.push_frame("push0001")
    page.wait_for(
        "window.FleetDashboard.state.frames === %d" % (frames_before + 1),
        message="the stream never pushed a frame for the heartbeat change",
    )

    detail_after = page.evaluate(
        "document.querySelector('[data-row=\"rung:sister\"] td:nth-child(3)').textContent"
    )
    assert detail_before != detail_after, "the frame did not change the rendered rung detail"
    assert "push0001" in detail_after, detail_after

    # The same document: no navigation, no reload, the probe node still connected.
    assert page.navigations() == navigations_before, "the push caused a document navigation"
    assert page.evaluate("document.getElementById('statusLoads').getAttribute('data-loads')") == "1"
    assert page.evaluate(
        "window.__probe === document.querySelector('#lanesBody') && window.__probe.isConnected"
    ) is True
    assert page.evaluate("window.FleetDashboard.state.frames") == frames_before + 1

    # One frame per push, not per poll: the channel is quiet while nothing changes.
    time.sleep(0.6)
    assert page.evaluate("window.FleetDashboard.state.frames") == frames_before + 1, (
        "the stream pushed extra frames while the fleet was idle"
    )
    assert not page.exceptions(), page.exceptions()


def test_the_page_egresses_nothing(dashboard):
    """Every request the page makes is same-origin loopback (no CDN, no egress)."""
    page = dashboard.page
    assert page.evaluate(
        "(function(){return !!document.querySelector('link[href=\"/css/console.css\"]');})()"
    )
    urls = page.request_urls()
    assert urls, "no requests observed; the network recorder did not arm"
    foreign = [url for url in urls if not url.startswith(dashboard.origin)]
    assert not foreign, f"the page requested off-origin resources: {foreign}"


# --------------------------------------------------------------------------
# AC2 — the tenant / lane / rung filters change the visible rows
# --------------------------------------------------------------------------


def test_filters_change_the_visible_rows(dashboard):
    page = dashboard.page
    total = page.all_rows("lanesBody")
    assert total >= SEEDED_LANES, f"expected the seeded lanes, saw {total}"
    assert page.visible_rows("lanesBody") == total, "nothing is filtered initially"

    # rung
    assert page.set_select("filterRung", "monitor") == "monitor"
    monitor_rows = page.visible_rows("lanesBody")
    assert 0 < monitor_rows < total, (monitor_rows, total)
    assert set(page.facet_values("lanesBody", "rung")) == {"monitor"}
    # the row *set* is stable; only visibility changed
    assert page.all_rows("lanesBody") == total

    # lane: #241 is dispatched by exactly one wave, so exactly one row matches
    assert page.set_select("filterRung", "") == ""
    assert page.set_select("filterLane", "#241") == "#241"
    assert page.visible_rows("lanesBody") == 1
    assert page.facet_values("lanesBody", "lane") == ["#241"]

    # tenant: this portal instance serves kushin77/agent-orchestrator, which the
    # #151 hierarchy places under `acme`. Asking for another declared tenant is
    # an honest zero rows — and asking for `acme` restores every row.
    assert page.set_select("filterLane", "") == ""
    assert page.set_select("filterTenant", "globex") == "globex"
    assert page.visible_rows("lanesBody") == 0
    assert page.all_rows("lanesBody") == total
    assert page.set_select("filterTenant", "acme") == "acme"
    assert page.visible_rows("lanesBody") == total
    assert set(page.facet_values("lanesBody", "tenant")) == {"acme"}

    # the rungs table is filtered by the same predicate
    assert page.set_select("filterRung", "brain") == "brain"
    assert set(page.facet_values("rungsBody", "rung")) == {"brain"}
    assert page.set_select("filterRung", "") == ""
    assert page.visible_rows("rungsBody") == page.all_rows("rungsBody") == 3


# --------------------------------------------------------------------------
# AC3 — the timeline renders more than the TUI's window, and scrolls
# --------------------------------------------------------------------------


def test_timeline_renders_more_than_the_tui_window_and_scrolls(dashboard):
    page = dashboard.page
    window = page.evaluate(
        "Number(document.getElementById('timelineCount').getAttribute('data-tui-window'))"
    )
    assert window == TUI_EVENT_WINDOW, "the page must declare the TUI's own window size"

    page.wait_for(
        "document.querySelectorAll('#timeline [data-row]').length >= %d" % SEEDED_EVENTS,
        message="the 100-event history never loaded",
    )
    rendered = page.evaluate("document.querySelectorAll('#timeline [data-row]').length")
    assert rendered > window, f"timeline rendered {rendered}, the TUI window is {window}"
    assert rendered == SEEDED_EVENTS, (
        f"expected every seeded event exactly once (deduplicated), saw {rendered}"
    )
    assert page.evaluate(
        "Number(document.getElementById('timelineCount').getAttribute('data-count'))"
    ) == SEEDED_EVENTS

    # It scrolls: the history is taller than its viewport, and scrollTop moves.
    assert page.evaluate(
        "(function(){var t=document.getElementById('timeline');"
        "return t.scrollHeight > t.clientHeight;})()"
    ) is True
    assert page.evaluate(
        "(function(){var t=document.getElementById('timeline');t.scrollTop=140;return t.scrollTop;})()"
    ) > 0


# --------------------------------------------------------------------------
# AC4 — the org roll-up, keyed to the #151 tenant hierarchy
# --------------------------------------------------------------------------


def test_rollup_shows_per_org_rows_keyed_to_the_tenant_hierarchy(dashboard):
    page = dashboard.page
    hierarchy = json.loads(HIERARCHY.read_text(encoding="utf-8"))
    declared = {
        org["orgId"]: [tenant["tenantId"] for tenant in org["tenants"]]
        for org in hierarchy["orgs"]
    }

    page.wait_for("document.querySelectorAll('#rollupBody [data-row]').length > 0")
    rows = page.evaluate(
        "(function(){var out={};document.querySelectorAll('#rollupBody [data-kind=\"org\"]')"
        ".forEach(function(row){var r={};Array.prototype.slice.call(row.attributes)"
        ".forEach(function(a){if(a.name.indexOf('data-')===0){r[a.name.slice(5)]=a.value;}});"
        "out[row.getAttribute('data-org')]=r;});return out;})()"
    )
    assert set(rows) == set(declared), f"org rows {sorted(rows)} != declared {sorted(declared)}"

    for org_id, tenant_ids in declared.items():
        assert rows[org_id]["tenants"].split(",") == tenant_ids, org_id
        assert rows[org_id]["present"] == str(len(tenant_ids)), org_id

    # The figures are computed from the live tenant projection, not narrated:
    # recompute them here from the same API the page reads and compare.
    tenants = {row["tenantId"]: row for row in dashboard.api("/api/tenants")["tenants"]}
    for org_id, tenant_ids in declared.items():
        expected_agents = sum(int(tenants[t]["agents"]) for t in tenant_ids if t in tenants)
        expected_spend = round(sum(float(tenants[t]["usageUsd"]) for t in tenant_ids if t in tenants), 2)
        expected_budget = round(sum(float(tenants[t]["budgetUsd"]) for t in tenant_ids if t in tenants), 2)
        assert int(rows[org_id]["agents"]) == expected_agents, org_id
        assert round(float(rows[org_id]["spend-usd"]), 2) == expected_spend, org_id
        assert round(float(rows[org_id]["budget-usd"]), 2) == expected_budget, org_id
        expected_util = round(100 * expected_spend / expected_budget, 1) if expected_budget else 0.0
        assert float(rows[org_id]["utilization-pct"]) == expected_util, org_id

    # The pilot org really does aggregate three tenants, and repos roll up under them.
    assert int(rows[ORG_ELEVATEDIQ]["agents"]) > int(rows[ORG_INITECH]["agents"])
    assert "kushin77/agent-orchestrator" in rows[ORG_ELEVATEDIQ]["repos"].split(",")
    assert page.evaluate(
        "(function(){return document.querySelector('#rollupBody [data-kind=\"org\"]')"
        ".getAttribute('data-org');})()"
    ) == ORG_ELEVATEDIQ


# --------------------------------------------------------------------------
# AC5 — the page keeps working when a section's data is missing
# --------------------------------------------------------------------------


def test_the_page_survives_an_empty_projection_and_missing_sections(dashboard):
    page = dashboard.page
    result = page.evaluate(
        "(function(){"
        "var model=FleetDashboard.buildModel({frame:{},tenants:[],hierarchy:null,history:[]});"
        "FleetDashboard.render(document,model);"
        "return {"
        "rows:model.rows.length,"
        "lanes:document.querySelectorAll('#lanesBody [data-row]').length,"
        "rungs:document.querySelectorAll('#rungsBody [data-row]').length,"
        "rollup:document.querySelectorAll('#rollupBody [data-row]').length,"
        "timeline:document.querySelectorAll('#timeline [data-row]').length,"
        "emptyLanes:!!document.querySelector('#lanesBody [data-empty=\"lanes\"]'),"
        "emptyRungs:!!document.querySelector('#rungsBody [data-empty=\"rungs\"]'),"
        "emptyRollup:!!document.querySelector('#rollupBody [data-empty=\"rollup\"]'),"
        "emptyTimeline:!!document.querySelector('#timeline [data-empty=\"timeline\"]'),"
        "emptyWatchdog:!!document.querySelector('#watchdogList [data-empty=\"watchdog\"]')"
        "};})()"
    )
    assert result["rows"] == 0, "an empty projection cannot yield rows"
    for key in ("lanes", "rungs", "rollup", "timeline"):
        assert result[key] == 0, key
    for key in ("emptyLanes", "emptyRungs", "emptyRollup", "emptyTimeline", "emptyWatchdog"):
        assert result[key] is True, f"{key}: a missing section must render its own placeholder"
    assert not page.exceptions(), page.exceptions()

    # Still live: the next push repaints a complete frame over the empty one.
    frames_before = page.evaluate("window.FleetDashboard.state.frames")
    dashboard.push_frame("recover1")
    page.wait_for(
        "window.FleetDashboard.state.frames === %d" % (frames_before + 1),
        message="the page stopped consuming pushes after an empty render",
    )
    assert page.all_rows("lanesBody") >= SEEDED_LANES
    assert page.visible_rows("rungsBody") == 3
    assert page.all_rows("rollupBody") > 0
    assert not page.exceptions(), page.exceptions()


def test_a_failing_section_endpoint_does_not_break_the_rest(dashboard):
    """With /api/tenants taken away the roll-up degrades; the fleet frame stays."""
    page = dashboard.page
    result = page.evaluate(
        "(function(){"
        "var model=FleetDashboard.buildModel({tenants:[]});"
        "FleetDashboard.render(document,model);"
        "return {lanes:document.querySelectorAll('#lanesBody [data-row]').length,"
        "rollup:document.querySelectorAll('#rollupBody [data-row]').length,"
        "orgs:model.orgs.length,"
        "present:model.orgs.map(function(o){return o.present;}).join(',')};})()"
    )
    assert result["lanes"] >= SEEDED_LANES, "the fleet frame must survive a missing tenant read"
    assert result["rollup"] > 0, "the orgs are declared by the hierarchy, so they still render"
    assert result["present"] == ",".join(["0"] * result["orgs"]), (
        "an unserved tenant must aggregate zero, never a fabricated figure"
    )
    assert not page.exceptions(), page.exceptions()
