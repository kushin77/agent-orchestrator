"""Pytest bootstrap + shared fixtures for engine/multiagent tests.

``engine/`` has no ``__init__.py`` (a later engine-phase lane owns adding
one), so ``engine.multiagent`` is reached through the repo-root PEP-420
namespace: this conftest puts the repo root on ``sys.path`` (mirroring the
engine/queue, engine/memory and identity/* conftests).  That also makes the
integration seams importable (``engine.core``, ``engine.queue``) from any
cwd.

Note: this tests directory is deliberately NOT prepended to ``sys.path`` here
and the tests never import ``conftest``/``support`` by a bare name — the
plain module names ``conftest`` and ``support`` are shared across sibling
test trees when suites run together (engine/core, engine/queue), so fixtures
are referenced only through pytest's fixture mechanism and shared plan
builders live in each test module.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# engine/multiagent/tests -> engine/multiagent -> engine -> repo root
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import pytest  # noqa: E402

from engine.multiagent.model import Lane, LaneRole, Mission  # noqa: E402

PLANNER_LANE = Lane(
    lane_id="planner",
    role=LaneRole.PLANNER,
    persona="lead",
    specialization="decomposition",
    agent_ids=("planner-1",),
)

SPECIALIST_LANES = (
    Lane(
        lane_id="specialist-a",
        role=LaneRole.SPECIALIST,
        persona="analyst",
        specialization="facts",
        agent_ids=("s-a-1",),
    ),
    Lane(
        lane_id="specialist-b",
        role=LaneRole.SPECIALIST,
        persona="reviewer",
        specialization="risk",
        agent_ids=("s-b-1",),
    ),
    Lane(
        lane_id="specialist-c",
        role=LaneRole.SPECIALIST,
        persona="builder",
        specialization="implementation",
        agent_ids=("s-c-1",),
    ),
)

VOTER_LANES = (
    Lane(lane_id="voter-a", role=LaneRole.VOTER, persona="reviewer", agent_ids=("v-a-1",)),
    Lane(lane_id="voter-b", role=LaneRole.VOTER, persona="auditor", agent_ids=("v-b-1",)),
    Lane(lane_id="voter-c", role=LaneRole.VOTER, persona="policy", agent_ids=("v-c-1",)),
)

MISSION = Mission(
    mission_id="mission-1",
    objective="assess launch risk",
    context={"domain": "fintech"},
)


@pytest.fixture
def planner_lane() -> Lane:
    return PLANNER_LANE


@pytest.fixture
def specialist_lanes():
    return SPECIALIST_LANES


@pytest.fixture
def voter_lanes():
    return VOTER_LANES


@pytest.fixture
def mission() -> Mission:
    return MISSION
