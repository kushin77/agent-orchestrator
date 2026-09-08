"""Task-route and capability resolution tests (issue #10).

Resolution fails closed: an unknown task type is denied (never routed, never
falling back to other task types or other tenants), a route may only name
agents registered in the same tenant, and capability lookup never crosses a
tenant boundary.
"""

from __future__ import annotations

import pytest

from service import (
    NoRoutableAgentError,
    RouteAgentNotInTenantError,
    RoutingError,
    UnknownCapabilityError,
    UnknownTaskTypeError,
    AgentRegistry,
)


@pytest.fixture()
def reg():
    registry = AgentRegistry()
    # tenant acme
    registry.register("acme", "coder-1", "coder")
    registry.register("acme", "reviewer-1", "reviewer")
    registry.register("acme", "researcher-1", "researcher")
    # a separate tenant with a distinct coder and one acme-unseen agent
    registry.register("globex", "coder-1", "coder")
    registry.register("globex", "researcher-x", "researcher")
    for agent in ("coder-1", "reviewer-1", "researcher-1"):
        registry.activate("acme", agent)
    registry.activate("globex", "coder-1")
    registry.activate("globex", "researcher-x")
    return registry


# --------------------------------------------------------------------- #
# route administration
# --------------------------------------------------------------------- #
def test_set_task_route_and_list(reg):
    route = reg.set_task_route(
        "acme", "code-review", ["reviewer-1"], required_capabilities=["code-review"]
    )
    assert route.task_type == "code-review"
    assert route.agent_ids == ("reviewer-1",)
    assert route.required_capabilities == ("code-review",)
    routes = reg.list_routes("acme")
    assert [r.task_type for r in routes] == ["code-review"]


def test_route_referencing_agent_of_other_tenant_is_refused(reg):
    with pytest.raises(RouteAgentNotInTenantError):
        # researcher-x exists in globex but not in acme; an acme route may not name it
        reg.set_task_route("acme", "research", ["researcher-x"])


def test_route_referencing_unknown_agent_is_refused(reg):
    with pytest.raises(RouteAgentNotInTenantError):
        reg.set_task_route("acme", "code-author", ["ghost"])


def test_route_with_unknown_capability_is_refused(reg):
    with pytest.raises(UnknownCapabilityError):
        reg.set_task_route(
            "acme",
            "code-author",
            ["coder-1"],
            required_capabilities=["not-a-real-capability"],
        )


def test_route_candidate_must_hold_required_capability(reg):
    # reviewer-1 does not have code-author, so this route must be refused
    with pytest.raises(RoutingError):
        reg.set_task_route(
            "acme",
            "code-author",
            ["reviewer-1"],
            required_capabilities=["code-author"],
        )


def test_route_with_empty_candidates_is_refused(reg):
    with pytest.raises(RoutingError):
        reg.set_task_route("acme", "code-author", [])


def test_route_event_is_appended(reg):
    reg.set_task_route("acme", "code-author", ["coder-1"])
    events = reg.events.events()
    route_events = [e for e in events if e["event"] == "route"]
    assert len(route_events) == 1
    assert route_events[0]["tenantId"] == "acme"
    assert route_events[0]["detail"]["taskType"] == "code-author"
    reg.events.verify()


# --------------------------------------------------------------------- #
# task resolution (fail closed)
# --------------------------------------------------------------------- #
def test_resolve_task_returns_active_candidates_in_route_order(reg):
    reg.set_task_route("acme", "code-author", ["coder-1"])
    resolution = reg.resolve_task("acme", "code-author")
    assert resolution.resolved
    assert resolution.agent_ids == ("coder-1",)
    assert resolution.candidates[0].status == "active"


def test_resolve_unknown_task_type_is_denied(reg):
    # no route exists for this task type -> fail closed, never fall back
    with pytest.raises(UnknownTaskTypeError):
        reg.resolve_task("acme", "refactor-anything")


def test_resolve_skips_paused_and_retired_candidates(reg):
    reg.set_task_route("acme", "code-review", ["reviewer-1", "researcher-1"])
    reg.pause("acme", "reviewer-1")
    resolution = reg.resolve_task("acme", "code-review")
    # reviewer-1 is paused -> skipped, no substitution from another tenant
    assert resolution.resolved
    assert resolution.agent_ids == ("researcher-1",)


def test_resolve_no_active_candidates_denies_without_fallback(reg):
    reg.set_task_route("acme", "research", ["researcher-1"])
    reg.pause("acme", "researcher-1")
    resolution = reg.resolve_task("acme", "research")
    assert not resolution.resolved
    assert resolution.agent_ids == ()
    # globex coder-1 is active and could research-adjacent, but is out of tenant


def test_resolve_required_raises_when_no_qualified_candidate(reg):
    # reviewer-1 holds audit; once paused there is no qualified active candidate
    reg.set_task_route(
        "acme",
        "audit-run",
        ["reviewer-1"],
        required_capabilities=["audit"],
    )
    reg.pause("acme", "reviewer-1")
    with pytest.raises(NoRoutableAgentError):
        reg.resolve_required("acme", "audit-run")


# --------------------------------------------------------------------- #
# capability resolution
# --------------------------------------------------------------------- #
def test_resolve_by_capability_returns_active_holders_in_tenant(reg):
    holders = reg.resolve_by_capability("acme", "code-review")
    assert [a.agent_id for a in holders] == ["reviewer-1"]


def test_resolve_by_capability_is_tenant_scoped(reg):
    # globex coder-1 also has code-author, but is invisible to acme lookups
    acme_holders = reg.resolve_by_capability("acme", "code-author")
    globex_holders = reg.resolve_by_capability("globex", "code-author")
    assert [a.agent_id for a in acme_holders] == ["coder-1"]
    assert [a.agent_id for a in globex_holders] == ["coder-1"]
    assert acme_holders[0].tenant_id == "acme"
    assert globex_holders[0].tenant_id == "globex"


def test_resolve_by_capability_excludes_paused_and_retired(reg):
    reg.pause("acme", "reviewer-1")
    assert reg.resolve_by_capability("acme", "code-review") == []
    reg.activate("acme", "reviewer-1")
    reg.retire("acme", "reviewer-1")
    assert reg.resolve_by_capability("acme", "code-review") == []


def test_resolve_by_unknown_capability_is_refused(reg):
    with pytest.raises(UnknownCapabilityError):
        reg.resolve_by_capability("acme", "time-travel")


def test_route_then_capability_consistency(reg):
    # a route with required capabilities still only surfaces qualified agents
    reg.set_task_route(
        "acme",
        "quality-gate",
        ["reviewer-1"],
        required_capabilities=["audit", "test-run"],
    )
    resolution = reg.resolve_task("acme", "quality-gate")
    assert resolution.resolved
    assert resolution.agent_ids == ("reviewer-1",)


def test_capacity_report(reg):
    report = reg.router().capacity_report("acme")
    assert report["tenantId"] == "acme"
    assert report["byStatus"]["active"] == 3
    assert report["total"] == 3
