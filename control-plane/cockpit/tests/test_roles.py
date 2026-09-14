"""The role workspaces — lenses, proven narrowing-only (issue #566).

ADR-0026 D8: CTO / VP-Eng / Manager / Analyst are arrangements of ONE function
set, scoped by identity/rbac — never four builds, never four permission
systems. A lens can only narrow: a role sees a subset of what the API would
already return for that caller, and a cross-role read is refused by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from cockpit import registry as registry_io  # noqa: E402

from conftest import RecordingTransport, make_cockpit, panel_ids  # noqa: E402


def test_roles_are_lenses_over_one_function_set(registry):
    union: set[str] = set()
    for role in registry.roles:
        ids = set(registry_io.workspace_ids(registry, role))
        assert ids, role
        assert ids <= set(registry.functions), role
        union |= ids
    # the lenses partition the ONE declared set: nothing unreachable, nothing invented
    assert union == set(registry.functions)


def test_each_workspace_renders_exactly_its_lens(registry, flags_on):
    transport = RecordingTransport()
    for role in registry.roles:
        cockpit = make_cockpit(registry, transport, role=role, flags_registry=flags_on)
        rendered = panel_ids(cockpit.compose())
        assert rendered == set(cockpit.workspace_ids()), role


def test_a_role_sees_only_a_subset_of_the_served_set(registry, flags_on):
    transport = RecordingTransport()
    for role in registry.roles:
        cockpit = make_cockpit(registry, transport, role=role, flags_registry=flags_on)
        lens = set(cockpit.workspace_ids())
        # what the API would serve this caller is at most the declared set; a
        # lens is a subset of that — narrowing, never widening.
        assert lens <= set(registry.functions), role
        frame_text = cockpit.compose()
        assert panel_ids(frame_text) == lens, role


def test_a_cross_role_read_is_refused_by_name_and_sends_nothing(registry, flags_on):
    cases = [
        ("Analyst", "HALT"),  # CTO only
        ("Analyst", "PAUSE"),  # not an Analyst function
        ("Manager", "OVERRIDE"),  # CTO only
    ]
    for role, function_id in cases:
        transport = RecordingTransport()
        cockpit = make_cockpit(registry, transport, role=role, flags_registry=flags_on)
        exit_code, text = cockpit.one_shot(function_id, {})
        assert exit_code == 1, (role, function_id)
        assert "role_lens" in text, (role, function_id)
        assert function_id in text
        assert transport.calls == []


def test_the_interactive_cross_role_read_is_refused_too(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, role="Analyst", flags_registry=flags_on)
    status = cockpit.handle_line("HALT")
    assert "role_lens" in status
    assert "HALT" in status
    assert transport.calls == []
