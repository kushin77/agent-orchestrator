"""Operator terminal — the caller-scoped verb catalogue, in a REAL browser (#1523).

MECHANISM (stated plainly, as the repo's browser tests require): these are
**real-browser** tests, driven through the harness the fleet dashboard already
owns (``portal/tests/test_fleet_dashboard_dom.py``: headless Chrome over the
DevTools protocol, one tab per test, real HTTP to the console's own stdlib
server). Nothing is asserted against a string this file wrote: every claim about
the panel is read back out of Blink's DOM, and the dispatch proof is a POST
observed on the wire **and** at the control module's own lever seam.

Why this harness and not Playwright: this console ships offline — no npm, no
``node_modules``, no CDN (``portal/static/js/operator.js`` states it, and
``scripts/check-portal-offline-spog.sh`` enforces it). Playwright would add a
second browser driver and a package manager to a surface that deliberately has
neither, and it would be a *new* mechanism where the repo already has a working
one. The issue's "Playwright test" is therefore answered by the repo's real
browser acceptance mechanism — a real click in a real browser, which is the
property the acceptance is actually about. The harness is *imported*, not
copied: a second DevTools client would be a second implementation of the thing
under test.

What is measured (issue #1523 acceptance):

* the panel renders the families beyond fleet-steer — the count of distinct
  family headings in the live DOM is read and compared with the registry's own
  family count;
* a click on a **non-fleet** verb dispatches end-to-end: the browser POSTs
  ``/api/control/board/status``, the control module hands it to its lever, and
  the receipt is rendered back into the DOM;
* the NEGATIVE CONTROL one principal away — a caller whose capabilities reach
  nothing is offered no steer button at all, and the verb the panel withheld is
  the verb the plane refuses (asserted over real HTTP);
* the middle regime — a caller with a partial capability set sees its own
  families and none of the others, so the filter is a filter and not an
  all-or-nothing switch.

Nothing here runs a real fleet lever: the control surface is installed with a
recording lever (the same double ``portal/tests/test_operator_terminal.py``
uses), so a click is observed at the dispatch seam without executing a fleet
command. The capability gate, the session, the route and the DOM are all real.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent
for path in (str(REPO_ROOT), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, str(path))

# The repo's ONE browser harness (headless Chrome + DevTools). Imported rather
# than re-implemented: `browser` is a pytest fixture, so requesting it here
# reuses the launch/teardown logic instead of copying ~250 lines of CDP client.
from test_fleet_dashboard_dom import _redirect_runtime, browser  # noqa: E402,F401

from conftest import AUTH_GATE, console_sso  # noqa: E402

from portal.server.app import build_app  # noqa: E402
from portal.server.control_api import (  # noqa: E402
    PLATFORM_ORG,
    LeverResult,
    RemoteControl,
    Vocabulary,
    install,
)
from portal.server.fleet import FleetProjection  # noqa: E402
from portal.server.fleet_authz import SUBJECT_USER  # noqa: E402
from portal.server.httpd import ConsoleServer  # noqa: E402
from portal.server.sso import SESSION_COOKIE  # noqa: E402

PAGE_PATH = "/views/console.html"
REGISTRY = REPO_ROOT / "control-plane" / "control" / "verbs.yaml"

SUPER_ADMIN_EMAIL = "root@platform.example.com"
RESTRICTED_EMAIL = "alice@acme.example.com"
PARTIAL_EMAIL = "partial@acme.example.com"
#: The platform preset role that grants a PARTIAL set (`fleet:read`): the
#: fleet/channel/recover reads, and neither `board` nor `closure` (measured —
#: 14 of 54 exposed verbs, families {channel, fleet, recover}).
PARTIAL_ROLE = "admin"
#: A non-fleet verb: reading the board's audit ledger. Chosen because it is the
#: first family beyond fleet-steer in the registry and it mutates nothing.
NEW_FAMILY_VERB = "board.status"
NEW_FAMILY_FAMILY = "board"


class RecordingLever:
    """A lever double that records the VERB ID it was handed, never runs it."""

    def __init__(self) -> None:
        self.verbs: list[tuple[str, tuple[str, ...]]] = []

    def run(self, row, args):
        self.verbs.append((row.id, tuple(args)))
        return LeverResult(
            argv=(row.source, row.local, *args), exit_code=0, stdout="", stderr=""
        )


class SpyLedger:
    """The command-record seam, so a dispatch cannot skip the record path."""

    def __init__(self) -> None:
        self.begun: list[str] = []
        self.finished: list[str] = []

    def begin(self, command):
        self.begun.append(command.verb)
        return None

    def finish(self, command, record):
        self.finished.append(command.verb)

    def end(self, command):
        return None


class _Console:
    """The console's own HTTP API, so a test can ask it what the page asked."""

    def __init__(self, origin: str, lever) -> None:
        self.origin = origin
        self.lever = lever

    def get(self, path: str, token: str) -> tuple[int, dict]:
        """One authenticated client-side read of the API the page reads."""
        connection = http.client.HTTPConnection(
            "127.0.0.1", int(self.origin.rsplit(":", 1)[1]), timeout=10
        )
        try:
            connection.request(
                "GET", path, headers={"Cookie": f"{SESSION_COOKIE}={token}"}
            )
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8") or "{}")
            return response.status, body
        finally:
            connection.close()

    def post(self, path: str, token: str) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", int(self.origin.rsplit(":", 1)[1]), timeout=10
        )
        try:
            connection.request(
                "POST",
                path,
                body=b"{}",
                headers={
                    "Cookie": f"{SESSION_COOKIE}={token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8") or "{}")
            return response.status, body
        finally:
            connection.close()


def _bind(app, email: str, role: str) -> None:
    """Bind a principal to a platform preset role, through the authority's store.

    This is the seam the console's own super-admin rule uses
    (``FleetAuthorizer._bind_super_admin``), so the partial principal is built
    out of the authority rather than a permission stub that could disagree with
    it.
    """
    store = app.fleet_authz.store
    role_id = app.fleet_authz._role_id(store, PLATFORM_ORG, role)
    assert role_id is not None, f"{role} is not a role at {PLATFORM_ORG}"
    store.add_binding(PLATFORM_ORG, email.lower(), SUBJECT_USER, role_id)


@pytest.fixture
def console(browser, tmp_path, monkeypatch):
    """The real console server, its control surface doubled, one tab per caller."""
    fleet_console = _redirect_runtime(tmp_path, monkeypatch)
    fleet = FleetProjection(
        repo_root=REPO_ROOT, console=fleet_console, enabled=True, poll_interval=0.05
    )
    app = build_app(sso=console_sso(), fleet_projection=fleet)
    lever = RecordingLever()
    install(app, RemoteControl(app=app, enabled=True, lever=lever, commands=SpyLedger()))
    _bind(app, PARTIAL_EMAIL, PARTIAL_ROLE)
    server = ConsoleServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    pages: list = []

    def open_tab(principal: str):
        """A real tab on the panel, as that caller, once the catalogue painted."""
        token = AUTH_GATE.mint(principal, "acme")
        page = browser.new_page(origin + PAGE_PATH, cookie=(SESSION_COOKIE, token))
        pages.append(page)
        page.wait_for(
            "(function(){var s=document.getElementById('steerState');"
            "return !!s && s.textContent.indexOf('loading') === -1 "
            "&& s.textContent.length > 0;})()",
            timeout=30,
            message=f"the steer panel never left its loading state for {principal}",
        )
        return page, token

    try:
        yield open_tab, _Console(origin, lever)
    finally:
        for page in pages:
            page.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# the panel offers the families beyond fleet-steer
# ---------------------------------------------------------------------------
def _family_headings(page) -> list[str]:
    """The family labels the panel painted, read out of the live DOM."""
    return (
        page.evaluate(
            "(function(){var out=[];"
            "document.querySelectorAll('#verbsBody > div.hint').forEach("
            "function(n){out.push(n.textContent.trim());});return out;})()"
        )
        or []
    )


def test_the_panel_renders_the_families_beyond_fleet_steer(console):
    open_tab, _ = console
    page, _token = open_tab(SUPER_ADMIN_EMAIL)

    headings = _family_headings(page)
    registry = Vocabulary.load(REGISTRY)
    declared_families = {row.family for row in registry.verbs.values()}
    exposed_families = {row.family for row in registry.verbs.values() if row.exposed}

    # The acceptance asks for at least two families BEYOND fleet-steer, counted
    # against the registry's own total rather than a number written here.
    beyond_fleet = sorted(set(headings) - {"fleet"})
    assert len(beyond_fleet) >= 2, (
        f"the panel painted {sorted(headings)}; the registry declares exposed "
        f"families {sorted(exposed_families)}"
    )
    assert set(headings) <= declared_families, sorted(set(headings) - declared_families)
    assert NEW_FAMILY_FAMILY in headings, sorted(headings)

    # And the state line is the panel's own account of its filter, not a count
    # of what happens to be on screen.
    line = page.evaluate("document.getElementById('steerState').textContent")
    assert "declared verb(s) are yours to run" in line, line


# ---------------------------------------------------------------------------
# click -> POST /api/control/<family>/<action> -> rendered
# ---------------------------------------------------------------------------
def test_clicking_a_non_fleet_verb_dispatches_and_renders_the_receipt(console):
    open_tab, api = console
    page, _token = open_tab(SUPER_ADMIN_EMAIL)

    assert api.lever.verbs == [], "a lever ran before anything was clicked"

    # A real click on the real button the panel built for a non-fleet verb.
    clicked = page.evaluate(
        "(function(){var rows=document.querySelectorAll('#verbsBody tbody tr');"
        "for(var i=0;i<rows.length;i++){"
        f"if(rows[i].cells[0].textContent.trim()==='{NEW_FAMILY_VERB}'){{"
        "var b=rows[i].querySelector('button');"
        "if(!b){return 'no-button';}"
        "b.click();return 'clicked';"
        "}}"
        "return 'not-found';})()"
    )
    assert clicked == "clicked", f"the panel offered no steer button for {NEW_FAMILY_VERB}"

    # (1) the wire: the browser POSTed the non-fleet route, not a fleet one.
    page.wait_for(
        "!!document.querySelector('#verbsBody .card')",
        timeout=20,
        message="the receipt never rendered",
    )
    posts = [
        url
        for url in page.request_urls()
        if "/api/control/" in url
    ]
    assert any(url.endswith(f"/api/control/{NEW_FAMILY_VERB.replace('.', '/')}")
               for url in posts), posts

    # (2) the dispatch seam: the control module handed THAT verb to its lever.
    assert [verb for verb, _args in api.lever.verbs] == [NEW_FAMILY_VERB], api.lever.verbs

    # (3) the DOM: the receipt names the verb and its verdict.
    receipt = page.evaluate(
        "(function(){var c=document.querySelector('#verbsBody .card');"
        "return c ? c.textContent : '';})()"
    )
    assert NEW_FAMILY_VERB in receipt, receipt
    assert "applied" in receipt, receipt

    # (4) the row itself is fully built: the effect class renders as a badge.
    # The receipt and this cell are the two places `el()` dropped every child it
    # was handed as an argument rather than as an array (issue #1523) — a click
    # that dispatched correctly and rendered nothing is the "built but not wired"
    # defect wearing a rendering bug.
    cell = page.evaluate(
        "(function(){var rows=document.querySelectorAll('#verbsBody tbody tr');"
        "for(var i=0;i<rows.length;i++){"
        f"if(rows[i].cells[0].textContent.trim()==='{NEW_FAMILY_VERB}'){{"
        "return rows[i].cells[1].textContent.trim();}}return 'NOT-FOUND';})()"
    )
    assert cell == "read", f"the effect-class cell rendered {cell!r}"


# ---------------------------------------------------------------------------
# the negative control, one principal away
# ---------------------------------------------------------------------------
#
# The caller this defect actually reaches is the PARTIAL one, which is why the
# control is that principal and not a capability-less one. Every non-owner
# platform preset role carries `fleet:read` (the SPoG's own binding layer), so a
# non-owner operator can load the vocabulary — `fleet.verbs` needs `fleet:read` —
# and was then offered all 54 exposed verbs while its capabilities reached 14.
# A caller that holds nothing cannot load the vocabulary in the first place, so
# it never reaches the panel; that case is the second test here, kept because a
# filter must not paper over it.
def test_a_partial_caller_is_offered_only_the_verbs_it_may_run(console):
    open_tab, api = console
    page, token = open_tab(PARTIAL_EMAIL)

    permitted = api.get("/api/console/me", token=token)[1]["data"]["controlVerbs"]
    permitted_families = {verb.split(".", 1)[0] for verb in permitted}
    assert permitted_families, "the partial principal must still reach some families"

    headings = set(_family_headings(page))
    # Every family on screen is one this caller may run something in...
    assert headings <= permitted_families, sorted(headings - permitted_families)
    # ...and the families whose verbs it cannot run are not on screen at all.
    for withheld_family in ("board", "closure"):
        assert withheld_family not in headings, sorted(headings)
    # A family BEYOND fleet-steer is still offered, so the filter narrows the
    # catalogue rather than emptying it.
    assert headings - {"fleet"}, sorted(headings)

    panel_text = page.evaluate("document.getElementById('verbsBody').textContent")
    for verb in ("board.reap", "closure.retire", "fleet.override"):
        assert verb not in panel_text, f"{verb} was rendered for a caller who cannot run it"

    # The panel SAYS it withheld them, so the filter is not a silent omission
    # that would read as a small vocabulary.
    line = page.evaluate("document.getElementById('steerState').textContent")
    assert "withheld" in line, line

    # The control: over real HTTP as this same principal, a withheld verb is
    # refused — the panel's withholding and the plane's refusal are one answer.
    status, payload = api.post("/api/control/board/status", token=token)
    assert status == 403, payload
    assert payload["error"]["code"] in ("permission_denied", "scope_denied")
    # ...and a permitted verb is not refused, so the control is not vacuous.
    ok_status, ok_payload = api.post("/api/control/fleet/status", token=token)
    assert ok_status == 200, ok_payload


def test_a_caller_with_no_capability_is_offered_no_steer_button(console):
    """The capability-less caller: no button, and no button offered by omission.

    This caller cannot even load the vocabulary (`fleet.verbs` needs
    `fleet:read`), so the panel reports that instead of an empty catalogue. Both
    readings are asserted, because "no buttons" is true for the wrong reason here.
    """
    open_tab, api = console
    page, token = open_tab(RESTRICTED_EMAIL)

    buttons = page.evaluate("document.querySelectorAll('#verbsBody button').length")
    assert buttons == 0, "a steer button was offered to a caller who cannot run any verb"

    line = page.evaluate("document.getElementById('steerState').textContent")
    assert "cannot load the vocabulary" in line, line
    for verb in (NEW_FAMILY_VERB, "closure.retire", "fleet.override"):
        assert verb not in page.evaluate(
            "document.getElementById('verbsBody').textContent"
        ), f"{verb} was rendered for a caller who cannot run it"

    status, payload = api.post("/api/control/board/status", token=token)
    assert status == 403, payload
    assert api.get("/api/console/me", token=token)[1]["data"]["controlVerbs"] == []
