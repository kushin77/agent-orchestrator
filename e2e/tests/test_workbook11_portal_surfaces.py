"""E2E probe suite for the workbook-11 portal views (issue #642).

The probe lives in ``e2e/workbook11_portal.py``; this suite asserts its
observations. Each assertion is a fact the probe *measured* over the real
console app wired to the real producers, so a serving adapter that quietly
started re-deriving a fact (a copy of the org chart, a fixture ticket, a
hard-coded category list) turns this suite red.
"""

from __future__ import annotations

from e2e.workbook11_portal import (
    SURFACE_ROUTES,
    TICKET_ID,
    probe_flags_off_by_default,
    probe_org_chart_renders_the_declaration,
    probe_skill_studio_serves_the_studio_catalog,
    probe_task_board_replays_a_real_ticket,
)


def test_every_workbook11_surface_ships_off_and_is_invisible_unauthorised():
    """GR-5 over the real app: OFF by default, 404 before authentication."""
    observed = probe_flags_off_by_default()

    assert set(observed) == set(SURFACE_ROUTES)
    for surface, row in observed.items():
        assert row["configDeclaresOff"] is True, f"{surface} is not declared OFF"
        assert row["status"] == 404, f"{surface} served while its flag was off"
        assert row["code"] == "feature_disabled", f"{surface} refused with {row['code']}"


def test_org_chart_renders_the_committed_declaration():
    observed = probe_org_chart_renders_the_declaration()

    assert observed["status"] == 200
    assert observed["state"] == "resolved", "the committed chart must resolve"
    assert observed["root"] == "ceo"
    assert observed["roleIds"] == ["ceo", "cto", "coo", "cfo", "cmo"]
    # the governance columns are the declaration's own workbook-1 values
    assert observed["tiers"]["ceo"] == "MAX"
    assert observed["tiers"]["cfo"] == "LOW"
    assert observed["caps"]["ceo"] == 300
    assert observed["caps"]["cfo"] == 50


def test_task_board_replays_a_real_closed_ticket():
    observed = probe_task_board_replays_a_real_ticket()

    assert observed["status"] == 200
    assert observed["ticketIds"] == [TICKET_ID]
    assert observed["state"] == "closed"
    assert observed["closed"] is True
    assert observed["lifecycle"] == [
        "created",
        "decomposed",
        "dispatched",
        "executed",
        "reviewed",
        "closed",
    ]


def test_skill_studio_serves_the_studios_own_catalog():
    observed = probe_skill_studio_serves_the_studio_catalog()

    assert observed["status"] == 200
    assert observed["schema"] == "ao.portal-skill-studio/v1"
    # the category vocabulary is the workbook-9 studio's own, not a local list
    assert observed["categories"] == [
        "code-authoring",
        "code-review",
        "testing",
        "analysis",
        "data",
        "operations",
        "security",
        "communication",
    ]
    assert observed["skillCount"] == 0


def test_the_org_chart_and_task_board_are_independent_surfaces():
    """Two views, two flags: promoting one must not promote the other."""
    chart = probe_org_chart_renders_the_declaration()
    board = probe_task_board_replays_a_real_ticket()

    assert chart["status"] == 200 and board["status"] == 200
    # the chart's vocabulary is roles; the board's is ticket states — neither
    # leaks into the other's document.
    assert "ceo" in chart["roleIds"]
    assert "created" in board["lifecycle"]
    assert "ceo" not in board["lifecycle"]
