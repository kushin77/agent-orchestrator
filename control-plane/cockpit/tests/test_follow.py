"""``--follow`` — the two authenticated SSE streams over the session path (#566).

ADR-0026 D3.2: a client of the two declared streams, carrying no credential of
its own — the console session cookie is attached last-moment from the supplied
session and never invented. Every non-"frames arrived" state renders a NAMED
condition: ``unreachable``, ``NO_DATA`` — never an empty pane.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import URLError

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from cockpit import _paths  # noqa: E402
from cockpit import follow as follow_module  # noqa: E402

from conftest import run_cli  # noqa: E402


def stream_frames() -> tuple[str, str]:
    fleet = (
        "event: snapshot\ndata: "
        + json.dumps({"repo": "kushin77/agent-orchestrator", "rungs": {"sister": "running"}})
        + "\n\n"
    )
    telemetry = (
        "event: telemetry\ndata: "
        + json.dumps({"kind": "dispatch", "callId": "c-1"})
        + "\n\n"
    )
    return fleet, telemetry


def make_stream_transports(
    fleet: str = "", telemetry: str = "", error: Exception | None = None
):
    fleet_transport = follow_module.FixtureSseTransport(
        {follow_module.FLEET_STREAM_PATH: fleet}, error=error
    )
    telemetry_transport = follow_module.FixtureSseTransport(
        {follow_module.TELEMETRY_STREAM_PATH: telemetry}, error=error
    )
    transports = {
        "fleet_projection": fleet_transport,
        "telemetry_live_feed": telemetry_transport,
    }
    return transports, (fleet_transport, telemetry_transport)


def test_follow_consumes_the_two_declared_streams(flags_on):
    fleet, telemetry = stream_frames()
    transports, _doubles = make_stream_transports(fleet, telemetry)
    rc, out, _err = run_cli(
        ["--follow", "--max-events", "4"],
        flags_registry=flags_on,
        stream_transports=transports,
        session="tok123",
    )
    assert rc == 0
    assert "fleet_projection" in out
    assert "snapshot" in out
    assert "telemetry_live_feed" in out
    assert "telemetry" in out
    assert "c-1" in out
    assert "follow: OK" in out


def test_follow_uses_the_session_identity_path_and_invents_no_credential(flags_on):
    transports, doubles = make_stream_transports("", "")
    rc, _out, _err = run_cli(
        ["--follow", "--max-events", "1"],
        flags_registry=flags_on,
        stream_transports=transports,
        session="tok123",
    )
    assert rc == 0
    from aoctl.contract import session_cookie_name

    for double in doubles:
        assert double.opens[0]["cookie"] == f"{session_cookie_name()}=tok123"
    # with no session supplied, no cookie is invented
    transports2, doubles2 = make_stream_transports("", "")
    run_cli(
        ["--follow", "--max-events", "1"],
        flags_registry=flags_on,
        stream_transports=transports2,
        session="",
    )
    for double in doubles2:
        assert double.opens[0]["cookie"] == ""


def test_follow_names_the_streams_from_the_declaring_modules():
    _paths.ensure_paths()
    import portal.server.fleet as fleet_module
    import portal.server.live_feed as live_module

    fleet_stream, telemetry_stream = follow_module.declared_streams()
    assert fleet_stream.surface == fleet_module.FLEET_SURFACE == "fleet_projection"
    assert fleet_stream.event == fleet_module.SSE_EVENT == "snapshot"
    assert fleet_stream.path == follow_module.FLEET_STREAM_PATH
    assert telemetry_stream.surface == live_module.LIVE_FEED_SURFACE == "telemetry_live_feed"
    assert telemetry_stream.event == live_module.SSE_EVENT == "telemetry"
    assert telemetry_stream.path == follow_module.TELEMETRY_STREAM_PATH


def test_follow_renders_an_unreachable_stream_by_name_and_exits_nonzero(flags_on):
    transports, _doubles = make_stream_transports(error=URLError("connection refused"))
    rc, out, _err = run_cli(
        ["--follow"], flags_registry=flags_on, stream_transports=transports, session="tok"
    )
    assert rc == 2
    assert "unreachable" in out
    assert "connection refused" in out
    assert "follow: CANNOT-ASSESS" in out


def test_follow_renders_no_data_by_name(flags_on):
    transports, _doubles = make_stream_transports("", "")
    rc, out, _err = run_cli(
        ["--follow", "--max-events", "1"],
        flags_registry=flags_on,
        stream_transports=transports,
        session="tok",
    )
    assert rc == 0
    assert out.count("NO_DATA") == 2


def test_the_sse_parser_never_guesses_across_chunk_boundaries():
    chunks = [
        "event: snap",
        "shot\ndata: ",
        '{"a": 1}',
        "\n\n",
    ]
    events = follow_module.parse_events(chunks)
    assert len(events) == 1
    assert events[0].event == "snapshot"
    assert events[0].data == '{"a": 1}'
