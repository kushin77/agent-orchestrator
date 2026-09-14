"""Startup flag gating and honest surface conditions (issue #566).

``surfaces.cockpit`` is read fail-closed: an absent or unreadable registry, or
an ``off`` default, all render the NAMED ``FLAG_OFF`` condition and exit
non-zero — never a silent no-op, never an empty pane that reads healthy.
A function whose own surface flag is off renders the ``disabled`` condition
naming that flag (ADR-0026 D10.3).
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from cockpit import flags as flags_module  # noqa: E402

from conftest import RecordingTransport, make_cockpit, run_cli, write_flags  # noqa: E402


def test_a_flag_off_cockpit_renders_flag_off_and_exits_nonzero(tmp_path):
    off = write_flags(tmp_path, cockpit="off")
    rc, out, _err = run_cli(["--once"], flags_registry=off)
    assert rc != 0
    assert "FLAG_OFF" in out
    assert "surfaces.cockpit" in out


def test_a_flag_off_cockpit_also_gates_a_command(tmp_path):
    off = write_flags(tmp_path, cockpit="off")
    rc, out, _err = run_cli(["PAUSE"], flags_registry=off)
    assert rc != 0
    assert "FLAG_OFF" in out


def test_a_flag_off_cockpit_gates_follow_too(tmp_path):
    off = write_flags(tmp_path, cockpit="off")
    rc, out, _err = run_cli(["--follow"], flags_registry=off)
    assert rc != 0
    assert "FLAG_OFF" in out


def test_an_absent_flag_registry_fails_closed(tmp_path):
    rc, out, _err = run_cli(
        ["--once"], flags_registry=tmp_path / "no-such-registry.yaml"
    )
    assert rc != 0
    assert "FLAG_OFF" in out


def test_an_unreadable_flag_registry_fails_closed(tmp_path):
    broken = tmp_path / "registry.yaml"
    broken.write_text("surfaces: [ this is not: valid\n", encoding="utf-8")
    rc, out, _err = run_cli(["--once"], flags_registry=broken)
    assert rc != 0
    assert "FLAG_OFF" in out


def test_a_flag_on_cockpit_starts(registry, flags_on):
    rc, out, _err = run_cli(["--once"], flags_registry=flags_on)
    assert rc == 0
    assert "cockpit" in out
    assert "BOARD" in out  # the Analyst lens renders its declared panels


def test_the_flag_off_frame_never_reads_as_healthy():
    text = flags_module.render_flag_off()
    assert "FLAG_OFF" in text
    assert "OK" not in text


def test_a_disabled_surface_is_named_when_rendered(tmp_path, registry):
    partial = write_flags(
        tmp_path,
        cockpit="on",
        surfaces={
            "remote_control": "on",
            "fleet_projection": "off",
            "telemetry_live_feed": "on",
        },
    )
    transport = RecordingTransport()
    cockpit = make_cockpit(
        registry, transport, role="VP-Eng", flags_registry=partial
    )
    frame_text = cockpit.compose()
    assert "disabled" in frame_text
    assert "surfaces.fleet_projection" in frame_text
