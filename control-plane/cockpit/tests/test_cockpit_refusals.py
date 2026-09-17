"""Receipts and refusals — every refusal code rendered and named (#566).

ADR-0026 D10.4: an action either shows the receipt the API returned, or the
refusal — including the 401/403/405/409/422/503 shapes ADR-0025 D3 fixed. An
unreachable plane, an unpermitted verb and a duplicate command each exit
non-zero with a NAMED reason — never a silent no-op.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from conftest import (  # noqa: E402
    RecordingTransport,
    effect_record,
    make_cockpit,
    ok_envelope,
    refusal_envelope,
)

PAUSE_PATH = "/api/control/fleet/pause"


@pytest.mark.parametrize(
    "status,code,expected_exit",
    [
        (401, "unauthorized", 1),
        (403, "verb_not_exposed", 1),
        (403, "scope_denied", 1),
        (403, "permission_denied", 1),
        (405, "method_not_allowed", 1),
        (409, "duplicate_command", 1),
        (422, "unknown_verb", 1),
        (503, "lever_unreachable", 2),
    ],
)
def test_each_refusal_code_is_named_and_exits_nonzero(
    registry, flags_on, status, code, expected_exit
):
    transport = RecordingTransport(
        {("POST", PAUSE_PATH): (status, refusal_envelope(status, code, "nope"))}
    )
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("PAUSE", {})
    assert exit_code == expected_exit
    assert exit_code != 0
    assert code in text


def test_an_unreachable_plane_is_named_and_exits_cannot_assess(registry, flags_on):
    transport = RecordingTransport(fail=URLError("connection refused"))
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("PAUSE", {})
    assert exit_code == 2
    assert "plane_unreachable" in text


def test_an_unpermitted_verb_is_named_and_exits_nonzero(registry, flags_on):
    transport = RecordingTransport(
        {
            ("POST", PAUSE_PATH): (
                403,
                refusal_envelope(403, "scope_denied", "caller out of platform scope"),
            )
        }
    )
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("PAUSE", {})
    assert exit_code == 1
    assert "scope_denied" in text


def test_a_duplicate_command_hands_back_the_original_receipt(registry, flags_on):
    record = effect_record(commandId="cmd_orig", verb="fleet.pause")
    message = f"already applied; receipt: {json.dumps(record, sort_keys=True)}"
    transport = RecordingTransport(
        {("POST", PAUSE_PATH): (409, refusal_envelope(409, "duplicate_command", message))}
    )
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("PAUSE", {})
    assert exit_code == 1
    assert "duplicate_command" in text
    assert "original receipt" in text
    assert "cmd_orig" in text


def test_a_receipt_is_rendered_and_exits_zero(registry, flags_on):
    transport = RecordingTransport(
        {
            ("POST", PAUSE_PATH): (
                200,
                ok_envelope(
                    effect_record(
                        commandId="cmd_ok", verb="fleet.pause", auditAction="fleet.pause"
                    )
                ),
            )
        }
    )
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("PAUSE", {})
    assert exit_code == 0
    assert "OK" in text
    assert "cmd_ok" in text
    assert "audit action" in text


def test_a_send_without_a_session_is_a_named_local_refusal(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(
        registry, transport, role="CTO", flags_registry=flags_on, session=""
    )
    exit_code, text = cockpit.one_shot("PAUSE", {})
    assert exit_code == 1
    assert "no_session" in text
    assert transport.calls == []
