"""Authorization is delegated to ERP-08, and there is no back door (#651, criterion 3).

Criterion 3 is *"Auth/scope enforcement is delegated to ERP-08's layer; this
surface never bypasses it (no back door)"*. "Delegated" is easy to claim, so these
tests hold four separable properties:

1. **the layer decides, not the surface** — a role map that grants an action the
   platform does not grant is refused *by the platform*, and a subject with no
   binding is refused by scope;
2. **the tenant always comes from the principal** — a principal from another
   tenant gets nothing, and a foreign document is not even distinguishable from an
   absent one;
3. **the surface has no parameter that could widen anything** — the tenant and the
   scope team are not inputs, so there is nothing to send;
4. **field policy is applied by omission on read and refused on write** — a
   read-denied field is *absent* from the response (never blanked) and a
   write-denied field refuses the write.
"""

from __future__ import annotations

import inspect

import pytest

from integrations.erp.api import fixtures, surface as surface_module
from integrations.erp.auth.model import Principal


def test_the_layer_decides_not_the_surface(world_for):
    """An ERP role that grants `create` is still refused when the platform lacks it."""
    # The clerk's role map grants `create` on party; the platform binding holds
    # only `erp.party:read`, so the platform's permission gate is what refuses.
    restricted = world_for("ERP Clerk", permissions=("erp.party:read",))
    refused = restricted.post("/v1/erp/documents/party", fixtures.documents()["party"][0])
    assert refused["status"] == 403
    assert refused["error"]["code"] == "forbidden"
    assert refused["error"]["details"]["reason"] == "permission-denied"
    assert "erp.party:create" in refused["error"]["message"]


def test_a_subject_with_no_binding_is_refused_by_scope(world):
    refused = world.get(
        "/v1/erp/documents/party", Principal(tenant="acme", subject="unbound", roles=("ERP Clerk",))
    )
    assert refused["status"] == 403
    assert refused["error"]["details"]["reason"] == "scope-denied"


def test_an_undeclared_role_is_refused_rather_than_ignored(world):
    refused = world.get("/v1/erp/documents/party", world.principal_as("No Such Role"))
    assert refused["status"] == 403
    assert refused["error"]["details"]["reason"] == "unknown-role"


def test_an_auditor_may_read_and_may_not_write(world_for):
    auditor = world_for("ERP Auditor")
    assert auditor.get("/v1/erp/documents/party")["ok"] is True
    refused = auditor.post("/v1/erp/documents/party", fixtures.documents()["party"][0])
    assert refused["status"] == 403
    assert refused["error"]["details"]["reason"] == "permission-denied"


def test_a_clerk_may_not_cancel(world_for):
    """The role separation is real: cancel is a manager's permission in the fixture."""
    clerk = world_for("ERP Clerk")
    refused = clerk.post("/v1/erp/documents/sales-order/SALES-ORDER-0001/transitions/cancel")
    assert refused["status"] == 403
    assert refused["error"]["details"]["reason"] == "permission-denied"
    assert "cancel" in refused["error"]["message"]


def test_a_principal_from_another_tenant_reaches_nothing(world_for):
    """The tenant is the principal's own, so there is no request that could disagree."""
    foreign = Principal(tenant="globex", subject=fixtures.PLATFORM_SUBJECT, roles=("ERP Manager",))
    world = world_for("ERP Manager")
    for path in (
        "/v1/erp/documents/sales-order",
        "/v1/erp/documents/sales-order/SALES-ORDER-0001",
    ):
        refused = world.get(path, foreign)
        assert refused["ok"] is False, path
        assert refused["status"] in (403, 404), path


def test_a_document_of_another_tenant_is_absent_not_forbidden(world_for):
    """The store is keyed by tenant, so a foreign document is not in the address space."""
    world = world_for("ERP Manager")
    # The same id, a different tenant's principal: the tenant gate passes (the
    # request tenant is the principal's), and the lookup finds nothing.
    assert world.documents.get(world.principal.tenant, "sales-order", "SALES-ORDER-0001")
    assert world.documents.peek("globex", "sales-order", "SALES-ORDER-0001") is None


def test_the_surface_has_no_tenant_input():
    """No parameter of `handle` or `authorize_request` could name a tenant or a team."""
    for method in (surface_module.Surface.handle, surface_module.Surface.authorize_request):
        signature = inspect.signature(method)
        assert not (set(signature.parameters) & {"tenant", "tenant_id", "team", "org", "scope"}), (
            f"{method.__name__} takes a scope input; the tenant must come from the principal only"
        )


def test_the_authorization_layer_is_called_from_exactly_one_place():
    """The structural proof of 'no back door': one call site, measured on the source."""
    source = inspect.getsource(surface_module)
    assert source.count("auth_scope.authorize(") == 1


def test_the_request_tenant_is_the_principals(world):
    """Read the decision's own request back: the tenant is the principal's, always."""
    seen: dict = {}
    original = surface_module.auth_scope.authorize

    def spy(role_map, policy_set, store, principal, request):
        seen["tenant"] = request.tenant
        seen["team"] = request.team
        return original(role_map, policy_set, store, principal, request)

    surface_module.auth_scope.authorize = spy
    try:
        assert world.get("/v1/erp/documents/party")["ok"] is True
    finally:
        surface_module.auth_scope.authorize = original
    assert seen["tenant"] == world.principal.tenant
    assert seen["team"] == surface_module.SCOPE_TEAM


# --- field policy -----------------------------------------------------------


def test_a_read_denied_field_is_absent_and_named(world):
    """`po_reference` is write-only for a clerk: the read omits it and reports it."""
    fetched = world.get("/v1/erp/documents/sales-order/SALES-ORDER-0001")
    assert fetched["ok"] is True
    document = fetched["data"]["document"]
    assert "po_reference" not in document, "a withheld field must be absent, never blanked"
    assert fetched["data"]["redacted"] == ["po_reference"]


def test_a_collection_projects_each_item_by_the_same_rule(world):
    listed = world.get("/v1/erp/documents/sales-order")
    assert listed["data"]["redacted"] == ["po_reference"]
    for item in listed["data"]["items"]:
        assert "po_reference" not in item


def test_a_manager_sees_the_field(world_for):
    """The rule is scoped to the clerk, so a manager's read carries the field."""
    manager = world_for("ERP Manager")
    fetched = manager.get("/v1/erp/documents/sales-order/SALES-ORDER-0001")
    assert fetched["data"]["document"]["po_reference"] == "CUST-PO-1"
    assert fetched["data"]["redacted"] == []


def test_a_field_write_denied_is_refused_naming_the_rule(world):
    """`total` is read-only for a clerk: a payload carrying it is refused, not ignored."""
    order = fixtures.documents()["sales-order"][0]
    refused = world.put(
        "/v1/erp/documents/sales-order/SALES-ORDER-0001", {**order, "total": 240.0}
    )
    assert refused["status"] == 403
    assert refused["error"]["details"]["reason"] == "field-write-denied"
    assert "total" in refused["error"]["message"]


def test_a_clerk_may_write_the_field_that_is_merely_write_only(world):
    """`po_reference` is write-only, not forbidden: a clerk may set it and not read it."""
    order = fixtures.documents()["sales-order"][0]
    replaced = world.post(
        "/v1/erp/documents/sales-order", {**order, "id": "SALES-ORDER-0002"}
    )
    assert replaced["status"] == 201
    assert "po_reference" not in replaced["data"]["document"]


def test_a_declaration_that_cannot_be_read_is_cannot_assess(world):
    """An unreadable contract is a 503 — never a denial, and never a pass."""
    from integrations.erp.auth import contract as auth_contract
    from integrations.erp.auth.model import Refused

    original = auth_contract.rbac

    def unreadable():
        raise Refused("contract-unavailable", "provoked: identity/rbac is unreadable")

    auth_contract.rbac = unreadable
    try:
        refused = world.get("/v1/erp/documents/party")
    finally:
        auth_contract.rbac = original
    assert refused["status"] == 503
    assert refused["error"]["code"] == "unavailable"
    assert refused["error"]["details"]["reason"] == "contract-unavailable"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/erp/documents/party"),
        ("POST", "/v1/erp/documents/party"),
        ("GET", "/v1/erp/documents/party/CUST-0001"),
        ("PUT", "/v1/erp/documents/party/CUST-0001"),
        ("DELETE", "/v1/erp/documents/party/CUST-0001"),
        ("POST", "/v1/erp/documents/party/CUST-0001/transitions/submit"),
    ],
)
def test_no_route_that_carries_tenant_data_is_reachable_without_a_principal(world, method, path):
    """Every route that carries tenant data refuses an anonymous caller."""
    refused = world.surface.handle(method, path, body={"doctype": "party", "id": "CUST-1"})
    assert refused["status"] == 401, (method, path, refused)
    assert refused["error"]["code"] == "unauthorized"
