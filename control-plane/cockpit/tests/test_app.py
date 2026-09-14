"""The interactive app and the CLI's remaining seams (issue #566).

The alternate screen is entered and left exactly once (``fleet/console.py``'s
technique); the ticker ack goes through the control API as the DECLARED
``channel.send``; an unreadable registry is CANNOT-ASSESS, named.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from cockpit import frame as frame_module  # noqa: E402

from conftest import (  # noqa: E402
    RecordingTransport,
    effect_record,
    make_cockpit,
    ok_envelope,
    run_cli,
)

SEND_PATH = "/api/control/channel/send"


def test_once_renders_one_frame_with_declared_panels(registry, flags_on):
    rc, out, _err = run_cli(["--once"], flags_registry=flags_on)
    assert rc == 0
    assert "cockpit" in out
    assert "BOARD" in out
    assert "alerts" in out
    assert "drill" in out


def test_the_interactive_loop_enters_and_leaves_the_alternate_screen(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    out = io.StringIO()
    rc = cockpit.run(out, keys=iter(["?", "q"]), color=False)
    assert rc == 0
    rendered = out.getvalue()
    assert frame_module.ENTER_SCREEN in rendered
    assert frame_module.LEAVE_SCREEN in rendered
    assert "cockpit" in rendered
    assert "MNEMONIC" in rendered  # the help frame was redrawn after "?"


def test_help_and_workspace_keys_are_one_keystroke(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    assert "MNEMONIC" in cockpit.handle_line("?")
    assert cockpit.handle_line("c") == "workspace CTO (a lens over the same function set)"
    assert cockpit.role == "CTO"
    assert cockpit.handle_line("a") == "workspace Analyst (a lens over the same function set)"
    assert cockpit.role == "Analyst"


def test_the_ticker_ack_goes_through_the_control_api(registry, flags_on):
    transport = RecordingTransport(
        {
            ("POST", SEND_PATH): (
                200,
                ok_envelope(
                    effect_record(
                        commandId="cmd_ack", verb="channel.send", auditAction="channel.send"
                    )
                ),
            )
        }
    )
    cockpit = make_cockpit(registry, transport, role="VP-Eng", flags_registry=flags_on)
    status = cockpit.handle_line("k")
    assert "OK" in status
    assert transport.calls[-1]["path"] == SEND_PATH
    assert transport.calls[-1]["body"]["args"] == ["--message", "ack a-1"]
    frame_text = cockpit.compose()
    assert "acked" in frame_text
    assert "cmd_ack" in frame_text


def test_the_ticker_ack_is_refused_outside_the_lens(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, role="Analyst", flags_registry=flags_on)
    status = cockpit.handle_line("k")
    assert "role_lens" in status
    assert transport.calls == []


def test_an_interactive_command_renders_a_receipt(registry, flags_on):
    transport = RecordingTransport(
        {
            ("POST", "/api/control/fleet/pause"): (
                200,
                ok_envelope(
                    effect_record(
                        commandId="cmd_9", verb="fleet.pause", auditAction="fleet.pause"
                    )
                ),
            )
        }
    )
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    status = cockpit.handle_line("PAUSE")
    assert "OK" in status
    assert "cmd_9" in status


def test_an_unreadable_registry_is_named_cannot_assess(tmp_path, flags_on):
    rc, out, err = run_cli(
        ["--once"],
        flags_registry=flags_on,
        registry_path=str(tmp_path / "missing-functions.yaml"),
    )
    assert rc == 2
    assert "registry_unreadable" in err
    assert out == ""
