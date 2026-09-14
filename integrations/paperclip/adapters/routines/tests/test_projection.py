"""The projection: exactly one routine per scheduled entry, and the refusals."""

from __future__ import annotations

from pathlib import Path

from integrations.paperclip.adapters.routines import pmo as pmo_mod
from integrations.paperclip.adapters.routines.model import ROUTINES, RoutineSpec
from integrations.paperclip.adapters.routines.projection import project, render
from integrations.paperclip.adapters.routines.tests.conftest import build_schedule_tree


def _markers(view) -> list[str]:
    return [routine.marker for routine in view.routines]


def test_every_scheduled_entry_projects_to_exactly_one_routine(schedule_tree: Path) -> None:
    view = project(schedule_tree, pmo_root=schedule_tree)
    assert view.findings == []
    assert len(view.routines) == 3
    for routine in view.routines:
        assert routine.owner
        assert routine.trigger.raw
        assert routine.params["argv"]
        assert routine.params["log"]


def test_projection_is_deterministic_across_checkouts(tmp_path: Path) -> None:
    one = project(build_schedule_tree(tmp_path / "one"), pmo_root=tmp_path / "one")
    two = project(build_schedule_tree(tmp_path / "two"), pmo_root=tmp_path / "two")
    assert render(one) == render(two)


def test_a_routine_whose_schedule_the_code_lacks_is_refused(schedule_tree: Path) -> None:
    specs = (
        *ROUTINES,
        RoutineSpec(
            id="fleet-ghost",
            marker="ao-fleet-ghost",
            owner="fleet/ghost",
            lane="fleet-ops",
            anchor="kushin77/agent-orchestrator#1",
        ),
    )
    view = project(schedule_tree, pmo_root=schedule_tree, specs=specs)
    assert [finding.code for finding in view.findings] == ["routine-orphan-schedule"]
    assert view.findings[0].subject == "ao-fleet-ghost"
    assert "ao-fleet-ghost" not in _markers(view)


def test_a_scheduled_entry_no_routine_claims_is_reported_as_drift(tmp_path: Path) -> None:
    def add_entry(text: str) -> str:
        text = text.replace(
            "MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER)",
            'MARKERS = (MARKER, PRUNE_MARKER, RECONCILE_MARKER, "ao-fleet-extra")',
        )
        return text.replace(
            "        reconcile_line(interval),\n    ]",
            "        reconcile_line(interval),\n"
            '        "*/5 * * * * cd /tmp && /usr/bin/python3 fleet/extra.py run '
            '>> /tmp/extra.log 2>&1 # ao-fleet-extra",\n    ]',
        )

    view = project(build_schedule_tree(tmp_path, mutate=add_entry), pmo_root=tmp_path)
    assert [finding.code for finding in view.findings] == ["schedule-unprojected"]
    assert view.findings[0].subject == "ao-fleet-extra"
    assert len(view.routines) == 3


def test_a_routine_with_no_owner_fails_closed(schedule_tree: Path) -> None:
    specs = [
        RoutineSpec(id=spec.id, marker=spec.marker, owner="", lane=spec.lane, anchor=spec.anchor)
        if spec.marker == "ao-fleet-reconcile"
        else spec
        for spec in ROUTINES
    ]
    view = project(schedule_tree, pmo_root=schedule_tree, specs=specs)
    assert [finding.code for finding in view.findings] == ["routine-unowned"]
    assert view.findings[0].subject == "fleet-reconcile"


def test_a_duplicated_marker_is_not_one_routine(schedule_tree: Path) -> None:
    view = project(schedule_tree, pmo_root=schedule_tree, specs=(*ROUTINES, ROUTINES[0]))
    assert "duplicate-marker" in [finding.code for finding in view.findings]
    assert render(view)  # the document still renders; the finding is what matters


def test_the_view_agrees_with_the_pmo_graph_where_they_overlap(
    schedule_tree: Path, monkeypatch
) -> None:
    anchor = "kushin77/agent-orchestrator#237"
    view = pmo_mod.PmoView(available=True)
    view.tickets = {anchor}
    view._lanes = {anchor: "lane-x"}
    monkeypatch.setattr(pmo_mod, "load", lambda root: view)

    projection = project(schedule_tree, pmo_root=schedule_tree)
    assert [finding.code for finding in projection.findings] == ["pmo-lane-disagreement"]
    assert projection.findings[0].subject == anchor


def test_an_owner_disagreement_with_the_graph_is_a_finding(
    schedule_tree: Path, monkeypatch
) -> None:
    anchor = "kushin77/agent-orchestrator#237"
    view = pmo_mod.PmoView(available=True)
    view.tickets = {anchor}
    view._owners = {anchor: "someone-else"}
    monkeypatch.setattr(pmo_mod, "load", lambda root: view)

    projection = project(schedule_tree, pmo_root=schedule_tree)
    assert [finding.code for finding in projection.findings] == ["pmo-owner-disagreement"]


def test_an_anchor_the_graph_does_not_carry_is_reported_not_failed(
    schedule_tree: Path, monkeypatch
) -> None:
    view = pmo_mod.PmoView(available=True)
    view.tickets = set()
    monkeypatch.setattr(pmo_mod, "load", lambda root: view)

    projection = project(schedule_tree, pmo_root=schedule_tree)
    assert projection.findings == []
    assert any(note.startswith("anchor-unauditable") for note in projection.notes)


def test_an_unbuildable_pmo_is_a_note_not_a_pass_over_nothing(
    schedule_tree: Path, monkeypatch
) -> None:
    def boom(root):
        raise pmo_mod.PmoUnavailable("board snapshot missing: .board/snapshot.json")

    monkeypatch.setattr(pmo_mod, "load", boom)
    projection = project(schedule_tree, pmo_root=schedule_tree)
    assert any(note.startswith("pmo-unavailable") for note in projection.notes)
    # the schedule still projects; the agreement is the part that is unavailable
    assert len(projection.routines) == 3
