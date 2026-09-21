"""E2E suite for the fleet board view (issue #1522, step 5).

The fleet board (``GET /api/board/rows``, ``portal/server/livestore.py``)
shipped its backend in #880 with ``portal/tests/test_board_live_surface.py``
covering the loader, the schema and the route — but it had **no e2e coverage at
all** and no view. This suite gives it both: the probe in ``e2e/board_ui.py``
drives the real console app wired to the real board documents, and every
assertion below is a fact that probe *measured*.

Two of these tests exist only to prove the others can fail:

* ``test_an_absent_view_is_a_404`` — if the serving probe could never fail, the
  200 assertions would be decoration;
* ``test_the_wiring_check_goes_red_when_the_route_is_stripped`` — the wiring
  assertion reads the served bytes, and this measures that it does.
"""

from __future__ import annotations

import pytest

from e2e.board_ui import (
    VIEW_FRAMES,
    probe_absent_view_is_not_served,
    probe_board_rows_serve_the_joined_roster,
    probe_flags_off_by_default,
    probe_shell_navigation,
    probe_view_documents,
    probe_wiring_check_is_falsifiable,
)
from portal.server.config_flags import FLEET_BOARD_SURFACE


@pytest.fixture()
def tmp_board(tmp_path):
    return tmp_path


def test_the_board_frame_is_served_and_names_its_route(tmp_board):
    """The frame reaches the browser AND declares the route it calls."""
    observed = probe_view_documents(tmp_board)["board"]

    assert observed["status"] == 200, "board.html is not served"
    assert observed["isHtml"] is True, "board.html is not an HTML document"
    assert observed["bytes"] > 0, "board.html is an empty document"
    for route, wired in observed["wired"].items():
        assert wired is True, f"board.html does not reference {route}"


def test_an_absent_view_is_a_404(tmp_board):
    """Negative control: the serving probe can fail."""
    assert probe_absent_view_is_not_served(tmp_board)["status"] == 404


def test_the_wiring_check_goes_red_when_the_route_is_stripped(tmp_board):
    """Negative control: the wiring assertion measures the document.

    ``probe_wiring_check_is_falsifiable`` re-runs the same ``in`` check over a
    copy of the served bytes with the route removed. It must read as wired
    as-served and unwired once stripped, and the mutation must really have
    changed the bytes — otherwise the check would be a constant.
    """
    observed = probe_wiring_check_is_falsifiable(tmp_board)["board"]

    assert observed["servedStatus"] == 200
    assert observed["wiredAsServed"] is True
    assert observed["mutationActuallyChangedTheBytes"] is True
    assert observed["wiredWhenRouteStripped"] is False


def test_the_route_serves_the_joined_roster(tmp_board):
    """The board's data half: the roster is joined and claimed from the pair."""
    observed = probe_board_rows_serve_the_joined_roster(tmp_board)

    assert observed["status"] == 200
    assert observed["schema"] == "ao.portal-fleet-board-row/v1"
    # open and closed rows both served; order is the snapshot's, not sorted here
    assert set(observed["numbers"]) == {9, 1522}
    assert observed["claim"] == {"lane": "console", "claimed_by": "console-sme"}
    # an issue with no unreleased claim is unclaimed, not invented
    assert observed["unclaimedLane"] == ""


def test_the_route_names_the_row_it_refused(tmp_board):
    """A malformed source row is served as a named refusal, never as a row."""
    observed = probe_board_rows_serve_the_joined_roster(tmp_board)

    assert observed["rejectedIdentifiers"] == ["2"]
    assert any("is not one of" in error for error in observed["rejectedErrors"])
    # ...and the bad row is not among the served rows
    assert 2 not in observed["numbers"]


def test_the_board_surface_is_gated_off_and_invisible_unauthorised(tmp_board):
    """GR-5 over the real app: OFF by default, and 404 BEFORE authentication."""
    observed = probe_flags_off_by_default(tmp_board)[FLEET_BOARD_SURFACE]

    assert observed["configDeclaresOff"] is True
    assert observed["status"] == 404
    assert observed["code"] == "feature_disabled"


def test_the_shell_dispatches_a_view_the_rail_does_not_yet_register(tmp_board):
    """Reachability is not the same fact as rail registration, and both are read.

    The shell resolves ``?view=<id>`` to ``/views/<id>.html`` for ANY id, so a
    frame is reachable by URL even while it is absent from the nav arrays. This
    test asserts the *reader* is sound — it classifies both frames and its
    dispatch template is falsifiable — and records what the rail registers so
    the open wiring state is a measured fact rather than prose. When the nav
    registration for these two views lands, this test flips without edits.
    """
    observed = probe_shell_navigation(tmp_board)

    assert observed["shellReadable"] is True
    assert observed["dispatchPresent"] is True
    assert observed["dispatchFalsifiable"] is True, "the shell reader is vacuous"
    assert set(observed["views"]) == set(VIEW_FRAMES)
    for view, row in observed["views"].items():
        assert set(row) == {"inTenantNav", "inGlobalNav", "onRail"}
        assert row["onRail"] is (row["inTenantNav"] or row["inGlobalNav"])
