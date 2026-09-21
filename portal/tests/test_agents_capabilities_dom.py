"""Agents view — the Capabilities column renders chips, never "[object HTMLSpanElement]" (#1553).

WHAT IS PROVEN HERE, and by which mechanism

  AC1 *the Agents view's Capabilities cell shows the capability chips.*
      Proven in a **real browser**, never against a string of HTML this file
      wrote: the console's own stdlib HTTP server runs in-process, a genuine
      auth-gate session cookie is minted, and headless Chrome drives
      ``/views/agents.html`` over its DevTools protocol. Each cell's text is read
      back out of Blink's DOM and compared, agent by agent, against the very
      capability list the page's own ``CP.get`` returned from
      ``/api/tenants/<t>/agents`` — so the rendered DOM and the served contract
      are compared from one client, and a cell that dropped or restated a
      capability fails here.

  AC2 *the regression is a CLASS, not one call site.*
      ``CP.el`` is the console's shared DOM helper, so the pre-fix defect —
      an ARRAY child has no ``nodeType`` and became
      ``document.createTextNode(String(child))`` — hit every call site that
      passed a ``.map(...)`` result. Two live sites did: the Agents capabilities
      and the Personas capability tags. The second is not matched by the issue's
      own ``CP.el("td", {}, (`` grep, so it is pinned here as well: the helper is
      the fix, and the helper has more than one consumer.

  AC3 *the assertion can fail.*  "No ``[object `` text is rendered" is vacuous
      if the detector could not have seen it, so the detector is asserted to be
      real: stringifying a ``<span>`` IS the exact placeholder this test
      searches for, and the helper DOES flatten a nested array. Against the
      pre-fix helper the same page renders
      ``[object HTMLSpanElement],[object HTMLSpanElement],…`` and AC1 fails —
      measured by reverting ``api.js`` and re-running this file.

MECHANISM, stated plainly: a hand-written DOM shim would be a mock of the thing
under test, so the harness is the fleet dashboard's real-browser one
(``portal/tests/test_fleet_dashboard_dom.py``, issue #332) — headless Chrome
driven over a WebSocket to Chrome's own debug endpoint. The console ships
offline (no npm, no node_modules, no bundler) and carries no JS test runner, so
this is the in-repo equivalent of the Playwright row the issue proposed, and the
only kind of test that can see what a *rendering* bug actually renders.

Governance gap this closes (the issue's own finding): no view had a rendering
test. ``scripts/check-portal-view-syntax.sh`` proves every inline ``<script>``
PARSES; nothing proved a view RENDERS. This file is the first one that does.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The telemetry namespace is imported before the browser harness: the harness
# puts `fleet/` on sys.path, and `fleet/telemetry.py` then shadows the
# `telemetry/` namespace package, so `telemetry.metering` becomes "not a
# package" (measured by issue #774). The real submodules must already be in
# sys.modules before `fleet/` is importable.
import telemetry.budgets  # noqa: E402,F401
import telemetry.metering  # noqa: E402,F401
from conftest import AUTH_GATE, console_sso  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.httpd import ConsoleServer  # noqa: E402
from portal.server.sso import SESSION_COOKIE  # noqa: E402
from test_fleet_dashboard_dom import browser  # noqa: E402,F401  (the CDP harness)

SUPER_ADMIN_EMAIL = "root@platform.example.com"
TENANT = "acme"

AGENTS_VIEW = "/views/agents.html"
PERSONAS_VIEW = "/views/personas.html"
AGENTS_API = f"/api/tenants/{TENANT}/agents"
PERSONAS_API = f"/api/tenants/{TENANT}/personas"

#: The placeholder a stringified DOM node renders as — the symptom itself.
PLACEHOLDER = "[object HTMLSpanElement]"

#: Each view's "the render ran" condition. Waiting on the page's own marker
#: (rather than a sleep) is what makes a failure mean "the view did not render"
#: instead of "the test was too fast".
AGENTS_READY = (
    "document.querySelectorAll('#app table.tbl tbody tr').length > 0"
)
PERSONAS_READY = "document.querySelectorAll('#app .grid.two .card').length > 0"


class _Console:
    """The real console server (in-process, real HTTP) and the tab opener."""

    def __init__(self, browser_handle, origin: str, token: str) -> None:
        self._browser = browser_handle
        self.origin = origin
        self.token = token

    @contextlib.contextmanager
    def view(self, path: str, ready: str, tenant: str = TENANT):
        """A fresh tab on ``path`` (its own cookie), ready or it is a failure."""
        url = f"{self.origin}{path}"
        if tenant:
            url = f"{url}?tenant={tenant}"
        page = self._browser.new_page(url, cookie=(SESSION_COOKIE, self.token))
        try:
            page.wait_for(ready, message=f"{path} never rendered")
            yield page
        finally:
            page.close()

    @contextlib.contextmanager
    def agents(self):
        with self.view(AGENTS_VIEW, AGENTS_READY) as page:
            yield page

    @contextlib.contextmanager
    def personas(self):
        with self.view(PERSONAS_VIEW, PERSONAS_READY) as page:
            yield page


@pytest.fixture(scope="module")
def console(browser):  # noqa: F811 - the harness fixture is imported, and used here
    """The console under test: the repo's own app, real HTTP, a real session."""
    app = build_app(sso=console_sso())
    server = ConsoleServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Console(
            browser,
            f"http://127.0.0.1:{server.server_address[1]}",
            AUTH_GATE.mint(SUPER_ADMIN_EMAIL, TENANT),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------
# Reading the page: the API it fetched, and the DOM it built
# --------------------------------------------------------------------------


#: Every rendered row's agent id, its Capabilities cell text, and the chip texts
#: in that cell. Read from the live DOM in one evaluation, so the table cannot
#: change between the three observations.
_CELLS_JS = """
(function () {
  var out = {};
  document.querySelectorAll("#app table.tbl tbody tr").forEach(function (tr) {
    var cells = tr.querySelectorAll("td");
    if (cells.length < 4) return;
    var cell = cells[3];
    out[cells[0].textContent.trim()] = {
      text: cell.textContent,
      chips: Array.prototype.map.call(cell.querySelectorAll("span.chip"),
        function (chip) { return chip.textContent; })
    };
  });
  return out;
})()
"""


def _api(page, path: str):
    """The console's own answer for ``path``, read through the page itself."""
    return page.evaluate(
        "(async function () { var r = await CP.get(%r); return r.data; })()" % path,
        await_promise=True,
    )


def _agents_from_api(page) -> dict[str, list[str]]:
    data = _api(page, AGENTS_API)
    return {
        agent["agentId"]: list(agent["capabilities"])
        for team in data["teams"]
        for agent in team["agents"]
    }


# --------------------------------------------------------------------------
# AC1 — the Capabilities cell renders the chips the API served
# --------------------------------------------------------------------------


def test_the_capabilities_cell_renders_the_served_chips(console):
    """Every agent's cell holds exactly the capabilities the API served."""
    with console.agents() as page:
        expected = _agents_from_api(page)
        rendered = page.evaluate(_CELLS_JS)

    assert rendered, "the agents table rendered no rows — there is nothing to assert on"
    assert set(rendered) == set(expected), (
        "the rendered rows are not the agents the API served: "
        f"rendered={sorted(rendered)} served={sorted(expected)}"
    )
    for agent_id, capabilities in expected.items():
        cell = rendered[agent_id]
        assert cell["chips"] == capabilities, (
            f"{agent_id}: the cell's chips are not the served capabilities "
            f"(chips={cell['chips']!r} served={capabilities!r})"
        )


def test_no_capabilities_cell_contains_the_object_placeholder(console):
    """The symptom: the cell is the chips, not ``[object HTMLSpanElement],…``."""
    with console.agents() as page:
        rendered = page.evaluate(_CELLS_JS)

    assert rendered, "the agents table rendered no rows — there is nothing to assert on"
    offenders = {
        agent_id: cell["text"]
        for agent_id, cell in rendered.items()
        if "[object " in cell["text"]
    }
    assert not offenders, f"a Capabilities cell rendered the placeholder: {offenders}"


def test_the_registry_still_serves_agents_whose_cell_shows_several_chips(console):
    """The precondition that makes the two tests above bite.

    The defect joined the chips with a comma, so it was only *visible* on an
    agent with two or more capabilities. If the roster ever degenerated to
    single-capability agents the regression would still be real but no longer
    observable here, and this file must say so rather than pass quietly.
    """
    with console.agents() as page:
        expected = _agents_from_api(page)

    multi = {a: caps for a, caps in expected.items() if len(caps) >= 2}
    assert multi, (
        "no served agent declares two or more capabilities, so the comma-joined "
        f"placeholder this file pins could not be observed: {expected}"
    )


def test_no_text_anywhere_in_the_rendered_agents_view_is_a_placeholder(console):
    """Page-wide, not cell-wide: any leaf node holding ``[object `` is the bug."""
    with console.agents() as page:
        offenders = page.evaluate(
            """
            (function () {
              var bad = [];
              document.querySelectorAll("#app *").forEach(function (node) {
                if (node.children.length === 0 &&
                    node.textContent.indexOf("[object ") >= 0) {
                  bad.push(node.tagName + ":" + node.textContent);
                }
              });
              return bad;
            })()
            """
        )

    assert not offenders, f"the rendered view contains placeholder text: {offenders}"


# --------------------------------------------------------------------------
# AC2 — the same helper, the second call site the issue's grep missed
# --------------------------------------------------------------------------


def test_the_personas_capability_tags_render_as_chips_too(console):
    """``personas.html`` passes an array to the same helper — pinned as a class.

    The issue's step 3 greps for ``CP.el("td", {}, (`` and finds only the Agents
    site; ``views/personas.html`` passes a ``.map(...)`` array to
    ``CP.el("div", {}, …)`` and rendered the identical placeholder. Both are
    fixed by the helper alone, so both are asserted.
    """
    with console.personas() as page:
        served = {
            persona["personaId"]: list(persona["capabilityTags"])
            for persona in _api(page, PERSONAS_API)["personas"]
        }
        rendered = page.evaluate(
            """
            (function () {
              var out = {};
              document.querySelectorAll("#app .grid.two .card").forEach(function (card) {
                var badge = card.querySelector("span.badge");
                var cell = card.lastElementChild;
                if (!badge || !cell) return;
                out[badge.textContent.trim()] = {
                  text: cell.textContent,
                  chips: Array.prototype.map.call(cell.querySelectorAll("span.chip"),
                    function (chip) { return chip.textContent; })
                };
              });
              return out;
            })()
            """
        )

    assert rendered, "the personas grid rendered no cards — there is nothing to assert on"
    for persona_id, capabilities in served.items():
        card = rendered.get(persona_id)
        assert card is not None, f"persona {persona_id} was served but not rendered"
        assert "[object " not in card["text"], f"persona {persona_id}: {card['text']!r}"
        assert card["chips"] == capabilities, (
            f"persona {persona_id}: chips={card['chips']!r} served={capabilities!r}"
        )


# --------------------------------------------------------------------------
# AC3 — the detector is real, and the helper's array contract holds
# --------------------------------------------------------------------------


def test_the_placeholder_this_file_searches_for_is_what_the_defect_rendered(console):
    """The detector's own control: a stringified node IS ``[object HTMLSpanElement]``.

    Without this, ``assert "[object " not in text`` could pass because the
    placeholder this file looks for is not the one the pre-fix helper produced.
    """
    with console.agents() as page:
        assert page.evaluate("String(document.createElement('span'))") == PLACEHOLDER
        assert page.evaluate(
            """
            (function () {
              var pre_fix = document.createTextNode(
                String([document.createElement("span"), document.createElement("span")]));
              return pre_fix.textContent;
            })()
            """
        ) == f"{PLACEHOLDER},{PLACEHOLDER}"


def test_the_helper_flattens_an_array_child_and_a_nested_one(console):
    """``CP.el``'s contract, asserted on nodes rather than on rendered text."""
    with console.agents() as page:
        observed = page.evaluate(
            """
            (function () {
              var chip = function () { return CP.el("span", { class: "chip" }); };
              var flat = CP.el("div", {}, [chip(), chip()]);
              var nested = CP.el("div", {}, [chip(), [chip(), [chip()]]]);
              var mixed = CP.el("div", {}, "text ", [chip()], null, false, 0);
              return {
                flat: flat.children.length,
                nested: nested.children.length,
                nestedChips: nested.querySelectorAll("span.chip").length,
                mixedText: mixed.textContent,
                mixedChildren: mixed.children.length
              };
            })()
            """
        )

    assert observed["flat"] == 2, f"an array child was not flattened: {observed}"
    assert observed["nested"] == 3 and observed["nestedChips"] == 3, (
        f"a nested array child was not flattened: {observed}"
    )
    # The pre-existing text/null/false semantics are unchanged by the fix: a
    # number still renders as text (it never had a `nodeType`), null and false
    # still contribute nothing.
    assert observed["mixedText"] == "text 0" and observed["mixedChildren"] == 1, (
        f"the helper's non-array child semantics changed: {observed}"
    )


def test_no_view_script_still_stringifies_a_child_itself(console):
    """The fix belongs in the helper — no view may reintroduce a local copy.

    A call site that pre-joined its array (``.map(...).join("")``) would render
    text where the console asks for chips, which is the same defect wearing a
    different hat; this asserts the console has no such workaround.
    """
    views = sorted((REPO_ROOT / "portal" / "static" / "views").rglob("*.html"))
    assert views, "no view files found — the scan would be vacuous"
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{index}"
        for path in views
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "nodeType" in line or "createTextNode" in line
    ]
    assert not offenders, (
        "a view reimplements the helper's child handling instead of calling CP.el: "
        f"{offenders}"
    )
