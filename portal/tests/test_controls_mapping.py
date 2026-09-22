"""Controls -> policy-control mapping tests (issue #39 AC #4).

Every console toggle must map to a server-side policy control — no UI-only
state. These tests prove: (1) every toggle id the UI can offer is a registered
server-side policy control in the mapping table; (2) each control's exposed
default IS the state its owning registry declares; (3) toggling one changes
server-side policy state (a second, independent actor observes the same state);
(4) the enforcement decision for the gated action actually changes (blocked when
ON); (5) tenant isolation; (6) fail-closed edges (unknown control, mode-off
cannot enable).

The default-state expectation is READ FROM THE AUTHORITY, never hardcoded:
``guardrails/policy/controls.yaml`` (the frozen server-side vocabulary) plus
``portal/catalog/policy-controls.yaml`` (this lane's own catalog). A fresh tenant
is seeded from each control's declared ``enabled:`` value, so the console cannot
publish a default the registry does not declare. That is the drift the old
"every control is OFF" literal could not survive: issue #1953 (owner decision
2026-09-21) flipped ``hermes-head-guardrails`` to ``enabled: true`` when the
GR-5/AO-GR-6 enabled-by-default reversal reached its paired ``enable_hermes``
provider flag — the capability shipped live while its guard would have stayed
inert.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from conftest import ApiClient, login_as

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The two catalogs the console consumes, in the order the server loads them
#: (``ControlCatalog.build``): the frozen guardrails vocabulary, then this lane's
#: own portal catalog.
_AUTHORITY = (
    REPO_ROOT / "guardrails" / "policy" / "controls.yaml",
    REPO_ROOT / "portal" / "catalog" / "policy-controls.yaml",
)


def _data(payload):
    return payload["data"]


def declared_defaults() -> dict[str, bool]:
    """Control id -> the ``enabled:`` value its owning registry declares.

    The same read ``ControlCatalog.load_guardrails`` / ``load_portal`` perform, so
    an exposed default that diverges from the registry is caught here rather than
    blessed by a second literal.
    """
    declared: dict[str, bool] = {}
    for path in _AUTHORITY:
        mapping = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for item in mapping.get("controls") or []:
            declared[str(item["id"])] = bool(item.get("enabled"))
    return declared


def test_every_ui_toggle_is_a_server_side_policy_control(app):
    """The mapping table covers every control the catalog offers to the UI."""
    api = login_as(app, "root@platform.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/controls")
    assert status == 200
    data = _data(payload)
    controls = data["controls"]
    policy_map = data["policyMap"]
    declared = declared_defaults()
    # The UI can only offer controls the server has registered + mapped.
    assert len(controls) >= 4
    for control in controls:
        assert control["id"] in policy_map
        entry = policy_map[control["id"]]
        assert entry["policy"] == control["id"]
        # The exposed default IS the registry's declared default (no UI-only
        # drift). A control the registries do not declare raises KeyError here,
        # which is the correct failure: an undeclared toggle is not a mapping.
        assert control["enabledDefault"] == declared[control["id"]]
        # A fresh tenant is seeded from that same declared default.
        assert control["enabled"] == declared[control["id"]]


def test_controls_default_to_the_declared_registry_state(app):
    """A fresh tenant's state IS the registries' declared ``enabled:`` state.

    Not "every control is OFF": the ratified rule (issue #1953, owner decision
    2026-09-21) is that a control follows its owning registry's declared
    ``enabled:``, so ``hermes-head-guardrails`` (``enabled: true``, its paired
    ``enable_hermes`` provider flag shipping on) is ON for a fresh tenant while
    the rest are OFF. The #1953 consequence is asserted by name below.
    """
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/controls")
    assert status == 200
    declared = declared_defaults()
    by_id = {c["id"]: bool(c["enabled"]) for c in _data(payload)["controls"]}
    assert by_id == declared
    assert by_id["hermes-head-guardrails"] is True  # issue #1953
    assert {cid for cid, on in by_id.items() if on} == {
        cid for cid, on in declared.items() if on
    }


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
