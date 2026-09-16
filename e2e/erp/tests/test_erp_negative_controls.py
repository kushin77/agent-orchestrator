"""The ERP refusals, each driven through the module that owns it (ERP-10, issue #655).

Two halves, and the second is the one that keeps the first from being a formality:

* **each control refuses, by name** — the flag OFF, a cross-tenant read, a
  budget-exhausted tenant, and the six sibling lanes' own drivers composed over one
  tree;
* **each control could have failed** — for every refusal asserted above, the *same*
  measurement is shown to come out the other way when the one thing under test changes:
  the promoted declaration serves the routes, the same read for the owning tenant is
  allowed, and a tenant with room in its budget is not stopped. A control that cannot
  fail proves nothing about the rule it claims to exercise, which is why each refusal
  is paired here rather than declared once.
"""

from __future__ import annotations

from e2e.erp.gate import DEFAULT_TENANT
from e2e.erp.golden_path import CYCLE_FAMILIES
from e2e.erp.negative_controls import (
    CODE_BUDGET_EXHAUSTED,
    CODE_CROSS_TENANT,
    CODE_FEATURE_DISABLED,
    FOREIGN_TENANT,
    MODULE_DOCUMENT,
    MODULE_ROUTES,
)


def test_every_control_passed(controls):
    assert controls["failedControls"] == [], controls["failedControls"]
    assert controls["passed"] is True


def test_the_flag_off_refusal_is_the_shipped_declaration(control_by_id):
    """AC2a: with the flag OFF the module is refused, before AuthN."""
    control = control_by_id["module-flag-off-refused"]
    evidence = control["evidence"]
    assert control["refusedBy"] == CODE_FEATURE_DISABLED
    assert evidence["shippedDefault"] == "off"
    assert set(evidence["failClosed"].values()) == {"off"}
    for path, row in evidence["refusals"].items():
        assert row["anonymousStatus"] == 404 and row["anonymousCode"] == CODE_FEATURE_DISABLED, path
        assert row["sessionStatus"] == 404 and row["sessionCode"] == CODE_FEATURE_DISABLED, path
    assert set(evidence["refusals"]) == set(MODULE_ROUTES) | {MODULE_DOCUMENT}


def test_the_flag_control_could_have_failed(control_by_id):
    """The pairing: one promoted declaration away, the same routes are served."""
    promoted = control_by_id["module-flag-off-refused"]["evidence"]["promoted"]
    assert set(promoted) == set(MODULE_ROUTES) | {MODULE_DOCUMENT}
    for path, row in promoted.items():
        assert row["status"] == 200, (path, row)
        assert row["code"] != CODE_FEATURE_DISABLED, path


def test_a_cross_tenant_read_is_denied_by_name(control_by_id):
    """AC2b: a cross-tenant read is denied, with ERP-08's own reason."""
    control = control_by_id["cross-tenant-read-denied"]
    evidence = control["evidence"]
    assert control["refusedBy"] == CODE_CROSS_TENANT
    assert evidence["foreignDecision"]["allowed"] is False
    assert evidence["foreignDecision"]["reason"] == CODE_CROSS_TENANT
    assert evidence["foreignTenant"] == FOREIGN_TENANT
    assert evidence["siblingDriver"]["uncovered"] == []
    assert evidence["siblingDriver"]["provoked"] == evidence["siblingDriver"]["declared"]


def test_the_cross_tenant_control_could_have_failed(control_by_id, cycle):
    """The pairing, driven again here: the owning tenant's identical read is allowed."""
    evidence = control_by_id["cross-tenant-read-denied"]["evidence"]
    assert evidence["owningDecision"]["allowed"] is True
    # Re-driven rather than read back: the same principal, the same kind, the same
    # fields — only the tenant in the request differs, and only the answer differs.
    from integrations.erp.auth import platform_fixture as fixture
    from integrations.erp.auth import policies as auth_policies
    from integrations.erp.auth import roles as auth_roles
    from integrations.erp.auth.model import Principal, Request
    from integrations.erp.auth.scope import authorize

    role_map = auth_roles.load_default()
    policy_set = auth_policies.load_default(kinds=role_map.kinds)
    permissions = sorted(
        {role_map.permission_for(kind, action) for kind in CYCLE_FAMILIES for action in ("read", "write")}
    )
    store, _node = fixture.build(tenant=DEFAULT_TENANT, permissions=permissions)
    principal = Principal(
        tenant=DEFAULT_TENANT, subject=fixture.DEFAULT_SUBJECT, roles=tuple(role_map.role_names)
    )
    invoice = cycle.hop_documents()[-1]
    fields = {"id": invoice.id, "total": invoice.body.get("total")}
    owning = authorize(
        role_map,
        policy_set,
        store,
        principal,
        Request(
            tenant=DEFAULT_TENANT,
            kind=invoice.kind,
            action="read",
            team=fixture.DEFAULT_TEAM,
            fields=fields,
        ),
    )
    foreign = authorize(
        role_map,
        policy_set,
        store,
        principal,
        Request(
            tenant=FOREIGN_TENANT,
            kind=invoice.kind,
            action="read",
            team=fixture.DEFAULT_TEAM,
            fields=fields,
        ),
    )
    assert owning.allowed is True and foreign.allowed is False
    assert foreign.reason == CODE_CROSS_TENANT


def test_a_budget_exhausted_tenant_is_stopped_by_name(control_by_id):
    """AC2c: a tenant over its limit is stopped, and the operation reaches nothing."""
    control = control_by_id["budget-exhausted-stopped"]
    evidence = control["evidence"]
    assert control["refusedBy"] == CODE_BUDGET_EXHAUSTED
    assert evidence["refusal"]["code"] == CODE_BUDGET_EXHAUSTED
    assert DEFAULT_TENANT in evidence["refusal"]["detail"]
    assert evidence["allowedOperations"] >= 1
    assert evidence["ledgerRecords"] == evidence["allowedOperations"]
    assert evidence["siblingDriver"]["uncovered"] == []


def test_the_budget_control_could_have_failed():
    """The pairing: the same operations, a tenant with room, no stop at all."""
    from integrations.erp.finops import harness

    tenant = DEFAULT_TENANT
    workspace = harness.build_workspace(policies=harness.policies_from({tenant: 1000.0}))
    for index in range(32):
        workspace.meter.create(
            "sales-order",
            tenant=tenant,
            document_id=f"SO-ROOM-{index:04d}",
            actor="agent:erp-e2e",
            at=harness.stamp(index),
        )
    assert workspace.audit.count(tenant) == 32
    assert workspace.audit.verify(tenant).status == "OK"


def test_the_six_sibling_lanes_are_composed(control_by_id):
    """Every sibling lane's own refusals hold at once, over one tree."""
    control = control_by_id["sibling-refusals-composed"]
    lanes = control["evidence"]["lanes"]
    assert set(lanes) == {"crm", "tx", "auth", "finops", "api", "ops"}
    for lane, row in lanes.items():
        assert row["declared"] > 0, lane
        assert row["reported"] is True, lane
        assert row["unreportedCodes"] == [], lane
    assert control["evidence"]["declaredTotal"] == sum(row["declared"] for row in lanes.values())


def test_the_composed_counts_are_the_modules_own_vocabularies(control_by_id):
    """The counts come from the modules, so a lane that adds a refusal moves them."""
    from integrations.erp.auth.model import REFUSALS as AUTH_REFUSALS
    from integrations.erp.crm.model import REFUSALS as CRM_REFUSALS
    from integrations.erp.finops.model import REFUSALS as FINOPS_REFUSALS
    from integrations.erp.ops.model import REFUSALS as OPS_REFUSALS
    from integrations.erp.tx.model import REFUSALS as TX_REFUSALS

    lanes = control_by_id["sibling-refusals-composed"]["evidence"]["lanes"]
    assert lanes["crm"]["declared"] == len(CRM_REFUSALS)
    assert lanes["tx"]["declared"] == len(TX_REFUSALS)
    assert lanes["auth"]["declared"] == len(AUTH_REFUSALS)
    assert lanes["finops"]["declared"] == len(FINOPS_REFUSALS)
    assert lanes["ops"]["declared"] == len(OPS_REFUSALS)
    assert lanes["auth"]["provoked"] == len(AUTH_REFUSALS)
    assert lanes["api"]["declared"] > 0
    assert "refused by name" in lanes["api"]["lastLine"]
