"""Pytest bootstrap + shared fixtures for the ticket-lifecycle suite.

``engine/`` has no ``__init__.py`` (a later engine-phase lane owns adding
one), so ``engine.multiagent`` is reached through the repo-root PEP-420
namespace and ``core`` is reached by putting ``engine/`` on ``sys.path`` —
the same bootstrap ``engine/core/tests/conftest.py`` uses, so the ticket
suite can import both halves it joins.

Note: the plain module names ``conftest``/``support`` are shared across
sibling test trees when suites run together, so this file is kept free of
constants other suites could collide with and the tests never import it by
name.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# engine/core/tickets/tests -> engine/core/tickets -> engine/core -> engine
_engine_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
if _engine_root not in sys.path:
    sys.path.insert(0, _engine_root)
# .../engine/core -> engine  (so ``core.tickets`` resolves as a package)
_core_parent = os.path.dirname(os.path.dirname(_here))
if _core_parent not in sys.path:
    sys.path.insert(0, _core_parent)

import pytest  # noqa: E402

from engine.multiagent.model import Lane, LaneRole, Mission  # noqa: E402

TENANT = "acme"
TICKET_ID = "TCK-1"
MISSION_ID = "mission-1"
OBJECTIVE = "restore the nightly feed"

PLANNER_LANE = Lane(lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",))
SPECIALIST_LANES = (
    Lane(lane_id="analyst", role=LaneRole.SPECIALIST, agent_ids=("analyst-1",)),
    Lane(lane_id="reviewer", role=LaneRole.SPECIALIST, agent_ids=("reviewer-1",)),
)

MISSION = Mission(
    mission_id=MISSION_ID,
    objective=OBJECTIVE,
    context={"tenant": TENANT},
)


@pytest.fixture
def tenant() -> str:
    return TENANT


@pytest.fixture
def ticket_id() -> str:
    return TICKET_ID


@pytest.fixture
def mission() -> Mission:
    return MISSION


@pytest.fixture
def planner_lane() -> Lane:
    return PLANNER_LANE


@pytest.fixture
def specialist_lanes():
    return SPECIALIST_LANES
