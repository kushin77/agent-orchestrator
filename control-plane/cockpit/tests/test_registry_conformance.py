"""The registry-conformance gate (issue #566).

ADR-0026 D9/D10: every rendered function and every accepted mnemonic resolves
to a DECLARED RC-10 entry — and an undeclared one is refused BY NAME. This file
is the gate: it renders the cockpit headlessly (the same fixture technique
``cockpit_render`` proves), walks every frame's real text, and provokes the
mutant — a fixture whose level map names a function the registry does not
declare.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from cockpit import app as app_module  # noqa: E402
from cockpit import drill as drill_module  # noqa: E402
from cockpit import registry as registry_io  # noqa: E402
from cockpit import ticker as ticker_module  # noqa: E402

from conftest import RecordingTransport, make_cockpit, panel_ids  # noqa: E402


def test_every_rendered_panel_is_declared(registry, flags_on):
    transport = RecordingTransport()
    for role in registry.roles:
        cockpit = make_cockpit(registry, transport, role=role, flags_registry=flags_on)
        frame_text = cockpit.compose()
        rendered = panel_ids(frame_text)
        assert rendered, role
        assert rendered == set(cockpit.workspace_ids()), role
        assert rendered <= set(registry.functions), role
        assert "FAILED" not in frame_text, role


def test_every_accepted_mnemonic_resolves_to_a_declared_entry(registry):
    reg_module, _ = registry_io.modules()
    for function_id in registry.functions:
        call, findings = reg_module.resolve_call(registry, function_id, {})
        assert not any(
            finding.code == "UNKNOWN-FUNCTION" for finding in findings
        ), function_id
        if not findings:
            assert call is not None, function_id
            assert call.endpoint_path == f"/api/control/{call.endpoint}", function_id


def test_an_undeclared_rendered_function_is_refused_by_name(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    _, render_module = registry_io.modules()
    fixture = render_module.Fixture(function_id="BOGUS", outcome="ok", rows=["x"])
    frame_text = cockpit.panel_frame("BOGUS", fixture)
    assert "UNDECLARED-FUNCTION" in frame_text
    assert "BOGUS" in frame_text


def test_an_undeclared_mnemonic_is_refused_by_name_and_sends_nothing(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    exit_code, text = cockpit.one_shot("BOGUS", {})
    assert exit_code == 1
    assert "undeclared_function" in text
    assert "BOGUS" in text
    assert transport.calls == []


def test_the_mutant_fixture_is_refused_by_name(registry):
    document = json.loads(drill_module.DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    document["levels"]["tool-call"] = "BOGUS"
    source = drill_module.FixtureDrillSource(document=document)
    drill = drill_module.Drill(source=source, level_functions=dict(source.level_functions))
    drill.path = [
        ("org", source.root_title()),
        ("lane", "control-plane"),
        ("issue", "#566 the terminal cockpit"),
        ("agent", "ao-rc11"),
        ("call", "c-1 plan the lane"),
        ("tool-call", "read functions.yaml"),
    ]
    frame_text = drill.render(registry)
    assert "UNDECLARED-FUNCTION" in frame_text
    assert "BOGUS" in frame_text


def test_the_committed_drill_levels_name_only_declared_functions(registry):
    source = drill_module.FixtureDrillSource()
    assert set(drill_module.LEVELS) == set(source.level_functions)
    assert set(source.level_functions.values()) <= set(registry.functions)


def test_the_ticker_ack_is_a_declared_command(registry):
    assert ticker_module.ACK_FUNCTION in registry.functions
    assert registry.functions[ticker_module.ACK_FUNCTION].kind == "command"
    function_id, given = ticker_module.ack_call(
        ticker_module.Alert(id="a-9", severity="warn", text="t", source="s")
    )
    assert function_id == "SEND"
    assert given == {"message": "ack a-9"}


def test_the_cockpit_keys_are_not_mnemonics(registry):
    for key in app_module.COCKPIT_KEYS:
        assert key not in registry.functions, key
