"""The ERP module's portal surface (ERP-07, issue #652).

WHAT IS PROVEN HERE, and by which mechanism — the issue's acceptance criteria
are claims about a *browser* and about a *flag*, so neither is asserted from a
string this file wrote:

AC1 *with the flag OFF the module is invisible and its API is refused.*
    Proven in-process over the real ``ConsoleApplication``: every route of the
    family and every one of the module's own documents answers
    ``404 feature_disabled`` **before** AuthN, with and without a session, while
    an unrelated view is still served. The control is one flag away: the same
    paths under a promoted surface answer ``401`` unauthenticated and ``200``
    authenticated, so a gate that always refused would fail here as loudly as one
    that never refused. In the shell, the module's nav entry is *offered only
    when the module answers* — read back out of Blink's DOM, so an unpromoted
    module is invisible in the console rather than a dead link in it.

AC2 *with the flag ON the module renders dashboards, document lists/forms and
    reports driven by the ERP-06 API.*
    Proven in a **real browser**: headless Chrome drives the module's own frame
    over the DevTools protocol and every claim is read back out of the live DOM
    — the dashboard's family rows, the family selector, a document list, the
    form's fields, a workflow transition that moves a row's state, and the
    reports table. Nothing here asserts on a string of HTML this file wrote.

AC3 *no projection re-derives knowledge the indexer already carries.*
    Proven as an *absence*, mechanically: the server module contains no ERP role
    name, no document family and no lifecycle state, and the frame's script
    contains no family, state or field name — every one of them is a value the
    contract served at request time. Those two scans are paired with a positive
    proof that the values really do come from the served declarations: mutating
    the manifest in a scratch copy changes the served declaration, and the
    dashboard's families are the contract's own ``kinds`` in its own order.

The harness is the fleet dashboard's (#332): it starts the console's own stdlib
HTTP server in-process and drives headless Chrome over a WebSocket to Chrome's
debug endpoint. A hand-written DOM shim would be a mock of the thing under test.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The portal server is imported BEFORE the browser harness, and the telemetry
# namespace is cached explicitly first: the harness puts `fleet/` on sys.path,
# and `fleet/telemetry.py` then shadows the `telemetry/` namespace package — so
# `telemetry.metering` becomes "not a package" (measured by issue #774). The
# real submodules must already be in sys.modules before `fleet/` is importable.
import telemetry.budgets  # noqa: E402,F401
import telemetry.metering  # noqa: E402,F401
from conftest import AUTH_GATE, console_sso  # noqa: E402
from portal.server.app import build_app  # noqa: E402
from portal.server.config_flags import (  # noqa: E402
    DECLARED_SURFACES,
    ERP_MODULE_SURFACE,
    surface_enabled,
)
from portal.server.erp import ErpModuleSurface, build_erp_api  # noqa: E402
from portal.server.httpd import ConsoleServer  # noqa: E402
from portal.server.sso import SESSION_COOKIE  # noqa: E402
from test_fleet_dashboard_dom import browser  # noqa: E402,F401  (the CDP harness)

CONFIG_RELATIVE = Path("portal") / "config" / "feature-flags.yaml"
MODULE_FRAME = "/erp/module.html"
MODULE_ROUTES = (
    "/api/erp/module",
    "/api/erp/dashboard",
    "/api/erp/reports",
    "/api/erp/reports/inventory",
    "/api/erp/documents/sales-order",
    "/api/erp/documents/sales-order/SALES-ORDER-0001",
    "/api/erp/health",
    "/api/erp/openapi.json",
)
MODULE_DOCUMENTS = (MODULE_FRAME, "/erp/module.js", "/erp/module.css")
SUPER_ADMIN_EMAIL = "root@platform.example.com"


# --------------------------------------------------------------------------
# the surface, the apps and the rule the front end must keep
# --------------------------------------------------------------------------


def _surface(*, enabled: bool) -> ErpModuleSurface:
    """The module surface wired to ERP-06's own assembly, at ``enabled``."""
    return ErpModuleSurface(
        repo_root=REPO_ROOT, enabled=enabled, api=build_erp_api(REPO_ROOT)
    )


def _call(app, method: str, path: str, *, body=None, session: bool = True):
    cookies = {SESSION_COOKIE: AUTH_GATE.mint(SUPER_ADMIN_EMAIL, "acme")} if session else {}
    response = app.handle(method, path, body=body or {}, cookies=cookies)
    payload = response.payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    return response.status, payload


@pytest.fixture(scope="module")
def off_app():
    return build_app(sso=console_sso(), erp_module_surface=_surface(enabled=False))


@pytest.fixture(scope="module")
def on_app():
    return build_app(sso=console_sso(), erp_module_surface=_surface(enabled=True))


def _error_code(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(((payload.get("error") or {}).get("code")) or "")


# --------------------------------------------------------------------------
# AC1 — the flag is the gate: absent while off, and the refusal is provable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", MODULE_ROUTES + MODULE_DOCUMENTS)
def test_with_the_flag_off_every_route_and_every_document_is_absent(off_app, path):
    """404 ``feature_disabled``, before AuthN — no session, no cookie, no hint."""
    status, payload = _call(off_app, "GET", path, session=False)
    assert status == 404, f"{path} -> {status} {payload!r}"
    assert _error_code(payload) == "feature_disabled", f"{path} -> {payload!r}"
    assert "feature-flag-gated OFF" in payload["error"]["message"]


@pytest.mark.parametrize("path", MODULE_ROUTES + MODULE_DOCUMENTS)
def test_with_the_flag_off_a_valid_session_changes_nothing(off_app, path):
    """A promoted-looking caller still cannot see an unpromoted module.

    The refusal is checked *before* the session is even read, so this is the
    same 404 a stranger receives — an unpromoted surface is absent, not merely
    unauthorised, and no credential can enumerate it.
    """
    status, payload = _call(off_app, "GET", path, session=True)
    assert status == 404 and _error_code(payload) == "feature_disabled", f"{path} -> {status}"


def test_an_unrelated_document_is_still_served_while_the_flag_is_off(off_app):
    """The control for the gate: only the module's own surface is withheld."""
    status, payload = _call(off_app, "GET", "/views/overview.html", session=False)
    assert status == 200 and isinstance(payload, str) and "<html" in payload


@pytest.mark.parametrize("path", ("/erp", "/erp/", "/erp/anything-at-all.css"))
def test_the_whole_namespace_answers_the_same_refusal_while_off(off_app, on_app, path):
    """One namespace, one answer — the bare directory included.

    Left to the static handler the bare ``/erp`` is a directory and answers
    ``404 not_found``, which is a different refusal from the
    ``feature_disabled`` the rest of the namespace gives. The pair below is the
    measurement: withheld while off, a plain ``not_found`` once promoted (so the
    refusal is the flag's and not a route that always refuses this path).
    """
    status, payload = _call(off_app, "GET", path, session=False)
    assert status == 404 and _error_code(payload) == "feature_disabled", f"{path} -> {status}"
    status, payload = _call(on_app, "GET", path, session=True)
    assert status == 404 and _error_code(payload) == "not_found", f"{path} -> {status}"


@pytest.mark.parametrize("path", MODULE_ROUTES)
def test_with_the_flag_on_the_same_routes_answer_the_module(on_app, path):
    """The promotion control: the flag, not a hardcoded 404, decides."""
    status, payload = _call(on_app, "GET", path)
    assert status == 200, f"{path} -> {status} {payload!r}"
    assert payload["ok"] is True


@pytest.mark.parametrize("path", MODULE_ROUTES + MODULE_DOCUMENTS)
def test_with_the_flag_on_the_surface_still_needs_a_session(on_app, path):
    """A promoted *API* route is 401 unauthenticated; a promoted frame is served.

    Both halves are checked so the 404 above cannot be confused with the 401 a
    promoted surface answers: the two are different statuses from different
    layers.
    """
    status, _ = _call(on_app, "GET", path, session=False)
    if path in MODULE_DOCUMENTS:
        assert status == 200, path
    else:
        assert status == 401, f"{path} -> {status}"


# --------------------------------------------------------------------------
# the declaration the gate reads, and that it fails closed
# --------------------------------------------------------------------------


def test_the_portal_declares_the_surface_off_and_keeps_its_key():
    document = yaml.safe_load((REPO_ROOT / CONFIG_RELATIVE).read_text(encoding="utf-8"))
    entry = (document.get("surfaces") or {}).get(ERP_MODULE_SURFACE)
    assert isinstance(entry, dict), "portal/config/feature-flags.yaml declares no erp_module"
    assert entry.get("default") in (False, "off"), entry.get("default")
    assert ERP_MODULE_SURFACE in DECLARED_SURFACES
    assert surface_enabled(REPO_ROOT, surface=ERP_MODULE_SURFACE) is False


def _scratch_config(tmp_path: Path, mutate) -> Path:
    document = yaml.safe_load((REPO_ROOT / CONFIG_RELATIVE).read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "feature-flags.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_the_reader_fails_closed_on_every_broken_declaration(tmp_path):
    """Absent, unreadable, wrong shape, absent entry, and an unparsable file."""
    readers = {
        "a missing file": tmp_path / "absent.yaml",
        "a directory": tmp_path,
    }
    for label, path in readers.items():
        assert surface_enabled(REPO_ROOT, config_path=path, surface=ERP_MODULE_SURFACE) is False, label

    unreadable = tmp_path / "unreadable.yaml"
    unreadable.write_text("surfaces: [this is not a mapping]\n", encoding="utf-8")
    assert surface_enabled(REPO_ROOT, config_path=unreadable, surface=ERP_MODULE_SURFACE) is False

    no_section = tmp_path / "no-section.yaml"
    no_section.write_text("schema_version: 1\n", encoding="utf-8")
    assert surface_enabled(REPO_ROOT, config_path=no_section, surface=ERP_MODULE_SURFACE) is False

    no_entry = _scratch_config(tmp_path, lambda doc: doc["surfaces"].pop(ERP_MODULE_SURFACE))
    assert surface_enabled(REPO_ROOT, config_path=no_entry, surface=ERP_MODULE_SURFACE) is False

    garbage = tmp_path / "garbage.yaml"
    garbage.write_text("surfaces: {erp_module: {default: \"on\"}\n", encoding="utf-8")
    assert surface_enabled(REPO_ROOT, config_path=garbage, surface=ERP_MODULE_SURFACE) is False


def test_an_explicit_promotion_is_the_only_thing_that_turns_the_reader_on(tmp_path):
    """The mutation that proves the reader is a reader, not a constant."""
    promoted = _scratch_config(
        tmp_path,
        lambda doc: doc["surfaces"].__setitem__(
            ERP_MODULE_SURFACE, {**doc["surfaces"][ERP_MODULE_SURFACE], "default": "on"}
        ),
    )
    assert surface_enabled(REPO_ROOT, config_path=promoted, surface=ERP_MODULE_SURFACE) is True


# --------------------------------------------------------------------------
# AC3 — the module reads its knowledge; it does not carry any
# --------------------------------------------------------------------------

#: `scripts/check-docs.sh`'s unfinished-marker rule, assembled from parts so this
#: file does not itself contain the token it searches for. Spelled exactly as
#: that gate spells it — including the leading `\b` on the third alternative
#: (issue #804), so a `mktemp` template's `XXXXXX` is not a false positive here
#: any more than it is there.
_MARKERS = "(TO" "DO|FIX" "ME|HA" "CK)\\b|\\bXX" "X\\b"


def _quoted(name: str) -> tuple[str, ...]:
    """The spellings a restated vocabulary entry would take.

    The scan is for a name written **as a literal**, not for a substring: an
    adapter may legitimately loop over ``items`` without restating the ERP family
    ``item``, and a word-boundary scan cannot tell those apart. A vocabulary the
    code carried would have to be spelled out, so the quoted forms are what is
    measured.
    """
    return (f'"{name}"', f"'{name}'")


def _restated(source: str, names, where: str) -> list[str]:
    return [name for name in names if any(form in source for form in _quoted(name))]


def test_the_server_module_carries_no_erp_vocabulary_of_its_own():
    """No role, no family and no state written as a literal — and no marker.

    The ERP vocabulary this surface renders is ERP-06's and ERP-08's. A copy of
    any of those names in the adapter is the second declaration the module
    forbids, and it is exactly the kind of drift a projection is tempted into, so
    the *absence* is what is measured. The role is read out of ERP-06's own
    signature (``erp._api_role``), which is what lets this scan pass.
    """
    source = (REPO_ROOT / "portal" / "server" / "erp.py").read_text(encoding="utf-8")
    contract = ErpModuleSurface(
        repo_root=REPO_ROOT, enabled=True, api=build_erp_api(REPO_ROOT)
    ).contract()
    kinds = (contract.get("x-erp-model") or {}).get("kinds") or []
    states = sorted(
        {
            state
            for moves in (contract.get("x-erp-transitions") or {}).values()
            for state in moves
        }
    )
    assert kinds and states, "the contract must declare a vocabulary for this scan to mean anything"
    restated = _restated(source, kinds + states + ["ERP Auditor", "ERP Clerk", "ERP Manager"],
                         "portal/server/erp.py")
    assert not restated, f"portal/server/erp.py restates {restated!r}"
    assert not re.search(_MARKERS, source)


def test_the_frame_script_carries_no_erp_vocabulary_of_its_own():
    """The front end's only source of family, state and field names is the API."""
    script = (REPO_ROOT / "portal" / "static" / "erp" / "module.js").read_text(encoding="utf-8")
    contract = ErpModuleSurface(
        repo_root=REPO_ROOT, enabled=True, api=build_erp_api(REPO_ROOT)
    ).contract()
    kinds = (contract.get("x-erp-model") or {}).get("kinds") or []
    states = sorted(
        {
            state
            for moves in (contract.get("x-erp-transitions") or {}).values()
            for state in moves
        }
    )
    fields = sorted(
        {
            name
            for kind in kinds
            for name in (((contract.get("components") or {}).get("schemas") or {}).get(kind) or {})
            .get("properties", {})
        }
    )
    assert kinds and states and fields
    restated = _restated(script, kinds + states + fields, "portal/static/erp/module.js")
    assert not restated, f"portal/static/erp/module.js restates {restated!r}"
    assert not re.search(_MARKERS, script)


def test_the_declaration_it_serves_is_the_manifest_not_a_copy(tmp_path):
    """Mutate the manifest in a scratch tree: the served declaration must follow.

    A projection that carried its own copy of the module's identity would answer
    the same document here, which is why this is a mutation and not a comparison.
    """
    scratch = tmp_path / "repo"
    (scratch / "portal" / "config").mkdir(parents=True)
    (scratch / "integrations" / "erp").mkdir(parents=True)
    shutil.copy(REPO_ROOT / CONFIG_RELATIVE, scratch / CONFIG_RELATIVE)
    manifest = (REPO_ROOT / "integrations" / "erp" / "module.yaml").read_text(encoding="utf-8")
    (scratch / "integrations" / "erp" / "module.yaml").write_text(
        manifest.replace("name: ERP\n", "name: ERP (renamed)\n", 1), encoding="utf-8"
    )
    assert "name: ERP (renamed)" in (scratch / "integrations" / "erp" / "module.yaml").read_text()

    surface = ErpModuleSurface(
        repo_root=scratch, enabled=True, api=build_erp_api(REPO_ROOT)
    )
    served = surface.module()
    assert served["module"]["name"] == "ERP (renamed)", served["module"]
    assert served["declarationSource"] == "integrations/erp/module.yaml"
    # ...and the untouched tree still serves the committed name, so the scan
    # above proved the write and not a constant.
    assert _surface(enabled=True).module()["module"]["name"] == "ERP"


def test_an_unreadable_declaration_is_cannot_assess_not_an_empty_module(tmp_path):
    scratch = tmp_path / "repo"
    scratch.mkdir()
    surface = ErpModuleSurface(repo_root=scratch, enabled=True, api=build_erp_api(REPO_ROOT))
    with pytest.raises(Exception) as refusal:
        surface.module()
    assert getattr(refusal.value, "status", None) == 503
    assert getattr(refusal.value, "code", None) == "declaration_unavailable"


# --------------------------------------------------------------------------
# the projection: every figure comes from the API or the contract
# --------------------------------------------------------------------------


def test_the_dashboard_families_are_the_contracts_kinds_in_its_own_order():
    surface = _surface(enabled=True)
    contract = surface.contract()
    dashboard = surface.dashboard()
    model = contract.get("x-erp-model") or {}
    transitions = contract.get("x-erp-transitions") or {}
    kinds = model.get("kinds") or []
    assert [family["kind"] for family in dashboard["families"]] == kinds
    for family in dashboard["families"]:
        # The contract declares transitions only for the families it lists as
        # having a lifecycle: a master family is not a family with no moves, it
        # is one the model places no workflow on, and the two are told apart by
        # the contract's own two lists rather than by an empty dict.
        assert family["transitions"] == (transitions.get(family["kind"]) or {})
        assert family["states"] == sorted(transitions.get(family["kind"]) or {})
        assert family["lifecycle"] is (family["kind"] in (model.get("lifecycleKinds") or []))
        if not family["lifecycle"]:
            assert family["states"] == [] and family["stateless"] == family["documents"]


def test_every_dashboard_figure_is_the_apis_own_answer():
    """Counts and omissions are the ERP-06 collection's, for this principal."""
    surface = _surface(enabled=True)
    dashboard = surface.dashboard()
    for family in dashboard["families"]:
        collection = surface.call("GET", f"/documents/{family['kind']}")["data"]
        assert family["documents"] == collection["count"]
        assert family["redactedFields"] == list(collection["redacted"])
        assert sum(family["byState"].values()) + family["stateless"] == collection["count"]
    assert dashboard["totals"]["documents"] == sum(
        family["documents"] for family in dashboard["families"]
    )


def test_a_refusal_from_erp_06_travels_with_its_own_code_and_status(on_app):
    """One contract, not two dialects — measured on the real console route.

    A clerk may read a sales order but may not cancel one, and a document may not
    be submitted twice. Both refusals are ERP-08's, and both arrive with the code
    and status ERP-06's own document declares for them, unchanged.
    """
    status, payload = _call(
        on_app,
        "POST",
        "/api/erp/documents/sales-order/SALES-ORDER-0001/transitions/cancel",
    )
    assert status == 403, (status, payload)
    assert payload["ok"] is False and payload["error"]["code"] == "forbidden"

    status, payload = _call(on_app, "GET", "/api/erp/documents/not-a-kind")
    assert status == 404 and payload["error"]["code"] == "unknown_document_kind"

    status, payload = _call(on_app, "POST", "/api/erp/documents/party", body={"doctype": "party"})
    assert status == 400 and payload["error"]["code"] == "schema_violation"

    status, payload = _call(on_app, "GET", "/api/erp/reports/no-such-report")
    assert status == 404 and payload["error"]["code"] == "unknown_report"


def test_a_proxied_read_omits_what_the_field_policy_withholds(on_app):
    """The field policy's read half, served through the proxy unchanged."""
    status, payload = _call(on_app, "GET", "/api/erp/documents/sales-order/SALES-ORDER-0001")
    assert status == 200
    assert "po_reference" in payload["data"]["redacted"]
    assert "po_reference" not in payload["data"]["document"]


def test_a_transition_moves_the_document_and_a_second_one_is_refused():
    """A declared move succeeds; the same move twice is refused by name.

    This test mutates, so it builds its own surface rather than sharing the
    read-only app fixture: a corpus advanced by one test must not decide what a
    later one sees.
    """
    app = build_app(sso=console_sso(), erp_module_surface=_surface(enabled=True))
    status, payload = _call(
        app,
        "POST",
        "/api/erp/documents/quotation/QUOTATION-0001/transitions/submit",
    )
    assert status == 200, (status, payload)
    assert payload["data"]["transition"]["state"] == "submitted"
    status, payload = _call(
        app,
        "POST",
        "/api/erp/documents/quotation/QUOTATION-0001/transitions/submit",
    )
    assert status == 409 and payload["error"]["code"] == "unknown_action"


# --------------------------------------------------------------------------
# AC1 + AC2 in a real browser
# --------------------------------------------------------------------------


def _serve(app):
    """The console's own stdlib server, in-process, on an ephemeral port."""
    server = ConsoleServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class _Live:
    def __init__(self, server, origin, page, token):
        self.server = server
        self.origin = origin
        self.page = page
        self.token = token

    def close(self):
        self.page.close()
        self.server.shutdown()
        self.server.server_close()


def _open(browser, app, path):
    server, origin = _serve(app)
    token = AUTH_GATE.mint(SUPER_ADMIN_EMAIL, "acme")
    page = browser.new_page(origin + path, cookie=(SESSION_COOKIE, token))
    return _Live(server, origin, page, token)


@pytest.fixture
def promoted(browser):
    live = _open(browser, build_app(sso=console_sso(), erp_module_surface=_surface(enabled=True)),
                 MODULE_FRAME)
    try:
        yield live
    finally:
        live.close()


def test_the_frame_renders_the_dashboard_families_the_contract_declares(promoted):
    page = promoted.page
    page.wait_for(
        "document.querySelectorAll('#familyRows [data-row=\"family\"]').length > 0",
        message="the dashboard never painted a family row",
    )
    kinds = page.evaluate("window.ErpModule.state.contract['x-erp-model'].kinds")
    rendered = page.evaluate(
        "(function(){var out=[];document.querySelectorAll('#familyRows [data-row=\"family\"]')"
        ".forEach(function(row){out.push([row.getAttribute('data-kind'),"
        "Number(row.getAttribute('data-documents'))]);});return out;})()"
    )
    assert [row[0] for row in rendered] == kinds, rendered
    # ERP-06's offline corpus holds one valid document per declared kind.
    assert len(kinds) == 10
    assert sum(row[1] for row in rendered) == len(kinds), rendered
    assert page.evaluate("document.getElementById('erpFlag').getAttribute('data-state')") == "on"
    assert page.evaluate("document.getElementById('erpModuleId').getAttribute('data-module')") == "erp"
    assert not page.exceptions(), page.exceptions()


def test_the_frame_lists_documents_and_filters_them(promoted):
    page = promoted.page
    page.wait_for(
        "document.querySelectorAll('#docRows [data-row=\"document\"]').length > 0",
        message="the document list never painted a row",
    )
    total = page.evaluate("Number(document.getElementById('docCount').getAttribute('data-count'))")
    visible = page.evaluate("Number(document.getElementById('docCount').getAttribute('data-visible'))")
    assert total == visible > 0

    # The filter is the contract's own state vocabulary, applied to the API's
    # rows: filtering to a state must never *add* a row.
    states = page.evaluate(
        "(function(){var out=[];document.getElementById('docState').querySelectorAll('option')"
        ".forEach(function(option){if(option.value){out.push(option.value);}});return out;})()"
    )
    assert states == sorted(states)
    assert "draft" in states and "submitted" in states
    page.set_select("docState", "submitted")
    narrowed = page.evaluate("Number(document.getElementById('docCount').getAttribute('data-visible'))")
    assert narrowed <= visible
    filtered_states = page.evaluate(
        "(function(){var out=[];document.querySelectorAll('#docRows [data-row=\"document\"]')"
        ".forEach(function(row){out.push(row.getAttribute('data-state'));});return out;})()"
    )
    assert set(filtered_states) <= {"submitted"}
    page.set_select("docState", "")


def test_the_form_fields_are_the_familys_own_schema(promoted):
    """Every field the form offers is a property the contract declares — no more."""
    page = promoted.page
    page.wait_for(
        "document.querySelectorAll('#formFields [data-field]').length > 0",
        message="the form never rendered a field",
    )
    kind = page.evaluate("document.getElementById('docKind').value")
    declared = page.evaluate(
        "Object.keys(window.ErpModule.state.contract.components.schemas[%r].properties).sort()" % kind
    )
    rendered = page.evaluate(
        "(function(){var out=[];document.querySelectorAll('#formFields [data-field]')"
        ".forEach(function(node){out.push(node.getAttribute('data-field'));});return out.sort();})()"
    )
    assert rendered == declared, (kind, rendered, declared)
    required = page.evaluate(
        "window.ErpModule.state.contract.components.schemas[%r].required" % kind
    )
    assert set(required) <= set(rendered)


def test_a_mutation_is_refused_with_the_apis_own_code_in_the_frame(promoted):
    """A form submit the model refuses shows ERP-06's code, not a generic error."""
    page = promoted.page
    page.wait_for(
        "document.querySelectorAll('#formFields [data-field]').length > 0",
        message="the form never rendered a field",
    )
    assert page.evaluate(
        "window.ErpModule.state.contract.components.schemas.party.required.indexOf('id') !== -1"
    )
    page.evaluate("document.getElementById('docKind').value = 'party'")
    page.evaluate(
        "document.getElementById('docKind').dispatchEvent(new Event('change',{bubbles:true}))"
    )
    page.wait_for(
        "document.getElementById('field-id') !== null",
        message="the form did not rebuild for the new family",
    )
    page.evaluate("document.getElementById('formSubmit').click()")
    page.wait_for(
        "document.getElementById('formResult').getAttribute('data-state') === 'refused'",
        message="an empty party document was not refused",
    )
    result = page.evaluate("document.getElementById('formResult').textContent")
    assert "schema_violation" in result, result


def test_a_workflow_transition_moves_the_rendered_row(promoted):
    """The moves are the contract's; the row's state is the API's answer."""
    page = promoted.page
    page.wait_for(
        "document.querySelectorAll('#docRows [data-row=\"document\"]').length > 0",
        message="the document list never painted a row",
    )
    row = page.evaluate(
        "(function(){var found=null;"
        "document.querySelectorAll('#docRows [data-row=\"document\"]').forEach(function(node){"
        "if(!found && node.getAttribute('data-state')==='draft'){found=node.getAttribute('data-doc');}});"
        "return found;})()"
    )
    assert row, "the fixture family must hold a draft document"
    page.evaluate(
        "(function(){var target=null;"
        "document.querySelectorAll('#docRows [data-row=\"document\"]').forEach(function(node){"
        "if(node.getAttribute('data-doc')===%r){target=node;}});"
        "target.querySelector('[data-move=\"submit\"]').click();})()" % row
    )
    page.wait_for(
        "(function(){var found=false;"
        "document.querySelectorAll('#docRows [data-row=\"document\"]').forEach(function(node){"
        "if(node.getAttribute('data-doc')===%r && node.getAttribute('data-state')==='submitted'){"
        "found=true;}});return found;})()" % row,
        message="the transition did not move the rendered row",
    )
    assert not page.exceptions(), page.exceptions()


def test_the_reports_pane_renders_from_the_same_two_sources(promoted):
    page = promoted.page
    page.wait_for(
        "document.querySelectorAll('#reportBody [data-row]').length > 0",
        message="the reports table never painted a row",
    )
    ids = page.evaluate(
        "(function(){var out=[];document.getElementById('reportPick').querySelectorAll('option')"
        ".forEach(function(option){out.push(option.value);});return out;})()"
    )
    assert ids == ["inventory", "lifecycle", "contract"], ids
    inventory = page.evaluate(
        "(function(){var out=[];document.querySelectorAll('#reportBody [data-row]')"
        ".forEach(function(node){out.push(node.getAttribute('data-kind'));});return out;})()"
    )
    assert inventory == page.evaluate("window.ErpModule.state.contract['x-erp-model'].kinds")
    page.set_select("reportPick", "contract")
    page.wait_for(
        "document.querySelectorAll('#reportBody [data-row=\"report-contract\"]').length > 0",
        message="the contract report never painted",
    )
    assert page.evaluate(
        "document.querySelectorAll('#reportBody [data-row=\"report-contract\"]').length"
    ) >= 11


def test_the_frame_egresses_nothing(promoted):
    """Every request the module's frame makes is same-origin loopback."""
    page = promoted.page
    page.wait_for(
        "(function(){return document.querySelectorAll('#familyRows [data-row]').length > 0;})()",
        message="the module never loaded",
    )
    urls = page.request_urls()
    assert urls, "the page made no requests at all"
    for url in urls:
        assert url.startswith(promoted.origin), url


def test_the_shell_offers_the_module_only_when_it_is_reachable(browser):
    """AC1 in the console's own chrome: no nav entry while the module is absent.

    The control is the promoted shell one flag away, in the same browser: the
    entry appears there, so a shell that never rendered it would fail too.
    """
    off = _open(
        browser,
        build_app(sso=console_sso(), erp_module_surface=_surface(enabled=False)),
        "/views/shell.html",
    )
    try:
        off.page.wait_for(
            "!!document.querySelector('#nav .nav-btn')",
            message="the shell never rendered its nav",
        )
        assert off.page.evaluate(
            "document.querySelectorAll('#nav [data-view=\"erp\"]').length"
        ) == 0, "an unpromoted module was offered in the shell"
        # The probe really ran and really failed: the module's API is absent.
        assert off.page.evaluate(
            "(function(){return fetch('/api/erp/module').then(function(r){return r.status;});})()",
            await_promise=True,
        ) == 404
    finally:
        off.close()

    on = _open(
        browser,
        build_app(sso=console_sso(), erp_module_surface=_surface(enabled=True)),
        "/views/shell.html",
    )
    try:
        on.page.wait_for(
            "document.querySelectorAll('#nav [data-view=\"erp\"]').length === 1",
            message="the promoted module was not offered in the shell",
        )
        assert on.page.evaluate(
            "document.querySelector('#nav [data-group=\"erp\"]').textContent"
        ) == "Modules"
    finally:
        on.close()


def test_the_module_frame_is_absent_while_the_flag_is_off(browser):
    """The document itself is refused, so the shell can never frame it."""
    live = _open(
        browser,
        build_app(sso=console_sso(), erp_module_surface=_surface(enabled=False)),
        MODULE_FRAME,
    )
    try:
        body = live.page.evaluate("document.body ? document.body.textContent : ''")
        assert "feature_disabled" in body, body
        # The refusal is the whole document: no part of the frame's own markup
        # was served, so nothing of the module is on the page to be read.
        for element in ("erpFlag", "familyRows", "docRows", "reportBody", "formFields"):
            assert element not in body, f"the refused document carried {element!r}"
        assert live.page.evaluate(
            "document.querySelectorAll('[data-row]').length"
        ) == 0
    finally:
        live.close()
