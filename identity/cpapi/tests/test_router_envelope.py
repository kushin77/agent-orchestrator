"""Router + envelope tests: matching, path params, and envelope shape."""

import pytest

from cpapi.fakes import build_test_app
from cpapi.router import Route, Router, compile_template


# --- pure router ---------------------------------------------------------------


def test_compile_template_captures_path_params():
    pattern, names = compile_template("/v1/agents/{agentId}/tasks")
    assert names == ("agentId",)
    import re

    match = re.match(pattern, "/v1/agents/worker-1/tasks")
    assert match is not None
    assert match.group(1) == "worker-1"


def test_router_method_and_exact_match():
    router = Router().register_all(
        [
            Route("GET", "/v1/agents", "agent:read", "agents.list"),
            Route("GET", "/v1/agents/{agentId}", "agent:read", "agents.get"),
            Route("POST", "/v1/agents", "agent:create", "agents.register"),
        ]
    )
    hit = router.match("GET", "/v1/agents")
    assert hit is not None and hit.route.name == "agents.list"

    hit = router.match("GET", "/v1/agents/worker-1")
    assert hit is not None and hit.route.name == "agents.get"
    assert hit.params == {"agentId": "worker-1"}

    hit = router.match("POST", "/v1/agents")
    assert hit is not None and hit.route.name == "agents.register"

    # method mismatch on the same path shape must not alias.
    assert router.match("DELETE", "/v1/agents/worker-1") is None
    assert router.match("GET", "/v1/agents/worker-1/extra") is None


def test_route_table_is_declarative_and_complete():
    rig = build_test_app()
    names = rig.app.router.names()
    for expected in (
        "tenant.get",
        "tenant.pause",
        "tenant.resume",
        "budget.usage",
        "budget.quotas",
        "agents.list",
        "agents.register",
        "agents.get",
        "agents.activate",
        "agents.pause",
        "agents.retire",
        "agents.dispatch",
        "tasks.status",
        "profiles.list",
        "profiles.get",
        "personas.list",
        "personas.get",
        "prompts.list",
        "prompts.get",
        "policies.list",
        "policies.get",
        "audit.query",
        "outbox.events",
        "outbox.poll",
        "outbox.ack",
        "outbox.fail",
        "approvals.list",
        "approvals.approve",
        "approvals.deny",
    ):
        assert expected in names, f"missing route {expected}"


# --- envelope shape --------------------------------------------------------------


def test_ok_envelope_shape_and_request_id():
    rig = build_test_app()
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/tenants/acme", principal=admin)
    assert envelope["ok"] is True
    assert envelope["status"] == 200
    assert envelope["data"] is not None
    assert envelope["error"] is None
    assert envelope["requestId"].startswith("req_")


def test_error_envelope_has_machine_code():
    rig = build_test_app()
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/tenants/acme/missing", principal=admin)
    assert envelope["ok"] is False
    assert envelope["status"] == 404
    assert envelope["data"] is None
    assert envelope["error"]["code"] == "not_found"
    assert envelope["requestId"].startswith("req_")


def test_unknown_route_is_404():
    rig = build_test_app()
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("POST", "/v1/does-not-exist", principal=admin, body={})
    assert envelope["status"] == 404
    assert envelope["error"]["code"] == "not_found"


def test_validation_error_is_400_with_code():
    rig = build_test_app()
    admin = rig.principal("acme", "u_admin")
    # agentId missing -> model validation raises 400 validation_error.
    envelope = rig.app.handle("POST", "/v1/agents", principal=admin, body={"profileRef": "coder"})
    assert envelope["status"] == 400
    assert envelope["error"]["code"] == "validation_error"
    assert "agentId" in envelope["error"]["details"]["field"]


def test_non_object_body_is_400():
    rig = build_test_app()
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("POST", "/v1/agents", principal=admin, body=None)
    assert envelope["status"] == 400
    assert envelope["error"]["code"] == "invalid_body"


def test_unhandled_exception_becomes_envelope_500():
    rig = build_test_app()
    admin = rig.principal("acme", "u_admin")

    def boom(params, data, principal):
        raise RuntimeError("boom")

    rig.app.router.register(Route("POST", "/v1/boom", "agent:read", "boom.route"))
    rig.app._on_boom_route = boom  # type: ignore[attr-defined]
    envelope = rig.app.handle("POST", "/v1/boom", principal=admin, body={})
    assert envelope["status"] == 500
    assert envelope["error"]["code"] == "internal_error"
    assert envelope["error"]["details"]["kind"] == "RuntimeError"
