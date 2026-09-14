"""The registry exercised headlessly (issue #565, RC-10 of #551).

ADR-0026's acceptance for this lane requires the registry to be exercisable with
**no live plane in the gate**: no network, no TTY, no tmux, no bearer token. Every
test here renders against a committed fixture, so the same proof runs in `make
verify` as it does on an operator's laptop.

The honesty obligations of ADR-0026 D10 are the substance of these tests: an
empty panel is a claim, `NO_DATA` is never a green state, an unpromoted surface
names its flag, and a command prints a receipt or a refusal.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

import cockpit_registry as reg  # noqa: E402
import cockpit_render as renderer  # noqa: E402

REGISTRY = reg.load()
FIXTURES = renderer.load_fixtures()


def test_every_declared_function_has_a_fixture():
    assert set(FIXTURES) == set(REGISTRY.functions)


def test_every_declared_function_renders_headlessly_against_fixtures():
    frames = renderer.render_all(REGISTRY, FIXTURES)
    assert len(frames) == len(REGISTRY)
    for function in REGISTRY.ordered:
        frame = frames[function.id]
        assert frame.startswith(function.id), function.id
        assert function.title in frame, function.id
        assert "FAILED" not in frame, function.id


def test_a_declared_function_with_fixtures_is_never_rendered_as_empty():
    frames = renderer.render_all(REGISTRY, FIXTURES)
    for frame in frames.values():
        assert frame.strip().splitlines()[1].strip(), frame


def test_no_data_is_never_rendered_as_ok():
    fixture = renderer.Fixture(function_id="RUNGS", outcome="no_data")
    frame = renderer.render(REGISTRY["RUNGS"], fixture)
    assert "NO_DATA" in frame
    assert "OK" not in frame.upper().replace("NO_DATA", "")


def test_a_disabled_surface_is_invisible_and_names_its_flag():
    function = REGISTRY["RUNGS"]
    fixture = renderer.Fixture(
        function_id=function.id, outcome="disabled", flag="surfaces.fleet_projection"
    )
    frame = renderer.render(function, fixture)
    assert "disabled" in frame
    assert "surfaces.fleet_projection" in frame
    assert "surfaces.fleet_projection" in function.flags


def test_a_failed_read_names_its_reason_and_is_never_silently_empty():
    fixture = renderer.Fixture(
        function_id="RUNGS", outcome="error", reason="the projection store is absent"
    )
    frame = renderer.render(REGISTRY["RUNGS"], fixture)
    assert "FAILED" in frame
    assert "the projection store is absent" in frame


def test_a_command_renders_a_receipt_naming_its_endpoint_and_audit():
    frame = renderer.render(REGISTRY["CLOSE"], FIXTURES["CLOSE"])
    assert "receipt" in frame
    assert "POST closure/close" in frame
    assert "audit closure.close" in frame


def test_a_missing_fixture_says_so_rather_than_rendering_nothing():
    partial = {k: v for k, v in FIXTURES.items() if k != "LOG"}
    frames = renderer.render_all(REGISTRY, partial)
    assert "FAILED" in frames["LOG"]
    assert "LOG" in frames["LOG"]


def test_an_outcome_outside_the_closed_set_is_refused():
    outcomes = json.loads(
        (reg.FIXTURES / "outcomes.json").read_text(encoding="utf-8")
    )
    assert tuple(outcomes["closed"]) == renderer.OUTCOMES
    with_error = copy.deepcopy(outcomes)
    assert "maybe" not in with_error["closed"]


def test_a_fixture_with_an_unknown_outcome_is_refused(tmp_path):
    for name in ("outcomes.json", "bodies.json", "streams.json", "cockpit-console.json"):
        (tmp_path / name).write_text(
            (reg.FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    bodies = json.loads((reg.FIXTURES / "bodies.json").read_text(encoding="utf-8"))
    bodies["fixtures"]["LOG"]["outcome"] = "maybe"
    (tmp_path / "bodies.json").write_text(json.dumps(bodies), encoding="utf-8")
    try:
        renderer.load_fixtures(tmp_path)
    except renderer.RenderError as exc:
        assert "maybe" in str(exc)
    else:  # pragma: no cover - a fifth outcome must never be renderable
        raise AssertionError("an unknown outcome was accepted")


def test_a_role_workspace_renders_a_subset_of_the_declared_functions():
    workspace = renderer.render_workspace(REGISTRY, "Analyst", FIXTURES)
    ids = reg.render_workspace(REGISTRY, "Analyst")
    assert ids
    assert ids <= list(REGISTRY.functions)
    for function_id in ids:
        assert REGISTRY[function_id].title in workspace


def test_the_renderer_is_pure_no_live_plane_is_imported():
    source = (PACKAGE / "cockpit_render.py").read_text(encoding="utf-8")
    for forbidden in ("socket", "urllib", "http.client", "requests", "subprocess"):
        assert forbidden not in source, forbidden
