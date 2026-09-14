"""The plane client — the RC-3 wire, over the boundary's own transport seam.

Three things are asserted here that no end-to-end test could prove on its own:

* **the request is the declared command id** — the path is derived from the
  registry row's own ``<family>.<action>``, so there is no route table to drift;
* **the transport is the boundary's** — ADR-0016/ADR-0025 D5 (import
  ``integrations/paperclip/client.py``, never re-implement a transport) is
  asserted against the class the seam actually exports, and the seam's own offline
  ``FixtureTransport`` is exercised so the gate never touches the network;
* **the CLI writes no fleet state** — the strongest available evidence is that no
  module in the package opens a file at all, and none of the process/file
  mutation primitives appears in its source.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from _doubles import (
    ConsoleTransport,
    UnreachableTransport,
    effect_record,
    ok_envelope,
    refusal_envelope,
)

from integrations.paperclip.client import FixtureTransport, HttpTransport

from aoctl import plane as plane_module
from aoctl import vocabulary
from aoctl.contract import session_cookie_name
from aoctl.plane import Plane
from aoctl.refusals import EXIT_CANNOT_ASSESS, Refusal

PAUSE_PATH = "/api/control/fleet/pause"


@pytest.fixture()
def registry() -> vocabulary.Registry:
    return vocabulary.Registry.load()


def _plane(responses) -> tuple[Plane, ConsoleTransport]:
    transport = ConsoleTransport(responses)
    return Plane(base_url="http://plane.invalid", transport=transport), transport


# --- the request is the declared command id --------------------------------
@pytest.mark.parametrize(
    "verb,path",
    [
        ("status", "/api/control/fleet/status"),
        ("verbs", "/api/control/fleet/verbs"),
        ("pause", "/api/control/fleet/pause"),
        ("resume", "/api/control/fleet/resume"),
        ("stop", "/api/control/fleet/stop"),
        ("kill", "/api/control/fleet/kill"),
        ("override", "/api/control/fleet/override"),
        # The one row a reader would question: `audit` speaks the board's audit.
        ("audit", "/api/control/board/audit"),
    ],
)
def test_each_verb_addresses_its_declared_command_id(registry, verb, path):
    plane = Plane(transport=ConsoleTransport({}))
    request = plane.request_for(registry.row_for_verb(verb), command_id="cmd_1")
    assert (request.method, request.path) == ("POST", path)


def test_the_request_is_the_caller_key_and_the_levers_own_argv(registry):
    plane = Plane(transport=ConsoleTransport({}))
    request = plane.request_for(
        registry.row_for_verb("stop"), command_id="cmd_9", args=("--reason", "window")
    )
    assert request.body == {"commandId": "cmd_9", "args": ["--reason", "window"]}
    assert set(request.body) == {"commandId", "args"}, "the body is RC-3's shape, no more"


def test_a_minted_command_id_says_which_side_minted_it():
    minted = plane_module.mint_command_id()
    assert minted.startswith(plane_module.COMMAND_ID_PREFIX)
    assert len(minted) > len(plane_module.COMMAND_ID_PREFIX) + 16
    assert plane_module.mint_command_id() != plane_module.mint_command_id()


# --- the transport is the boundary's own (ADR-0016) ------------------------
def test_the_default_transport_is_the_boundarys_own_transport():
    plane = Plane(base_url="http://127.0.0.1:8787")
    assert isinstance(plane.transport, HttpTransport)
    assert type(plane.transport).__module__ == "integrations.paperclip.client"


def test_the_client_calls_no_transport_of_its_own():
    """The package reaches the wire only through the seam it was handed.

    Asserted on the **imports** (via ``ast``), not on the text: a docstring may
    name ``urllib`` to say that it is not used, and a substring scan would read
    that as the opposite of what it says.
    """
    package = Path(plane_module.__file__).parent
    forbidden = ("urllib", "http.client", "socket", "requests", "httplib")
    imported: set[str] = set()
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    for module in sorted(imported):
        assert not module.startswith(forbidden), f"the client imports {module}"
    assert "integrations.paperclip.client" in imported


def test_the_seams_own_offline_fixture_transport_is_enough_for_an_end_to_end_call(registry):
    """The gate's offline path: a canned response, replayed by the seam itself."""
    record = effect_record(verb="fleet.pause")
    transport = FixtureTransport(
        {"responses": [{"method": "POST", "path": PAUSE_PATH, "status": 200, "body": ok_envelope(record)}]}
    )
    plane = Plane(base_url="http://plane.invalid", transport=transport)
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    assert plane.send(request, session="session-value") == record
    assert transport.requests[0]["path"] == PAUSE_PATH


def test_the_seams_fixture_transport_refuses_a_status_with_the_status_alone(registry):
    """Which is why a refusal names the candidate set rather than guessing.

    ``FixtureTransport`` raises with the status and nothing else, so the code
    cannot be read back; the CLI names every code the status allows and takes the
    verdict those codes agree on — ``CANNOT-ASSESS`` here, exactly as the live
    path answers for the flag gate whose code it *can* read.
    """
    transport = FixtureTransport(
        {"responses": [{"method": "POST", "path": PAUSE_PATH, "status": 404, "body": None}]}
    )
    plane = Plane(base_url="http://plane.invalid", transport=transport)
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    with pytest.raises(Refusal) as caught:
        plane.send(request, session="s")
    assert caught.value.code is None
    assert caught.value.codes == ("feature_disabled", "not_found")
    assert caught.value.exit_code == EXIT_CANNOT_ASSESS
    assert "feature_disabled" in caught.value.render()


# --- the caller is the console session -------------------------------------
def test_the_cookie_is_the_console_session_and_nothing_else(registry):
    plane, transport = _plane(
        {("POST", PAUSE_PATH): (200, ok_envelope(effect_record(verb="fleet.pause")))}
    )
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    plane.send(request, session="a-session-token")
    headers = transport.requests[0]["headers"]
    assert headers == {"Cookie": f"{session_cookie_name()}=a-session-token"}
    assert "Authorization" not in headers


def test_no_session_attaches_no_cookie(registry):
    plane, transport = _plane(
        {("POST", PAUSE_PATH): (200, ok_envelope(effect_record(verb="fleet.pause")))}
    )
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    plane.send(request, session="")
    assert transport.requests[0]["headers"] == {}


def test_the_dry_run_document_never_renders_the_token(registry):
    plane = Plane(base_url="http://plane.invalid", transport=ConsoleTransport({}))
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    described = plane.describe(request, session_present=True)
    assert described["headers"]["Cookie"] == f"{session_cookie_name()}=<set>"
    assert "token-value" not in str(described)
    absent = plane.describe(request, session_present=False)
    assert "no console session" in absent["headers"]["Cookie"]


# --- the receipt, and the refusals -----------------------------------------
def test_the_receipt_is_the_planes_own_data_object(registry):
    record = effect_record(verb="fleet.pause", output="paused")
    plane, _ = _plane({("POST", PAUSE_PATH): (200, ok_envelope(record))})
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    assert plane.send(request, session="s") == record


def test_a_refusal_carries_the_code_the_plane_sent(registry):
    plane, _ = _plane(
        {
            ("POST", PAUSE_PATH): (
                403,
                refusal_envelope(403, "permission_denied", "fleet:operate at platform"),
            )
        }
    )
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    with pytest.raises(Refusal) as caught:
        plane.send(request, session="s")
    assert caught.value.code == "permission_denied"
    assert caught.value.status == 403
    assert "fleet:operate at platform" in caught.value.detail


def test_an_unreachable_plane_is_a_named_cannot_assess(registry):
    plane = Plane(base_url="http://plane.invalid", transport=UnreachableTransport("refused"))
    request = plane.request_for(registry.row_for_verb("status"), command_id="cmd_1")
    with pytest.raises(Refusal) as caught:
        plane.send(request, session="s")
    refusal = caught.value
    assert refusal.code == "plane_unreachable"
    assert refusal.exit_code == EXIT_CANNOT_ASSESS != 0
    assert refusal.verdict == "CANNOT-ASSESS"
    assert "refused" in refusal.detail


def test_a_replay_refusal_carries_the_original_receipt(registry):
    """``receipt: `` + canonical JSON, as ``control_audit.py`` documents it."""
    import json

    from aoctl.contract import receipt_marker

    original = effect_record(verb="fleet.pause", command_id="cmd_1")
    canonical = json.dumps(original, sort_keys=True, separators=(",", ":"))
    plane, _ = _plane(
        {
            ("POST", PAUSE_PATH): (
                409,
                refusal_envelope(
                    409,
                    "duplicate_command",
                    f"command 'cmd_1' for fleet.pause was already applied — a replay is "
                    f"refused rather than applied a second time; the original receipt is "
                    f"returned verbatim, {receipt_marker()}{canonical}",
                ),
            )
        }
    )
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    with pytest.raises(Refusal) as caught:
        plane.send(request, session="s")
    assert caught.value.code == "duplicate_command"
    assert caught.value.receipt == original


@pytest.mark.parametrize(
    "body",
    [
        {"ok": True, "status": 200, "data": None, "error": None},
        {"ok": False, "status": 200, "data": {}, "error": None},
        "not-an-envelope",
    ],
)
def test_a_shape_the_client_cannot_read_is_cannot_assess(registry, body):
    plane, _ = _plane({("POST", PAUSE_PATH): (200, body)})
    request = plane.request_for(registry.row_for_verb("pause"), command_id="cmd_1")
    with pytest.raises(Refusal) as caught:
        plane.send(request, session="s")
    assert caught.value.code == "plane_malformed"
    assert caught.value.exit_code == EXIT_CANNOT_ASSESS


def test_an_unusable_address_is_named():
    """An operator who forgets the scheme gets a named refusal, not a traceback."""
    plane = Plane(base_url="127.0.0.1:8787", transport=None)
    with pytest.raises(Refusal) as caught:
        _ = plane.transport
    assert caught.value.code == "address_invalid"
    assert "http(s)" in caught.value.detail


# --- the client writes nothing ---------------------------------------------
WRITE_PRIMITIVES = (
    "os.kill",
    "os.system",
    "os.remove",
    "os.rename",
    "os.replace",
    "os.mkdir",
    "os.makedirs",
    "subprocess",
    "shutil",
    "popen",
    "socket",
    ".fleet",
    "crontab",
    "tmux",
    "open(",
)


@pytest.mark.parametrize("primitive", WRITE_PRIMITIVES)
def test_the_client_never_writes_fleet_state(primitive):
    """The CLI's only reach is the plane; it opens no file and signals nothing."""
    package = Path(plane_module.__file__).parent
    sources = sorted(package.glob("*.py"))
    assert sources, "the package has no modules to scan"
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert primitive not in text, f"{path.name} references {primitive}"
