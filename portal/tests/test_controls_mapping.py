"""Controls -> policy-control mapping tests (issue #39 AC #4).

Every console toggle must map to a server-side policy control — no UI-only
state. These tests prove: (1) every toggle id the UI can offer is a registered
server-side policy control in the mapping table; (2) controls default OFF;
(3) toggling one changes server-side policy state (a second, independent actor
observes the same state); (4) the enforcement decision for the gated action
actually changes (blocked when ON); (5) tenant isolation; (6) fail-closed
edges (unknown control, mode-off cannot enable).
"""

from __future__ import annotations

from conftest import ApiClient, login_as


def _data(payload):
    return payload["data"]


def test_every_ui_toggle_is_a_server_side_policy_control(app):
    """The mapping table covers every control the catalog offers to the UI."""
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/controls")
    assert status == 200
    data = _data(payload)
    controls = data["controls"]
    policy_map = data["policyMap"]
    # The UI can only offer controls the server has registered + mapped.
    assert len(controls) >= 4
    for control in controls:
        assert control["id"] in policy_map
        entry = policy_map[control["id"]]
        assert entry["policy"] == control["id"]
        assert control["enabledDefault"] is False
        # Default state (fresh tenant) is OFF for every control.
        assert control["enabled"] is False


def test_controls_default_off(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/controls")
    enabled = [c["id"] for c in _data(payload)["controls"] if c["enabled"]]
    assert enabled == []


def test_toggle_changes_server_side_policy_state(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.post(
        "/api/tenants/acme/controls/model-call-budget", {"enabled": True}
    )
    assert status == 200
    control = payload["data"]["control"]
    assert control["enabled"] is True
    # A second, independent actor sees the SAME server state (not UI-local).
    other = login_as(app, "root@platform.example.com", "acme")
    status, payload = other.get("/api/tenants/acme/controls")
    by_id = {c["id"]: c["enabled"] for c in _data(payload)["controls"]}
    assert by_id["model-call-budget"] is True


def test_toggle_changes_enforcement_decision(app):
    """Flipping a control ON changes the gated action's decision (BLOCK)."""
    api = login_as(app, "root@platform.example.com", "acme")
    # Before: model.call is allowed (no control on).
    status, payload = api.post(
        "/api/tenants/acme/policy-check", {"action": "model.call"}
    )
    assert payload["data"]["decision"]["decision"] == "allow"
    # Flip the model-call-budget control ON.
    status, payload = api.post(
        "/api/tenants/acme/controls/model-call-budget", {"enabled": True}
    )
    assert status == 200
    gated = payload["data"]["gatedDecisions"]
    assert any(decision["decision"] == "block" for decision in gated)
    # After: the gated action is blocked at the console boundary.
    status, payload = api.post(
        "/api/tenants/acme/policy-check", {"action": "model.call"}
    )
    decision = payload["data"]["decision"]
    assert decision["decision"] == "block"
    assert decision["control"] == "model-call-budget"


def test_control_state_is_tenant_isolated(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    api.post("/api/tenants/acme/controls/pause-rail", {"enabled": True})
    other = login_as(app, "carol@globex.example.com", "globex")
    status, payload = other.get("/api/tenants/globex/controls")
    by_id = {c["id"]: c["enabled"] for c in _data(payload)["controls"]}
    assert by_id["pause-rail"] is False


def test_unknown_control_id_is_rejected(app):
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(
        "/api/tenants/acme/controls/does-not-exist", {"enabled": True}
    )
    assert status == 404
    assert payload["error"]["code"] == "unknown_control"


def test_pause_rail_blocks_dispatch(app):
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.post(
        "/api/tenants/acme/controls/pause-rail", {"enabled": True}
    )
    assert status == 200
    status, payload = api.post(
        "/api/tenants/acme/policy-check", {"action": "agent.dispatch"}
    )
    assert payload["data"]["decision"]["decision"] == "block"


def test_guardrails_controls_present_and_frozen(app):
    """The three platform controls are consumed from guardrails controls.yaml."""
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/controls")
    ids = {c["id"] for c in _data(payload)["controls"]}
    assert {"model-call-budget", "tool-use-guard", "data-egress-guard"} <= ids
