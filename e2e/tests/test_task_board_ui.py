"""E2E suite for the tenant task board view (issue #1522, step 4).

The issue asks for "a direct UI assertion for ``taskboard.html``". It names
``e2e/tests/test_workbook11_portal_surfaces.py`` as the home for it, **and that
file is held by the live ``issue-1521`` lane** (PR #1606 adds two imports and
two tests to it) — editing it would be the sibling-to-sibling clobber the
lane rule forbids. The assertion therefore lives in this sibling suite instead,
against the same probe family, so the coverage the issue asked for exists
without either lane touching the other's file.

Every assertion is a fact ``e2e/board_ui.py`` measured over the real console app
wired to a ticket the engine actually ran to ``closed``.
"""

from __future__ import annotations

import pytest

from e2e.board_ui import (
    probe_flags_off_by_default,
    probe_task_board_replays_a_real_ticket,
    probe_task_board_refuses_a_foreign_tenant,
    probe_view_documents,
    probe_wiring_check_is_falsifiable,
)
from portal.server.config_flags import TASK_BOARD_SURFACE


@pytest.fixture()
def tmp_board(tmp_path):
    return tmp_path


def test_the_task_board_frame_is_served_and_names_its_routes(tmp_board):
    """The frame is served as HTML and declares both routes it calls."""
    observed = probe_view_documents(tmp_board)["taskboard"]

    assert observed["status"] == 200, "taskboard.html is not served"
    assert observed["isHtml"] is True, "taskboard.html is not an HTML document"
    assert observed["bytes"] > 0, "taskboard.html is an empty document"
    for route, wired in observed["wired"].items():
        assert wired is True, f"taskboard.html does not reference {route}"


def test_the_task_board_wiring_is_falsifiable(tmp_board):
    """Negative control: the wiring check can go red."""
    observed = probe_wiring_check_is_falsifiable(tmp_board)["taskboard"]

    assert observed["servedStatus"] == 200
    assert observed["wiredAsServed"] is True
    assert observed["mutationActuallyChangedTheBytes"] is True
    assert observed["wiredWhenRouteStripped"] is False


def test_the_board_replays_a_ticket_the_engine_closed(tmp_board):
    """The row is the engine's replay — not a fixture, not a second store."""
    observed = probe_task_board_replays_a_real_ticket(tmp_board)

    assert observed["status"] == 200
    assert observed["schema"] == "ao.portal-task-board/v1"
    assert observed["ticketIds"] == ["TCK-E2E-642"]
    assert observed["state"] == "closed"
    assert observed["closed"] is True
    assert observed["absent"] == []
    # the vocabulary is the engine's own lifecycle_order
    assert observed["lifecycle"] == [
        "created",
        "decomposed",
        "dispatched",
        "executed",
        "reviewed",
        "closed",
    ]
    # the row records the path it actually took, and the lane it was dispatched to
    assert observed["reached"] == observed["lifecycle"]
    assert observed["dispatchLane"] == "analyst"
    assert observed["findings"] == 2


def test_the_terminal_state_offers_no_legal_move(tmp_board):
    """The state machine rendered is the model's, and it is terminal at closed."""
    observed = probe_task_board_replays_a_real_ticket(tmp_board)

    assert observed["movesStatus"] == 200
    assert observed["terminalMoves"] == []


def test_the_board_is_tenant_scoped(tmp_board):
    """A foreign tenant is refused, not served the wrong tenant's tickets.

    This is why ``taskboard`` belongs in the shell's tenant nav and ``board`` in
    its global nav: the two routes differ in scope, and the view placement must
    follow the route's scope rather than the other way round.
    """
    observed = probe_task_board_refuses_a_foreign_tenant(tmp_board)

    assert observed["status"] == 403
    assert observed["code"] == "tenant_mismatch"


def test_the_task_board_is_gated_off_and_invisible_unauthorised(tmp_board):
    """GR-5 over the real app: OFF by default, and 404 BEFORE authentication."""
    observed = probe_flags_off_by_default(tmp_board)[TASK_BOARD_SURFACE]

    assert observed["configDeclaresOff"] is True
    assert observed["status"] == 404
    assert observed["code"] == "feature_disabled"
