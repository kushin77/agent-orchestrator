"""portal tenant task-board tests (issue #642, workbook-11, server half).

Proves, against the served API, the acceptance criterion the serving half can
prove end to end:

* the board renders the **workbook-3 lifecycle** by replaying the engine's own
  event log through the real ``TicketRuntime`` — a ticket driven to ``closed``
  shows all six states, and the board keeps no ticket state of its own;
* the lifecycle vocabulary served to the client is the **model's** (the state
  order and the legal next moves), so a board cannot offer a transition the
  engine would refuse;
* a ticket the engine's store does not hold is **absent** (404) — an empty log
  is not a ticket, and the board must not project one into existence;
* the board is **feature-flag-gated, ON by default** (GR-5 reversal 2026-09-21), before authN, and is
  bound to its own tenant.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Tuple

import pytest
import yaml
from conftest import REPO_ROOT, console_sso, login_as

# ``core.tickets`` is reached with ``engine/`` on ``sys.path`` (its own lane
# convention); the portal's conftest bootstraps only the repo root, so this
# suite adds the pillar root it consumes. Idempotent and suite-local.
if str(REPO_ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "engine"))

from portal.server.app import ConsoleApplication, build_app
from portal.server.config_flags import TASK_BOARD_SURFACE, surface_enabled
from portal.server.task_board import SCHEMA, TaskBoardSurface

from engine.multiagent.model import (
    FanOutPlan,
    HierarchyConfig,
    Lane,
    LaneRole,
    Mission,
    Subtask,
    ok_result,
)
from engine.multiagent.planner import HierarchicalPlanner
from engine.multiagent.runner import ScriptedRunner

TENANT = "acme"
TICKET_ID = "TCK-642"
MISSION_ID = "mission-tck-642"
OBJECTIVE = "restore the nightly reconciliation feed"

PLANNER_LANE = Lane(lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",))
SPECIALIST_LANES = (
    Lane(lane_id="analyst", role=LaneRole.SPECIALIST, agent_ids=("analyst-1",)),
    Lane(lane_id="reviewer", role=LaneRole.SPECIALIST, agent_ids=("reviewer-1",)),
)


def _world(*, durable_path: Path | None = None) -> Tuple[Any, Any]:
    """The real engine + ticket runtime (in-memory or file-backed)."""
    from core.events import FileJsonlEventStore, InMemoryEventStore
    from core.namespaces import NamespaceRegistry
    from core.runtime import Engine
    from core.tickets import TicketRuntime, register_ticket_handlers

    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    store = (
        FileJsonlEventStore(str(durable_path))
        if durable_path is not None
        else InMemoryEventStore()
    )
    engine = Engine(store=store, namespaces=registry)
    runner = (
        ScriptedRunner()
        .on("analyst-1", "t1", ok_result("analyst-1", "t1", output={"cause": "stale"}))
        .on("reviewer-1", "t2", ok_result("reviewer-1", "t2", output={"totals": "ok"}))
    )
    planner = HierarchicalPlanner(
        runner,
        planner_lane=PLANNER_LANE,
        specialist_lanes=SPECIALIST_LANES,
        config=HierarchyConfig(max_escalation_rounds=2),
    )
    register_ticket_handlers(engine, decomposer=planner, max_escalation_rounds=2)
    return engine, TicketRuntime(engine, tenant=TENANT)


def _work_plan(ctx: Any) -> FanOutPlan:
    return FanOutPlan(
        mission_id=ctx.mission.mission_id,
        planner_lane_id="planner",
        subtasks=(
            Subtask(
                subtask_id="t1",
                objective="diagnose the feed failure",
                lane_id="analyst",
                agent_id="analyst-1",
            ),
            Subtask(
                subtask_id="t2",
                objective="verify reconciliation totals",
                lane_id="reviewer",
                agent_id="reviewer-1",
            ),
        ),
    )


def _drive_to_closed(tickets: Any, *, ticket_id: str = TICKET_ID) -> Any:
    from core.tickets import ticket_workflow

    spec = ticket_workflow(
        tenant=TENANT,
        ticket_id=ticket_id,
        mission_id=MISSION_ID,
        objective=OBJECTIVE,
        decomposer=_work_plan,
        dispatch_lane="analyst",
        review={"reviewed_by": "ops-lead", "approved": True, "rationale": "ok"},
    )
    return tickets.run(ticket_id, spec, title="reconciliation feed down")


def _surface(runtime: Any, *, ticket_ids=(), **kwargs) -> TaskBoardSurface:
    return TaskBoardSurface(
        repo_root=REPO_ROOT,
        runtime=runtime,
        ticket_ids=ticket_ids,
        tenant=TENANT,
        **kwargs,
    )


def _app(surface: TaskBoardSurface) -> ConsoleApplication:
    return build_app(sso=console_sso(), task_board_surface=surface)


def _authed(app: ConsoleApplication):
    return login_as(app, "root@platform.example.com", TENANT)


# --------------------------------------------------------------------------- #
# The flag gate (GR-5 reversal 2026-09-21: a new surface ships ON by default)
# --------------------------------------------------------------------------- #
def test_config_declares_the_board_on():
    document = yaml.safe_load(
        (REPO_ROOT / "portal" / "config" / "feature-flags.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = document["surfaces"][TASK_BOARD_SURFACE]
    # policy-gr5-enabled-by-default (2026-09-21): this test hardcoded the OLD
    # off-by-default policy; updated to assert the new correct default,
    # matching infra/feature-flags/registry.yaml's surfaces.task_board entry.
    assert entry["default"] in (True, "on"), (
        "the tenant task board must ship ON (GR-5 reversal)"
    )
    assert surface_enabled(REPO_ROOT, surface=TASK_BOARD_SURFACE) is True


def test_surface_is_refused_while_the_flag_is_off():
    app = _app(_surface(runtime=None, ticket_ids=(), enabled=False))
    api = _authed(app)
    status, payload = api.get("/api/taskboard/tickets")
    assert status == 404
    assert payload["error"]["code"] == "feature_disabled"
    assert "portal/config/feature-flags.yaml" in payload["error"]["message"]
    anonymous = _authed(app)
    anonymous.cookies.clear()
    status, _ = anonymous.get("/api/taskboard/tickets")
    assert status == 404


def test_the_default_app_is_visible_flag_is_on_by_default():
    """policy-gr5-enabled-by-default (2026-09-21): the default app (config
    decides) is no longer feature-flag-gated OFF, matching
    infra/feature-flags/registry.yaml. (No runtime is wired into the bare
    default app, so the route answers 503 rather than 200 — but it is no
    longer the flag's 404 feature_disabled.)"""
    app = build_app(sso=console_sso())
    api = _authed(app)
    status, payload = api.get("/api/taskboard/tickets")
    # 503, not the flag's 404 feature_disabled: no runtime is wired into the
    # bare default app, so the route fails for a different, unrelated reason.
    assert status == 503, status
    assert payload["error"]["code"] != "feature_disabled"


def test_the_board_renders_once_its_flag_is_flipped_on():
    engine, tickets = _world()
    _drive_to_closed(tickets)
    app = _app(_surface(tickets, enabled=True, ticket_ids=(TICKET_ID,)))

    status, payload = _authed(app).get("/api/taskboard/tickets")
    assert status == 200
    served = payload["data"]
    assert served["schema"] == SCHEMA
    assert served["tenant"] == TENANT
    assert [row["ticketId"] for row in served["tickets"]] == [TICKET_ID]


# --------------------------------------------------------------------------- #
# The board is a replay of the engine's log (no second store)
# --------------------------------------------------------------------------- #
def test_a_closed_ticket_shows_every_lifecycle_state():
    engine, tickets = _world()
    _drive_to_closed(tickets)
    app = _app(_surface(tickets, enabled=True, ticket_ids=(TICKET_ID,)))

    _status, payload = _authed(app).get("/api/taskboard/tickets")
    row = payload["data"]["tickets"][0]
    assert row["state"] == "closed"
    assert row["closed"] is True
    assert row["lifecycle"] == [
        "created",
        "decomposed",
        "dispatched",
        "executed",
        "reviewed",
        "closed",
    ]
    assert row["decomposition"]["subtask_count"] == 2
    # Workbook-3 semantics, served faithfully. The ticket title is passed as a
    # workflow *input* but the projection's builder never reads it back out of
    # an event, so a replayed ticket carries ``title: ""``; and the review
    # object is only materialized when the gate closed on a non-approved verdict
    # (``gate_open: false``), so an approved-and-closed ticket carries no review
    # and the derived ``approved`` accessor is False. The board reports the
    # projection's own values rather than inventing a title or a verdict the log
    # does not hold.
    assert row["title"] == ""
    assert row["review"] is None
    assert row["approved"] is False
    assert row["outcomes"] == []


def test_the_served_vocabulary_is_the_models_own():
    engine, tickets = _world()
    app = _app(_surface(tickets, enabled=True, ticket_ids=()))

    from core.tickets import legal_moves, lifecycle_order, TicketState

    _status, payload = _authed(app).get("/api/taskboard/tickets")
    assert payload["data"]["lifecycle"] == [
        state.value for state in lifecycle_order()
    ]

    for state in lifecycle_order():
        _status, payload = _authed(app).get(f"/api/taskboard/moves/{state.value}")
        assert payload["data"]["moves"] == [
            nxt.value for nxt in legal_moves(state)
        ], f"the board diverged from the model at {state.value}"
    assert legal_moves(TicketState.CLOSED) == ()


def test_the_board_is_a_replay_so_a_persisted_log_serves_identically(
    tmp_path: Path,
):
    """A restarted process reads the same ticket — the board holds no state."""
    log = tmp_path / "events.jsonl"
    engine, tickets = _world(durable_path=log)
    _drive_to_closed(tickets)

    # A *second* engine over the same file-backed store, with no shared memory.
    engine2, tickets2 = _world(durable_path=log)
    app = _app(_surface(tickets2, enabled=True, ticket_ids=(TICKET_ID,)))
    _status, payload = _authed(app).get("/api/taskboard/tickets")

    row = payload["data"]["tickets"][0]
    assert row["state"] == "closed"
    assert row["lifecycle"][-1] == "closed"


def test_an_unknown_ticket_is_absent_never_invented():
    """An empty log is not a ticket: the board must not project one."""
    _engine, tickets = _world()
    app = _app(_surface(tickets, enabled=True, ticket_ids=("TCK-NEVER",)))

    status, payload = _authed(app).get("/api/taskboard/tickets/TCK-NEVER")
    assert status == 404
    assert payload["error"]["code"] == "not_found"
    assert "event store" in payload["error"]["message"]

    # the board itself still renders, and *reports* the stale id rather than
    # silently dropping it or failing the whole read.
    status, payload = _authed(app).get("/api/taskboard/tickets")
    assert status == 200
    assert payload["data"]["tickets"] == []
    assert payload["data"]["absent"] == ["TCK-NEVER"]


def test_an_empty_board_is_valid_not_broken():
    _engine, tickets = _world()
    app = _app(_surface(tickets, enabled=True, ticket_ids=()))
    status, payload = _authed(app).get("/api/taskboard/tickets")
    assert status == 200
    assert payload["data"]["tickets"] == []
    assert payload["data"]["absent"] == []


def test_a_ticket_not_on_the_board_is_refused():
    """A listed board does not serve an id it was never asked to hold."""
    engine, tickets = _world()
    app = _app(_surface(tickets, enabled=True, ticket_ids=(TICKET_ID,)))
    status, payload = _authed(app).get("/api/taskboard/tickets/OTHER-TCK")
    assert status == 404
    assert payload["error"]["code"] == "not_found"


def test_the_board_is_bound_to_its_tenant():
    """Another tenant's board is refused, never served the wrong rows."""
    engine, tickets = _world()
    app = _app(_surface(tickets, enabled=True, ticket_ids=(TICKET_ID,)))
    status, payload = _authed(app).get(
        "/api/taskboard/tickets", query={"tenant": "othercorp"}
    )
    assert status == 403
    assert payload["error"]["code"] == "tenant_mismatch"


def test_an_unwired_board_reports_unavailable_not_empty():
    """No runtime is a stated 503 — never an empty board that reads as 'no work'."""
    surface = TaskBoardSurface(repo_root=REPO_ROOT, enabled=True, tenant=TENANT)
    app = _app(surface)
    status, payload = _authed(app).get("/api/taskboard/tickets")
    assert status == 503
    assert payload["error"]["code"] == "board_unavailable"


# --------------------------------------------------------------------------- #
# Transport contract
# --------------------------------------------------------------------------- #
def test_an_unknown_state_is_a_400_and_bad_methods_are_refused():
    _engine, tickets = _world()
    app = _app(_surface(tickets, enabled=True))
    api = _authed(app)

    status, payload = api.get("/api/taskboard/moves/teleported")
    assert status == 400
    assert payload["error"]["code"] == "invalid_state"

    status, payload = api.post("/api/taskboard/tickets", body={})
    assert status == 405
    assert payload["error"]["code"] == "method_not_allowed"

    status, _ = api.get("/api/taskboard/nope")
    assert status == 404


def test_the_board_requires_a_console_session():
    _engine, tickets = _world()
    app = _app(_surface(tickets, enabled=True))
    api = login_as(app, "nobody@example.com", TENANT)
    api.cookies.clear()
    status, payload = api.get("/api/taskboard/tickets")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"
