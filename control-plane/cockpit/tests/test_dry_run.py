"""``--dry-run`` prints the request it would send and sends nothing (#566).

The acceptance is asserted, not promised: the transport records every call, and
every test here proves the record is empty after a dry run.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from conftest import RecordingTransport, make_cockpit, run_cli  # noqa: E402


def test_dry_run_prints_the_request_and_sends_nothing(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("PAUSE", {}, dry_run=True, command_id="cmd_dry")
    assert exit_code == 0
    assert "DRY-RUN" in text
    assert "nothing was sent" in text
    assert "POST" in text
    assert "/api/control/fleet/pause" in text
    assert '"commandId": "cmd_dry"' in text
    assert transport.calls == []


def test_dry_run_needs_no_session(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(
        registry, transport, role="CTO", flags_registry=flags_on, session=""
    )
    exit_code, text = cockpit.one_shot("PAUSE", {}, dry_run=True)
    assert exit_code == 0
    assert transport.calls == []


def test_dry_run_of_an_irreversible_command_names_the_confirmation(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, role="CTO", flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("OVERRIDE", {"issue": 566}, dry_run=True)
    assert exit_code == 0
    assert "irreversible" in text
    assert "--confirm OVERRIDE" in text
    assert transport.calls == []


def test_the_cli_dry_run_prints_and_sends_nothing(tmp_path, registry, flags_on):
    transport = RecordingTransport()
    rc, out, _err = run_cli(
        ["--dry-run", "--command-id", "cmd_x", "--role", "CTO", "OVERRIDE", "issue=566"],
        flags_registry=flags_on,
        transport=transport,
    )
    assert rc == 0
    assert "DRY-RUN" in out
    assert "/api/control/fleet/override" in out
    assert transport.calls == []
